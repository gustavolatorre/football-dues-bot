"""Comandos do admin (/relatorio) e aprovacao/rejeicao de comprovantes.

Inclui handlers de texto (CommandHandler) e callbacks de botoes inline
(CallbackQueryHandler). Todos os comandos de gerenciamento verificam a identidade
do usuario antes de executar qualquer operacao. Decisoes sobre comprovantes so
valem uma vez (PagamentoJaDecidido protege contra decisao dupla).
"""
import asyncio
import logging
import re
from decimal import Decimal, InvalidOperation

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS, DIA_COBRANCA, eh_admin
from database import repo
from handlers.comprovante import enviar_revisao
from services.cobranca import competencia_atual, fmt_brl, frase_falta, reconciliar

log = logging.getLogger(__name__)


def _parse_valor(txt: str) -> Decimal | None:
    """Converte um valor digitado pelo admin em ``Decimal`` (aceita ``40``, ``40,00``,
    ``R$ 40,00``, ``1.234,50``). Retorna ``None`` se nao for um numero positivo."""
    t = re.sub(r"[^\d,.]", "", txt or "")
    if not t:
        return None
    if "," in t and "." in t:  # pt-BR: ponto de milhar + virgula decimal
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return v if v > 0 else None


async def _avisar_jogador_aprovado(bot, jogador_id: int) -> None:
    """Notifica o jogador que o comprovante foi aprovado, com o saldo atualizado.

    Compartilhado pelos fluxos de aprovacao (botao direto e informar/corrigir valor).
    Falhas de envio sao apenas logadas — nunca derrubam a operacao do admin.
    """
    sit = await asyncio.to_thread(repo.situacao, jogador_id, DIA_COBRANCA)
    if sit["falta"] > 0:
        extra = f"Ainda faltam {frase_falta(sit['falta'], sit['meses_atraso'], sit['parcial'])}."
    elif sit["credito"] > 0:
        v = fmt_brl(sit["credito"])
        extra = f"Você está em dia, com crédito de R$ {v}."
    else:
        extra = "Você está em dia. ✅"
    try:
        await bot.send_message(jogador_id, f"Seu comprovante foi aprovado! ✅ {extra}")
    except Exception as e:
        log.warning("falha ao avisar jogador %s: %s", jogador_id, e)


async def relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe o relatorio financeiro do mes para o admin.

    Mostra totais (ativos, em dia, inadimplentes), valor arrecadado, valor em
    aberto, lista de devedores (ordenada por debito decrescente) e lista de
    jogadores com credito. Inclui botao inline para disparar cobranca imediata.
    Comando restrito a admins.
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("Comando restrito ao(s) administrador(es).")
        return
    comp = competencia_atual()
    r = await asyncio.to_thread(repo.relatorio, comp, DIA_COBRANCA)
    arrecadado = fmt_brl(r["arrecadado"])
    devido = fmt_brl(r["total_devido"])
    linhas = [
        f"📊 Relatório — {comp}",
        "",
        f"Mensalistas ativos: {r['total']}",
        f"Em dia: {r['em_dia']}",
        f"Inadimplentes: {r['inadimplentes']}",
        f"Arrecadado no mês: R$ {arrecadado}",
        f"A receber (em aberto): R$ {devido}",
    ]
    if r["devedores"]:
        linhas.append("")
        linhas.append("Devendo:")
        for d in r["devedores"]:
            linhas.append(f"• {d['nome']} — {frase_falta(d['falta'], d['meses'], d['parcial'])}")
    if r["creditos"]:
        linhas.append("")
        linhas.append("Com crédito:")
        for d in r["creditos"]:
            v = fmt_brl(d["credito"])
            linhas.append(f"• {d['nome']} — R$ {v}")
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("📣 Cobrar inadimplentes agora", callback_data="cobrar_todos"),
    ]])
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado)


