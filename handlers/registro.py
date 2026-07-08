"""Fluxo de cadastro do jogador via /start (ConversationHandler).

Gerencia o dialogo de tres etapas (nome, nome_pix, telefone) tanto para novo
cadastro (/start) quanto para edicao de dados existentes (/editar). O cadastro
so e salvo ao completar todas as etapas; /cancelar aborta sem persistir nada.
"""
import asyncio

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import DIA_COBRANCA, MENSALIDADE_VALOR
from database import repo
from services.cobranca import fmt_brl

NOME, NOME_PIX, TELEFONE = range(3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia o cadastro ou informa o jogador que ja esta registrado.

    Se o jogador ja existir no banco, exibe o status atual e encerra a conversa.
    Caso contrario, solicita o nome completo e inicia o fluxo de cadastro.
    """
    uid = update.effective_user.id
    jog = await asyncio.to_thread(repo.obter_jogador, uid)
    if jog:
        await update.message.reply_text(
            f"Você já está cadastrado, {jog.nome}! Status atual: {jog.status}.\n"
            "Use /status para ver seus dados, /editar para corrigir, ou envie um comprovante."
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "Bem-vindo ao bot da mensalidade do futebol! ⚽\n\nQual é o seu nome completo?"
    )
    return NOME


async def editar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia a edicao dos dados do jogador ja cadastrado.

    Exige que o jogador ja esteja cadastrado; redireciona para /start caso contrario.
    Reutiliza os mesmos estados do ConversationHandler de cadastro.
    """
    uid = update.effective_user.id
    jog = await asyncio.to_thread(repo.obter_jogador, uid)
    if jog is None:
        await update.message.reply_text("Você ainda não está cadastrado. Use /start primeiro.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Vamos atualizar seu cadastro. 📝\nQual é o seu nome completo?"
    )
    return NOME


async def receber_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Armazena o nome digitado e pergunta pelo nome no PIX."""
    context.user_data["nome"] = update.message.text.strip()
    await update.message.reply_text(
        "Como o seu nome aparece no comprovante do PIX (titular da conta de origem)?\n"
        "Se for igual ao que você acabou de digitar, responda /igual."
    )
    return NOME_PIX


async def nome_pix_igual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resposta ao /igual: usa o nome completo como nome_pix e avanca para o telefone."""
    context.user_data["nome_pix"] = context.user_data.get("nome")
    return await _perguntar_telefone(update)


async def receber_nome_pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Armazena o nome no PIX digitado e avanca para a etapa do telefone."""
    context.user_data["nome_pix"] = update.message.text.strip()
    return await _perguntar_telefone(update)


async def _perguntar_telefone(update: Update):
    await update.message.reply_text(
        "Telefone para contato? (opcional)\nEnvie o número ou use /pular."
    )
    return TELEFONE


async def telefone_pular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resposta ao /pular: finaliza o cadastro sem telefone."""
    return await _finalizar(update, context, telefone=None)


async def receber_telefone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Armazena o telefone digitado e finaliza o cadastro."""
    return await _finalizar(update, context, telefone=update.message.text.strip())


async def _finalizar(update: Update, context: ContextTypes.DEFAULT_TYPE, telefone):
    """Persiste o jogador no banco e envia a mensagem de boas-vindas.

    Chamado tanto pelo recebimento de telefone quanto pelo /pular. Limpa
    ``context.user_data`` apos salvar para nao deixar estado residual.

    Args:
        update: Objeto de atualizacao do Telegram com a mensagem do usuario.
        context: Contexto do handler; ``user_data`` contem ``nome`` e ``nome_pix``.
        telefone: Numero de contato ou ``None`` se o jogador pulou esta etapa.
    """
    uid = update.effective_user.id
    await asyncio.to_thread(
        repo.criar_jogador,
        uid,
        context.user_data.get("nome"),
        context.user_data.get("nome_pix"),
        telefone,
    )
    context.user_data.clear()
    valor = fmt_brl(MENSALIDADE_VALOR)
    await update.message.reply_text(
        "Cadastro concluído! ✅ Bem-vindo ao time! ⚽\n\n"
        f"💰 *Mensalidade:* R$ {valor}, vence todo dia {DIA_COBRANCA}.\n\n"
        "*Como funciona:*\n"
        "• Quando pagar, envie aqui o *comprovante do PIX* (foto ou PDF) — eu confirmo "
        "o pagamento automaticamente.\n"
        "• Se passar do vencimento sem pagar, eu te lembro todo dia até quitar. 😉\n\n"
        "*Comandos que você pode usar:*\n"
        "/status — ver sua situação e seus dados\n"
        "/editar — corrigir seus dados\n"
        "/ajuda — ver tudo o que dá para fazer\n\n"
        "Na dúvida, é só mandar /ajuda. Bom jogo! 🏆",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aborta o fluxo de cadastro sem salvar nada.

    Limpa ``user_data`` e encerra a conversa. Acionado pelo comando /cancelar.
    """
    context.user_data.clear()
    await update.message.reply_text("Cadastro cancelado. Use /start para recomeçar.")
    return ConversationHandler.END


def build_registro_handler() -> ConversationHandler:
    """Constroi e retorna o ConversationHandler de cadastro/edicao.

    Returns:
        ``ConversationHandler`` com entry points ``/start`` e ``/editar``, tres
        estados (NOME, NOME_PIX, TELEFONE) e fallback ``/cancelar``.
    """
    priv = filters.ChatType.PRIVATE  # cadastro so no privado (em grupo o bot ignora)
    texto = filters.TEXT & ~filters.COMMAND & priv
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start, filters=priv),
            CommandHandler("editar", editar, filters=priv),
        ],
        states={
            NOME: [MessageHandler(texto, receber_nome)],
            NOME_PIX: [
                CommandHandler("igual", nome_pix_igual, filters=priv),
                MessageHandler(texto, receber_nome_pix),
            ],
            TELEFONE: [
                CommandHandler("pular", telefone_pular, filters=priv),
                MessageHandler(texto, receber_telefone),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )
