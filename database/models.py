"""Modelos de dados: Jogador e Pagamento.

Define as duas entidades persistidas via SQLAlchemy 2 (Mapped/mapped_column).
Jogador armazena o perfil e o historico de notificacoes; Pagamento registra
cada comprovante recebido com os dados extraidos pelo OCR e o resultado da validacao.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarativa compartilhada por todos os modelos ORM do projeto."""


class Jogador(Base):
    """Representa um jogador cadastrado no bot.

    Atributos principais:
        telegram_id: Chave primaria — ID do usuario no Telegram (BigInteger).
        nome: Nome completo digitado no cadastro.
        nome_pix: Nome como aparece no comprovante (titular da conta de origem).
        telefone: Contato opcional.
        status: ``"em_dia"`` ou ``"inadimplente"`` (atualizado pela reconciliacao).
        data_adesao: Data de entrada no grupo; usada para calcular mensalidades vencidas.
        ativo: Se False, o jogador nao e cobrado (pausa temporaria).
        ultima_notificacao: Data do ultimo lembrete enviado (idempotencia diaria).
        valor_mensalidade: Snapshot do valor vigente na adesao; nao muda retroativamente.
        desativado_em: Registra quando o jogador foi desativado; ao reativar, o periodo
            parado e descontado da data_adesao para nao cobrar meses inativos.
    """

    __tablename__ = "jogadores"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    nome: Mapped[str] = mapped_column(String(120))
    nome_pix: Mapped[str | None] = mapped_column(String(120), nullable=True)
    telefone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="em_dia")
    data_adesao: Mapped[date] = mapped_column(Date, default=date.today)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    ultima_notificacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    valor_mensalidade: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    desativado_em: Mapped[date | None] = mapped_column(Date, nullable=True)

    pagamentos: Mapped[list["Pagamento"]] = relationship(
        back_populates="jogador", lazy="raise")


class Pagamento(Base):
    """Representa um comprovante de pagamento enviado por um jogador.

    Atributos principais:
        competencia: Mes de referencia no formato ``"AAAA-MM"``.
        valor: Valor aprovado (abatido do saldo acumulado do jogador).
        file_id: Identificador do arquivo no Telegram (foto ou PDF); usado para dedup.
        origem_extraida / destino_extraido: Nome do pagador/recebedor lido pelo OCR.
        valor_extraido / data_extraida: Valor e data lidos do texto do comprovante.
        transacao: ID fim-a-fim do PIX (E + ISPB + ...) para deduplicacao.
        status_validacao: ``"auto_aprovado"``, ``"aprovado"``, ``"rejeitado"``
            ou ``"pendente_admin"``.
    """

    __tablename__ = "pagamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    jogador_id: Mapped[int] = mapped_column(ForeignKey("jogadores.telegram_id"), index=True)
    competencia: Mapped[str] = mapped_column(String(7))  # AAAA-MM
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    data_pagamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_id: Mapped[str] = mapped_column(String(256))
    origem_extraida: Mapped[str | None] = mapped_column(String(120), nullable=True)
    destino_extraido: Mapped[str | None] = mapped_column(String(120), nullable=True)
    valor_extraido: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    data_extraida: Mapped[date | None] = mapped_column(Date, nullable=True)
    # unique: deduplicacao a prova de concorrencia (NULLs sao distintos -> varios permitidos)
    transacao: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    is_pdf: Mapped[bool] = mapped_column(Boolean, default=False)  # p/ reenviar no /pendentes
    status_validacao: Mapped[str] = mapped_column(String(16))
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    jogador: Mapped["Jogador"] = relationship(
        back_populates="pagamentos", lazy="raise")
