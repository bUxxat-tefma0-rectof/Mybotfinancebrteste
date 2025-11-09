from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import os
import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    transactions = relationship('Transaction', backref='user')

class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    tipo = Column(String, default='Despesa')
    valor = Column(Float, nullable=False)
    descricao = Column(String)
    categoria = Column(String)
    data_ocorrencia = Column(DateTime, default=datetime.datetime.now)
    forma_pagamento = Column(String)
    conta_cartao = Column(String)
    parcelas = Column(String, default='não aplicável')
    status = Column(String)

engine = create_engine(os.getenv('DATABASE_URL'))
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)
