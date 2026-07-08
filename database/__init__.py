"""Engine e sessao do SQLAlchemy. Agnostico a SQLite (dev) ou Postgres (Railway).

Normaliza a URL de conexao, garante que o diretorio SQLite existe e expoe
``engine``, ``SessionLocal`` e ``init_db`` para o restante da aplicacao.
"""
import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL

log = logging.getLogger(__name__)


def _normalizar_url(url: str) -> str:
    """Converte o esquema ``postgres://`` (Railway) para ``postgresql+psycopg2://``.

    O Railway injeta a variavel DATABASE_URL com o prefixo legado ``postgres://``,
    que o SQLAlchemy 2 com psycopg2 nao reconhece diretamente.

    Args:
        url: URL de conexao original.

    Returns:
        URL com o esquema corrigido, pronta para o ``create_engine``.
    """
    # Railway expoe postgres://; SQLAlchemy + psycopg2 exige postgresql+psycopg2://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


def _garantir_dir_sqlite(url: str) -> None:
    """Cria o diretorio pai do arquivo SQLite caso ainda nao exista.

    Nao faz nada se a URL nao for SQLite.

    Args:
        url: URL de conexao ja normalizada.
    """
    if url.startswith("sqlite:///"):
        caminho = url.replace("sqlite:///", "", 1)
        pasta = os.path.dirname(caminho)
        if pasta:
            os.makedirs(pasta, exist_ok=True)


_url = _normalizar_url(DATABASE_URL)
_garantir_dir_sqlite(_url)

engine = create_engine(_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

# SQLite nao aplica FOREIGN KEY por padrao; habilita por conexao para o dev
# ter as mesmas garantias de integridade do Postgres (prod).
if engine.dialect.name == "sqlite":
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    """Cria as tabelas (se nao existirem) e aplica migracoes incrementais.

    Deve ser chamado uma vez na inicializacao do processo, antes de qualquer
    acesso ao banco.
    """
    from database.models import Base

    Base.metadata.create_all(engine)
    _migrar_colunas()


_TABELAS_OK = {"jogadores", "pagamentos"}
_TIPOS_OK = {"NUMERIC(10,2)", "DATE", "VARCHAR(64)", "BOOLEAN"}


def _migrar_colunas() -> None:
    """Adiciona colunas novas em bancos ja existentes (sem Alembic).

    Idempotente: so altera o que falta; usa `IF NOT EXISTS` no Postgres para evitar
    corrida em redeploy. Tabela/coluna/tipo passam por allowlist (DDL nao parametriza).
    """
    import re

    from sqlalchemy import inspect, text

    insp = inspect(engine)
    existentes = {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}
    novas = {
        ("jogadores", "valor_mensalidade"): "NUMERIC(10,2)",
        ("jogadores", "desativado_em"): "DATE",
        ("pagamentos", "transacao"): "VARCHAR(64)",
        ("pagamentos", "is_pdf"): "BOOLEAN",
    }
    pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        for (tabela, coluna), tipo in novas.items():
            # allowlist explicita (nao usar assert: some com `python -O`)
            if not (tabela in _TABELAS_OK and re.fullmatch(r"[a-z_]+", coluna)
                    and tipo in _TIPOS_OK):
                raise ValueError(f"migracao invalida: {tabela}.{coluna} {tipo}")
            if tabela in existentes and coluna not in existentes[tabela]:
                existe = "IF NOT EXISTS " if pg else ""
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {existe}{coluna} {tipo}"))

    # Indice UNIQUE em pagamentos.transacao -> dedup a prova de concorrencia.
    # Em transacao separada para nao desfazer as colunas; nao derruba o startup.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_pagamentos_transacao "
                "ON pagamentos (transacao)"
            ))
    except Exception as e:  # ex.: duplicatas pre-existentes
        log.warning("nao foi possivel criar indice unico de transacao: %s", e)