async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe a mensagem de ajuda com os comandos disponiveis.

    Mostra comandos basicos para todos os usuarios. Se o remetente for admin,
    acrescenta a secao de comandos administrativos.
    """
    texto = (
        "🤖 *Bot da Mensalidade do Futebol*\n\n"
        "*O que você pode fazer:*\n"
        "/start — fazer seu cadastro\n"
        "/editar — corrigir seus dados\n"
        "/status — ver seus dados e situação\n"
        "/ajuda — esta mensagem\n\n"
        "💸 *Para pagar:* é só enviar aqui o *comprovante do PIX* (foto ou PDF). "
        "Eu confirmo automaticamente quando estiver tudo certo. Se algo não bater, "
        "o admin confere e confirma — seu pagamento nunca se perde. ⚽"
    )
    if eh_admin(update.effective_user.id):
        texto += (
            "\n\n👑 *Admin:*\n"
            "/relatorio — relatório do mês (com botão de cobrar todos)\n"
            "/cobrar — cobrar todos os inadimplentes agora\n"
            "/pendentes — comprovantes a revisar (aprovar, corrigir/informar valor ou rejeitar)\n"
            "/jogadores — listar jogadores e IDs\n"
            "/desativar <id> — parar de cobrar alguém (mantém o histórico)\n"
            "/reativar <id> — voltar a cobrar\n"
            "/remover <id> — excluir definitivamente (pede confirmação; apaga o histórico)"
        )
    await update.message.reply_text(texto, parse_mode="Markdown")


async def cobrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispara a reconciliacao na hora (admin). Util para testar/forcar a cobranca."""
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("Comando restrito ao(s) administrador(es).")
        return
    resumo = await reconciliar(context.bot, forcar=True)
    await update.message.reply_text(
        "Cobrança disparada ✅\n"
        f"Inadimplentes: {resumo['inadimplentes']}\n"
        f"Avisados agora: {resumo['notificados']}"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe os dados do proprio jogador e sua situacao financeira atual.

    Mostra nome, nome_pix, telefone, data de adesao e situacao: devendo (com
    valor), em dia com credito ou em dia sem credito.
    """
    jog = await asyncio.to_thread(repo.obter_jogador, update.effective_user.id)
    if jog is None:
        await update.message.reply_text("Você não está cadastrado. Use /start.")
        return
    sit = await asyncio.to_thread(repo.situacao, update.effective_user.id, DIA_COBRANCA)
    if sit["falta"] > 0:
        situacao_txt = f"devendo {frase_falta(sit['falta'], sit['meses_atraso'], sit['parcial'])} ⚠️"
    elif sit["credito"] > 0:
        v = fmt_brl(sit["credito"])
        situacao_txt = f"em dia ✅ (crédito de R$ {v})"
    else:
        situacao_txt = "em dia ✅"
    await update.message.reply_text(
        "Seus dados:\n"
        f"Nome: {jog.nome}\n"
        f"Nome no PIX: {jog.nome_pix}\n"
        f"Telefone: {jog.telefone or '—'}\n"
        f"Situação: {situacao_txt}\n"
        f"Cadastrado em: {jog.data_adesao}\n\n"
        "Para corrigir, use /editar."
    )


async def callback_decisao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback inline para aprovar ou rejeitar um pagamento pendente.

    Acionado pelos botoes "Aprovar"/"Rejeitar" enviados ao admin junto com o
    comprovante. Atualiza o registro no banco, edita a legenda da mensagem com
    o resultado e notifica o jogador sobre a decisao (incluindo saldo atualizado
    em caso de aprovacao).
    """
    query = update.callback_query
    if not eh_admin(query.from_user.id):
        await query.answer("Apenas administradores.", show_alert=True)
        return
    acao, _, pid = query.data.partition(":")
    if acao not in ("aprovar", "rejeitar") or not pid.isdigit():
        await query.answer("Dado inválido.", show_alert=True)
        return
    aprovado = acao == "aprovar"
    try:
        pg = await asyncio.to_thread(repo.decidir_pagamento, int(pid), aprovado, DIA_COBRANCA)
    except repo.PagamentoJaDecidido as e:
        # decisao dupla (outro admin / clique repetido): a primeira vale
        await query.answer(f"Este comprovante já foi decidido ({e.status}).", show_alert=True)
        return
    await query.answer()
    if pg is None:
        await query.edit_message_caption(caption="Pagamento não encontrado.")
        return

    marca = "APROVADO ✅" if aprovado else "REJEITADO ❌"
    legenda = (query.message.caption or "") + f"\n\n→ {marca}"
    try:
        await query.edit_message_caption(caption=legenda)
    except Exception:  # mensagem sem caption (ex.: editada) -> ignora
        pass

    if aprovado:
        await _avisar_jogador_aprovado(context.bot, pg.jogador_id)
    else:
        try:
            await context.bot.send_message(
                pg.jogador_id,
                "Seu comprovante não foi aprovado. Confira os dados e envie novamente, por favor.",
            )
        except Exception as e:
            log.warning("falha ao avisar jogador %s: %s", pg.jogador_id, e)


async def callback_setvalor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback dos botoes "Informar valor" / "Corrigir valor".

    Pede ao admin o valor real do comprovante via ``ForceReply``. A resposta e
    capturada por ``receber_valor_admin``, que aprova o pagamento com o valor
    informado (o excedente vira credito automaticamente).
    """
    query = update.callback_query
    if not eh_admin(query.from_user.id):
        await query.answer("Apenas administradores.", show_alert=True)
        return
    _, _, pid = query.data.partition(":")
    if not pid.isdigit():
        await query.answer("Dado inválido.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(
        f"Comprovante #{pid} — digite o valor que caiu na conta (ex.: 40 ou 40,00):",
        reply_markup=ForceReply(),
    )


async def receber_valor_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recebe o valor digitado pelo admin (resposta ao ForceReply) e aprova o comprovante.

    Le o ``#<pid>`` da mensagem respondida para identificar o pagamento, converte o
    texto em ``Decimal``, aprova com esse valor e notifica o jogador. Ignora respostas
    que nao sejam ao nosso prompt de valor.
    """
    msg = update.message
    ref = msg.reply_to_message
    if ref is None or not ref.text:
        return
    m = re.search(r"[Cc]omprovante #(\d+)", ref.text)
    if m is None:
        return  # resposta a outra mensagem — nao e o nosso prompt de valor
    pid = int(m.group(1))
    valor = _parse_valor(msg.text)
    if valor is None:
        await msg.reply_text("Valor inválido. Envie apenas o número, ex.: 40 ou 40,00.")
        return
    try:
        pg = await asyncio.to_thread(repo.decidir_pagamento, pid, True, DIA_COBRANCA, valor)
    except repo.PagamentoJaDecidido as e:
        await msg.reply_text(f"O comprovante #{pid} já foi decidido antes ({e.status}) — nada foi alterado.")
        return
    if pg is None:
        await msg.reply_text("Pagamento não encontrado.")
        return
    v = fmt_brl(valor)
    await msg.reply_text(f"Comprovante #{pid} aprovado com valor de R$ {v}. ✅")
    await _avisar_jogador_aprovado(context.bot, pg.jogador_id)


async def cmd_pendentes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reenvia ao admin todos os comprovantes aguardando decisao (com os botoes).

    Garante que nenhum comprovante recebido fique esquecido: o admin pode chamar
    ``/pendentes`` a qualquer momento para revisar a fila.
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("Comando restrito ao(s) administrador(es).")
        return
    itens = await asyncio.to_thread(repo.pendentes)
    if not itens:
        await update.message.reply_text("Nenhum comprovante pendente. 👍")
        return
    await update.message.reply_text(f"⏳ {len(itens)} comprovante(s) pendente(s) de revisão:")
    for it in itens:
        _, teto = await asyncio.to_thread(repo.limites_validacao, it["jogador_id"], DIA_COBRANCA)
        await enviar_revisao(
            context.bot, jog_nome=it["nome"], jog_id=it["jogador_id"], pid=it["id"],
            file_id=it["file_id"], is_pdf=it["is_pdf"], valor=it["valor"],
            origem=it["origem"], destino=it["destino"], chave=None, data=it["data"],
            teto=teto, motivo="Pendente de revisão",
        )


async def jogadores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos os jogadores (ativos e inativos) com status e ID do Telegram.

    Exibe um indicador visual (em dia vs. inadimplente) e marca os inativos.
    Mostra instrucoes de como desativar, reativar ou remover. Comando restrito a admins.
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("Comando restrito ao(s) administrador(es).")
        return
    lista = await asyncio.to_thread(repo.listar_jogadores, True)
    if not lista:
        await update.message.reply_text("Nenhum jogador cadastrado.")
        return
    linhas = []
    for j in lista:
        marca = "✅" if j.status == "em_dia" else "⚠️"
        inativo = "" if j.ativo else " (inativo)"
        linhas.append(f"{marca} {j.nome}{inativo} — id {j.telegram_id}")
    await update.message.reply_text(
        "👥 Jogadores:\n" + "\n".join(linhas)
        + "\n\nGerenciar: /desativar <id> · /reativar <id> · /remover <id>"
    )


async def _gerenciar(update: Update, context: ContextTypes.DEFAULT_TYPE, acao: str):
    """Handler interno compartilhado pelos comandos desativar/reativar/remover.

    Valida a permissao de admin e o argumento de ID antes de chamar a funcao
    de repositorio correspondente.

    Args:
        update: Objeto de atualizacao do Telegram.
        context: Contexto do handler; ``context.args[0]`` deve ser o telegram_id
            numerico do jogador alvo.
        acao: Uma das strings ``"desativar"``, ``"reativar"`` ou ``"remover"``.
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("Comando restrito ao(s) administrador(es).")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"Uso: /{acao} <id>  (veja os IDs em /jogadores)")
        return
    tid = int(context.args[0])
    if acao == "remover":
        # exclusao apaga o historico financeiro -> exige confirmacao explicita
        if len(context.args) < 2 or context.args[1].lower() != "sim":
            await update.message.reply_text(
                "⚠️ /remover apaga o jogador e TODO o histórico de pagamentos dele, "
                "sem volta.\n"
                f"Se a ideia é só pausar a cobrança, prefira /desativar {tid}.\n\n"
                f"Para confirmar a exclusão definitiva: /remover {tid} sim"
            )
            return
        nome = await asyncio.to_thread(repo.remover_jogador, tid)
        msg = f"{nome} foi removido definitivamente." if nome else "Jogador não encontrado."
    else:
        ativo = acao == "reativar"
        nome = await asyncio.to_thread(repo.set_ativo, tid, ativo)
        if nome is None:
            msg = "Jogador não encontrado."
        elif ativo:
            msg = f"{nome} foi reativado (volta a ser cobrado)."
        else:
            msg = f"{nome} foi desativado (não será mais cobrado)."
    await update.message.reply_text(msg)


async def desativar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Desativa um jogador (pausa a cobranca). Uso: /desativar <id>."""
    await _gerenciar(update, context, "desativar")


async def reativar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reativa um jogador (retoma a cobranca). Uso: /reativar <id>."""
    await _gerenciar(update, context, "reativar")


async def remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove um jogador e todos os seus pagamentos definitivamente. Uso: /remover <id>."""
    await _gerenciar(update, context, "remover")


async def callback_cobrar_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback inline do botao "Cobrar inadimplentes agora" do /relatorio.

    Dispara a reconciliacao com ``forcar=True``, garantindo que todos os
    inadimplentes recebam o lembrete independentemente de ja terem sido avisados hoje.
    """
    query = update.callback_query
    if not eh_admin(query.from_user.id):
        await query.answer("Apenas administradores.", show_alert=True)
        return
    await query.answer("Disparando cobrança...")
    resumo = await reconciliar(context.bot, forcar=True)
    await query.message.reply_text(
        "📣 Cobrança disparada ✅\n"
        f"Inadimplentes: {resumo['inadimplentes']}\n"
        f"Avisados agora: {resumo['notificados']}"
    )


def build_admin_handlers() -> list:
    """Constroi e retorna a lista de handlers de admin (CommandHandler e CallbackQueryHandler).

    Returns:
        Lista com todos os handlers de comandos de admin e callbacks de botoes inline.
    """
    return [
        CommandHandler(["ajuda", "help"], ajuda),
        CommandHandler("relatorio", relatorio),
        CommandHandler("cobrar", cobrar),
        CommandHandler("jogadores", jogadores),
        CommandHandler("pendentes", cmd_pendentes),
        CommandHandler("desativar", desativar),
        CommandHandler("reativar", reativar),
        CommandHandler("remover", remover),
        CommandHandler("status", status),
        CallbackQueryHandler(callback_cobrar_todos, pattern=r"^cobrar_todos$"),
        CallbackQueryHandler(callback_decisao, pattern=r"^(aprovar|rejeitar):\d+$"),
        CallbackQueryHandler(callback_setvalor, pattern=r"^setvalor:\d+$"),
        MessageHandler(
            filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS)
            & filters.ChatType.PRIVATE,
            receber_valor_admin,
        ),
    ]
