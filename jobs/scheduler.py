"""Agenda a reconciliacao: no startup e a cada N horas (JobQueue do PTB).

Registra dois jobs na JobQueue do python-telegram-bot:
- Uma execucao unica 5 segundos apos o startup (catch-up de reinicializacoes).
- Uma execucao periodica a cada ``RECONCILE_INTERVAL_HORAS`` horas, comecando
  60 segundos apos o startup para nao sobrepor com o job inicial.
"""
import logging

from telegram.ext import ContextTypes

from config import RECONCILE_INTERVAL_HORAS
from services.cobranca import reconciliar

log = logging.getLogger(__name__)


async def _job_reconciliar(context: ContextTypes.DEFAULT_TYPE):
    """Wrapper do job que chama ``reconciliar`` e captura excecoes sem derrubar o scheduler.

    Args:
        context: Contexto do job passado automaticamente pelo JobQueue do PTB;
            usado para acessar ``context.bot``.
    """
    try:
        await reconciliar(context.bot)
    except Exception as e:
        log.exception("erro na reconciliacao agendada: %s", e)


def agendar(app) -> None:
    """Registra os jobs de reconciliacao na JobQueue da aplicacao.

    Dois jobs sao agendados:
    - ``run_once`` com ``when=5s``: executa a reconciliacao 5 segundos apos o startup
      para recuperar qualquer inadimplencia gerada durante uma queda do bot.
    - ``run_repeating`` com ``interval=RECONCILE_INTERVAL_HORAS * 3600``: reexecuta
      periodicamente (primeiro disparo 60 segundos apos o startup).

    Args:
        app: Instancia de ``Application`` do python-telegram-bot, ja construida,
            com ``job_queue`` disponivel.
    """
    jq = app.job_queue
    jq.run_once(_job_reconciliar, when=5)  # catch-up no startup
    jq.run_repeating(
        _job_reconciliar,
        interval=RECONCILE_INTERVAL_HORAS * 3600,
        first=60,
    )
