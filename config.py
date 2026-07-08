"""Configuracao do bot, carregada de variaveis de ambiente (.env).

Le e valida todas as variaveis necessarias ao inicializar; expostas como constantes
de modulo para uso em qualquer outro modulo sem re-parsear o ambiente.
"""
import os
import time
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv

load_dotenv()

# Aplica o fuso ao processo (no Docker o ENV TZ ja faz isso; cobre o dev local).
os.environ["TZ"] = os.getenv("TZ", "America/Sao_Paulo")
if hasattr(time, "tzset"):  # nao existe no Windows
    time.tzset()


def _decimal(valor: str) -> Decimal:
    """Converte string para Decimal, aceitando virgula como separador decimal.

    Args:
        valor: String numerica (ex.: "40,00" ou "40.00").

    Returns:
        Decimal correspondente, ou Decimal("0") em caso de valor invalido.
    """
    try:
        return Decimal(str(valor).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
MENSALIDADE_VALOR = _decimal(os.getenv("MENSALIDADE_VALOR", "0"))
DIA_COBRANCA = int(os.getenv("DIA_COBRANCA", "10"))
PIX_DESTINO = os.getenv("PIX_DESTINO", "")
NOME_RECEBEDOR = os.getenv("NOME_RECEBEDOR", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/bot.db")
TZ = os.getenv("TZ", "America/Sao_Paulo")
RECONCILE_INTERVAL_HORAS = int(os.getenv("RECONCILE_INTERVAL_HORAS", "3"))
OCR_CONFIANCA_MIN = int(os.getenv("OCR_CONFIANCA_MIN", "80"))
# Auto-aprova valores ate: divida atual + N mensalidades. Acima -> admin (evita OCR inflar).
MAX_MENSALIDADES_ADIANTADO = int(os.getenv("MAX_MENSALIDADES_ADIANTADO", "3"))
TAMANHO_MAX_ARQUIVO = int(os.getenv("TAMANHO_MAX_ARQUIVO", str(10 * 1024 * 1024)))


def eh_admin(telegram_id: int) -> bool:
    """Retorna True se o telegram_id pertence ao conjunto de administradores.

    Args:
        telegram_id: Identificador numerico do usuario no Telegram.

    Returns:
        True se o usuario e admin; False caso contrario.
    """
    return telegram_id in ADMIN_IDS


def validar_config() -> None:
    """Falha cedo se a configuracao essencial estiver ausente/invalida."""
    faltando = []
    if not BOT_TOKEN:
        faltando.append("BOT_TOKEN")
    if not ADMIN_IDS:
        faltando.append("ADMIN_IDS")
    if MENSALIDADE_VALOR <= 0:
        faltando.append("MENSALIDADE_VALOR")
    if not (1 <= DIA_COBRANCA <= 28):
        faltando.append("DIA_COBRANCA (use 1-28)")
    if not PIX_DESTINO and not NOME_RECEBEDOR:
        faltando.append("PIX_DESTINO ou NOME_RECEBEDOR")
    if faltando:
        raise SystemExit("Config invalida -> ausente/invalido: " + ", ".join(faltando))
