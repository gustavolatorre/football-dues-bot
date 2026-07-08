"""Reconciliacao idempotente: abre a competencia e notifica inadimplentes.

Roda no startup e a cada N horas (ver jobs/scheduler.py). Idempotente por
`ultima_notificacao` -> no maximo 1 lembrete por jogador por dia, mesmo apos
reinicios/redeploys.
"""
import asyncio
import logging
from datetime import date

from telegram.error import TelegramError

from config import DIA_COBRANCA
from database import repo

log = logging.getLogger(__name__)

MSG_COBRANCA = (
    "Olá, {nome}! ⚽\n\n"
    "Você está com a mensalidade em aberto.\n"
    "Valor a pagar: {frase}.\n\n"
    "Quando pagar, envie aqui o comprovante do PIX (foto ou PDF) para confirmar."
)


def competencia_atual() -> str:
    """Retorna a competencia do mes corrente no formato ``"AAAA-MM"``.

    Returns:
        String no formato ``"AAAA-MM"`` (ex.: ``"2025-06"``).
    """
    return date.today().strftime("%Y-%m")


def fmt_brl(valor) -> str:
    """Formata um numero no padrao monetario pt-BR, sem o simbolo.

    Ex.: ``Decimal("1234.5")`` -> ``"1234,50"``. Unica funcao de formatacao de
    moeda do projeto — reutilizada por todos os handlers (evita duplicacao).
    """
    return f"{valor:.2f}".replace(".", ",")


def frase_falta(falta, meses: int, parcial: bool) -> str:
    """Formata o valor devido em uma frase legivel para o jogador.

    Se o debito for um multiplo exato de mensalidades inteiras, inclui a
    quantidade entre parenteses (ex.: ``"R$ 80,00 (2 meses)"``). Se for
    parcial (sobra de mes anterior) ou menor que uma mensalidade, exibe
    apenas o valor (ex.: ``"R$ 20,00"``).

    Args:
        falta: Valor em aberto (Decimal ou float, positivo).
        meses: Numero inteiro de meses completos em atraso.
        parcial: True se o debito nao e multiplo exato da mensalidade.

    Returns:
        String formatada em reais pronta para exibicao ao usuario.
    """
    valor = f"R$ {fmt_brl(falta)}"
    if parcial or meses < 1:
        return valor
    return f"{valor} ({meses} {'mês' if meses == 1 else 'meses'})"


async def reconciliar(bot, forcar: bool = False) -> dict:
    """Reconcilia inadimplencia e envia lembretes de cobranca a todos os jogadores ativos.

    Para cada jogador ativo:
    - Recalcula a situacao financeira (saldo acumulado).
    - Atualiza o campo ``status`` no banco se mudou.
    - Envia o lembrete de cobranca se o jogador estiver devendo E (``forcar=True`` OU
      ainda nao foi avisado hoje — garantindo no maximo 1 mensagem por dia por jogador).

    Args:
        bot: Objeto ``Bot`` do python-telegram-bot usado para enviar mensagens.
        forcar: Se True, reenvia o lembrete mesmo que o jogador ja tenha sido avisado
            hoje (usado pelo comando /cobrar do admin e pelo botao do /relatorio).

    Returns:
        Dicionario de resumo com chaves: ``notificados``, ``novos_inadimplentes``,
        ``em_dia``, ``inadimplentes`` e ``ja_avisados``.
    """
    hoje = date.today()
    resumo = {"notificados": 0, "novos_inadimplentes": 0, "em_dia": 0,
              "inadimplentes": 0, "ja_avisados": 0}
    jogadores = await asyncio.to_thread(repo.jogadores_ativos)

    for jog in jogadores:
        # saldo acumulado em dinheiro (devendo/credito)
        sit = await asyncio.to_thread(repo.situacao, jog.telegram_id, DIA_COBRANCA)
        if sit["em_dia"]:
            if jog.status != "em_dia":
                await asyncio.to_thread(repo.set_status, jog.telegram_id, "em_dia")
                jog.status = "em_dia"
            resumo["em_dia"] += 1
            continue

        resumo["inadimplentes"] += 1
        if jog.status != "inadimplente":
            await asyncio.to_thread(repo.set_status, jog.telegram_id, "inadimplente")
            jog.status = "inadimplente"
            resumo["novos_inadimplentes"] += 1

        # automatico: 1 aviso por dia (idempotente). manual (forcar): sempre reenvia.
        if forcar or jog.ultima_notificacao != hoje:
            try:
                await bot.send_message(
                    jog.telegram_id,
                    MSG_COBRANCA.format(
                        nome=(jog.nome.split()[0] if jog.nome else "jogador"),
                        frase=frase_falta(sit["falta"], sit["meses_atraso"], sit["parcial"]),
                    ),
                )
                await asyncio.to_thread(repo.set_ultima_notificacao, jog.telegram_id, hoje)
                resumo["notificados"] += 1
            except TelegramError as e:
                log.warning("falha ao notificar %s: %s", jog.telegram_id, e)
        else:
            resumo["ja_avisados"] += 1

    log.info("reconciliar (%s): %s", hoje.strftime("%Y-%m"), resumo)
    return resumo
