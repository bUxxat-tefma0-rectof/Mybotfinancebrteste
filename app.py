import os
import re
import asyncio
import io
from datetime import datetime
from flask import Flask, request, Response
from telegram import Update, ForceReply
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from sqlalchemy.orm import sessionmaker
from models import engine, User, Transaction, Base, Session
import bcrypt
from matplotlib import pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Image, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

web_app = Flask(__name__)

# Estados para ConversationHandler
EMAIL, PASSWORD, CONFIRM_CREATE, ACTION = range(4)
CONFIRM_EXPENSE, ALTER_EXPENSE = range(2)  # Para financeiro

# Tokens de env
FINANCIAL_TOKEN = os.getenv('FINANCIAL_TOKEN')
REPORT_TOKEN = os.getenv('REPORT_TOKEN')
URL = os.getenv('RENDER_URL')  # Ex: https://seu-app.onrender.com

financial_application = None
report_application = None

# Função para parsear despesa (adaptada do seu exemplo)
def parse_expense(message):
    value_match = re.search(r'(\d+(?:,\d+)?)\s*reais', message)
    value_str = value_match.group(1).replace(',', '.') if value_match else '0'
    value = float(value_str)
    
    desc_match = re.search(r'de\s+(.+?)\s+no', message)
    description = desc_match.group(1).strip().capitalize() if desc_match else ''
    
    payment_match = re.search(r'no\s+(.+)', message)
    payment = payment_match.group(1).strip() if payment_match else ''
    
    categories = {
        'uber': 'Transporte',
        'unha': 'Gastos pessoais',
        # Adicione mais mapeamentos conforme necessário
    }
    category = categories.get(description.lower(), 'Outros')
    
    date = datetime.now().strftime('%d/%m/%Y')
    
    if 'cartão' in payment.lower():
        payment_method = 'Cartão de crédito'
        card = re.sub(r'cartão\s+', '', payment, flags=re.IGNORECASE).strip()
        status = 'pendente'
        conta_cartao = f'Cartão de crédito {card}'
    elif 'pix' in payment.lower():
        payment_method = 'Pix'
        card = re.sub(r'pix\s+', '', payment, flags=re.IGNORECASE).strip()
        status = 'executada'
        conta_cartao = f'{card}'
    else:
        payment_method = ''
        conta_cartao = ''
        status = ''
    
    return {
        'tipo': 'Despesa',
        'valor': value,
        'descricao': description,
        'categoria': category,
        'data_ocorrencia': date,
        'forma_pagamento': payment_method,
        'conta_cartao': conta_cartao,
        'parcelas': 'não aplicável',
        'status': status
    }

# Funções comuns de auth
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Olá! Para usar o bot, /login ou /criar_conta.')
    return ConversationHandler.END

async def criar_conta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Digite seu email:')
    return EMAIL

async def get_email_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text
    session = Session()
    if session.query(User).filter_by(email=email).first():
        await update.message.reply_text('Email já existe. Tente outro.')
        session.close()
        return EMAIL
    context.user_data['email'] = email
    await update.message.reply_text('Digite sua senha:')
    session.close()
    return PASSWORD

async def get_password_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    session = Session()
    user = User(email=context.user_data['email'], password_hash=hashed.decode('utf-8'))
    session.add(user)
    session.commit()
    context.user_data['user_id'] = user.id
    await update.message.reply_text('Conta criada! Agora você está logado.')
    session.close()
    return ACTION  # Vai para ações do bot específico

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Digite seu email:')
    return EMAIL

async def get_email_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text
    session = Session()
    user = session.query(User).filter_by(email=email).first()
    if not user:
        await update.message.reply_text('Conta inexistente. Use /criar_conta.')
        session.close()
        return ConversationHandler.END
    context.user_data['email'] = email
    await update.message.reply_text('Digite sua senha:')
    session.close()
    return PASSWORD

async def get_password_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    session = Session()
    user = session.query(User).filter_by(email=context.user_data['email']).first()
    if bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
        context.user_data['user_id'] = user.id
        await update.message.reply_text('Login sucesso! Agora você pode usar o bot.')
        session.close()
        return ACTION
    else:
        await update.message.reply_text('Senha incorreta. Tente novamente com /login.')
        session.close()
        return ConversationHandler.END

# Funções específicas do bot financeiro
async def action_financial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'user_id' not in context.user_data:
        await update.message.reply_text('Faça login primeiro com /login.')
        return ConversationHandler.END
    await update.message.reply_text('Envie sua despesa (ex: gastei 47 reais de uber no cartão Banco do Brasil)')
    return CONFIRM_EXPENSE

