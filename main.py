"""Ponto de entrada do bot de mensalidade do futebol.

Inicializa o banco de dados, registra handlers e jobs, e inicia o long-polling
com a API do Telegram.
"""
import logging

from telegram import BotCommand, Update
from telegram.ext import ApplicationBuilder, Application, ContextTypes

from config import ADMIN_IDS, BOT_TOKEN, validar_config
from database import init_db
from handlers.admin import build_admin_handlers
from handlers.comprovante import build_comprovante_handler
from handlers.registro import build_registro_handler
from jobs.scheduler import agendar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger(__name__)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Registra excecoes nao tratadas e notifica todos os admins via Telegram.

    Args:
        update: Objeto de atualizacao do Telegram que gerou o erro (pode ser None).
        context: Contexto do handler, contem ``context.error`` com a excecao original.
    """
    log.exception("erro nao tratado em handler: %s", context.error)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, f"⚠️ Erro interno no bot: {context.error}")
        except Exception:
            pass


async def _post_init(app: Application) -> None:
    """Callback pos-inicializacao: registra o menu nativo de comandos do Telegram.

    Exibe os quatro comandos de usuario no botao "/" da interface do Telegram.

    Args:
        app: Instancia do ``Application`` do python-telegram-bot, ja conectada.
    """
    await app.bot.set_my_commands([
        BotCommand("start", "Fazer seu cadastro"),
        BotCommand("editar", "Corrigir seus dados"),
        BotCommand("status", "Ver seus dados e situação"),
        BotCommand("ajuda", "Ver o que dá para fazer"),
    ])


def main() -> None:
    """Valida a configuracao, cria as tabelas, registra handlers/jobs e inicia o bot."""
    validar_config()
    init_db()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)  # processa varios usuarios em paralelo (OCR nao bloqueia a fila)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .get_updates_read_timeout(40.0)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(build_registro_handler())
    for handler in build_admin_handlers():
        app.add_handler(handler)
    app.add_handler(build_comprovante_handler())
    app.add_error_handler(_on_error)

    agendar(app)

    log.info("Bot iniciado. Aguardando mensagens...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
