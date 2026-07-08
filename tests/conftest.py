"""Configuracao de testes: env + banco temporario isolado por teste."""
import os
import tempfile

# Valores de teste FIXOS, definidos ANTES de importar config/database. Sao atribuidos
# diretamente (nao setdefault) para nao herdar o .env/shell do dev — os testes precisam
# ser deterministicos independentemente da mensalidade/recebedor configurados na maquina.
_DB_PATH = os.path.join(tempfile.gettempdir(), "bot_test.db")
os.environ["BOT_TOKEN"] = "test-token"
os.environ["ADMIN_IDS"] = "999"
os.environ["MENSALIDADE_VALOR"] = "40.00"
os.environ["DIA_COBRANCA"] = "10"
os.environ["PIX_DESTINO"] = "+5511912345678"       # ficticio
os.environ["NOME_RECEBEDOR"] = "Ricardo Alves Pereira"  # ficticio
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import pytest  # noqa: E402

from database import engine  # noqa: E402
from database.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _db_limpo():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