async def receive_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.lower()
    if not re.search(r'gastei', message):
        await update.message.reply_text('Formato inválido. Tente novamente.')
        return CONFIRM_EXPENSE
    draft = parse_expense(message)
    context.user_data['draft'] = draft
    draft_text = 'Rascunho — confirme antes de salvar:\n' + '\n'.join([f'• {k}: {v}' for k, v in draft.items()])
    draft_text += '\nSe tudo estiver correto, responda exatamente: Confirmar\nPara alterar algum campo, escreva o nome do campo e o novo valor.\nPara cancelar, escreva: Cancelar.'
    await update.message.reply_text(draft_text)
    return ALTER_EXPENSE

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == 'confirmar':
        draft = context.user_data['draft']
        session = Session()
        trans = Transaction(
            user_id=context.user_data['user_id'],
            tipo=draft['tipo'],
            valor=draft['valor'],
            descricao=draft['descricao'],
            categoria=draft['categoria'],
            data_ocorrencia=datetime.strptime(draft['data_ocorrencia'], '%d/%m/%Y'),
            forma_pagamento=draft['forma_pagamento'],
            conta_cartao=draft['conta_cartao'],
            parcelas=draft['parcelas'],
            status=draft['status']
        )
        session.add(trans)
        session.commit()
        await update.message.reply_text('Transação registrada com sucesso!')
        session.close()
        del context.user_data['draft']
        return ACTION  # Volta para mais ações
    elif text.lower() == 'cancelar':
        del context.user_data['draft']
        await update.message.reply_text('Cancelado.')
        return ACTION
    else:
        # Alterar campo
        try:
            field, new_value = text.split(':', 1)
            field = field.strip()
            context.user_data['draft'][field] = new_value.strip()
            draft_text = 'Rascunho atualizado:\n' + '\n'.join([f'• {k}: {v}' for k, v in context.user_data['draft'].items()])
            draft_text += '\nConfirme: Confirmar / Alterar / Cancelar'
            await update.message.reply_text(draft_text)
            return ALTER_EXPENSE
        except:
            await update.message.reply_text('Formato inválido para alteração. Ex: Valor: R$ 50,00')
            return ALTER_EXPENSE

# Funções específicas do bot de relatórios
async def action_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'user_id' not in context.user_data:
        await update.message.reply_text('Faça login primeiro com /login.')
        return ConversationHandler.END
    await update.message.reply_text('O que deseja? /relatorio_grafico ou /relatorio_pdf')
    return ConversationHandler.END  # Pode adicionar mais estados se necessário

async def generate_graphic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    trans = session.query(Transaction).filter_by(user_id=context.user_data['user_id']).all()
    if not trans:
        await update.message.reply_text('Nenhuma transação encontrada.')
        session.close()
        return
    categories = {}
    for t in trans:
        categories[t.categoria] = categories.get(t.categoria, 0) + t.valor
    fig, ax = plt.subplots()
    ax.pie(categories.values(), labels=categories.keys(), autopct='%1.1f%%')
    ax.set_title('Despesas por Categoria')
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    await update.message.reply_photo(photo=buf)
    session.close()

async def generate_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    trans = session.query(Transaction).filter_by(user_id=context.user_data['user_id']).all()
    if not trans:
        await update.message.reply_text('Nenhuma transação encontrada.')
        session.close()
        return
    
    # Gráfico
    categories = {}
    for t in trans:
        categories[t.categoria] = categories.get(t.categoria, 0) + t.valor
    fig, ax = plt.subplots()
    ax.pie(categories.values(), labels=categories.keys(), autopct='%1.1f%%')
    img_buf = io.BytesIO()
    fig.savefig(img_buf, format='png')
    img_buf.seek(0)
    
    # PDF
    pdf_buf = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buf, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph('Relatório de Transações', styles['Title']))
    
    data = [['Tipo', 'Valor', 'Descrição', 'Categoria', 'Data', 'Pagamento', 'Status']]
    for t in trans:
        data.append([t.tipo, f'R$ {t.valor:.2f}', t.descricao, t.categoria, t.data_ocorrencia.strftime('%d/%m/%Y'), t.forma_pagamento, t.status])
    
    table = Table(data)
    table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke)]))
    elements.append(table)
    
    elements.append(Paragraph('Gráfico de Despesas', styles['Heading2']))
    img = Image(img_buf, width=400, height=300)
    elements.append(img)
    
    doc.build(elements)
    pdf_buf.seek(0)
    await update.message.reply_document(document=pdf_buf, filename='relatorio.pdf')
    session.close()

# Configuração dos handlers comuns
def setup_handlers(application, is_financial=True):
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('criar_conta', criar_conta), CommandHandler('login', login)],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_create if 'create' in context.user_data else get_email_login)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password_create if 'create' in context.user_data else get_password_login)],
            ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, action_financial if is_financial else action_report)]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    application.add_handler(conv_handler)
    if is_financial:
        financial_conv = ConversationHandler(
            entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_expense)],
            states={ALTER_EXPENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)]},
            fallbacks=[],
            map_to_parent={ConversationHandler.END: ACTION}  # Volta para ACTION
        )
        application.add_handler(financial_conv)
    else:
        application.add_handler(CommandHandler('relatorio_grafico', generate_graphic))
        application.add_handler(CommandHandler('relatorio_pdf', generate_pdf))

# Rotas Flask para webhooks
@web_app.route('/financial', methods=['GET', 'POST'])
async def financial_webhook():
    if request.method == 'POST':
        update = Update.de_json(request.get_json(force=True), financial_application.bot)
        await financial_application.process_update(update)
        return Response('ok', status=200)
    return 'Financial Bot OK'

@web_app.route('/report', methods=['GET', 'POST'])
async def report_webhook():
    if request.method == 'POST':
        update = Update.de_json(request.get_json(force=True), report_application.bot)
        await report_application.process_update(update)
        return Response('ok', status=200)
    return 'Report Bot OK'

@web_app.route('/')
def home():
    return 'Bots rodando!'

async def init_applications():
    global financial_application, report_application
    financial_application = Application.builder().token(FINANCIAL_TOKEN).build()
    setup_handlers(financial_application, is_financial=True)
    await financial_application.bot.set_webhook(f'{URL}/financial')
    
    report_application = Application.builder().token(REPORT_TOKEN).build()
    setup_handlers(report_application, is_financial=False)
    await report_application.bot.set_webhook(f'{URL}/report')

async def main():
    await init_applications()

if __name__ == '__main__':
    asyncio.run(main())
    web_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
else:
    asyncio.run(main())  # Para Gunicorn
