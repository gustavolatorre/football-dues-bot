"""Recebe foto/PDF, roda OCR, valida e auto-aprova ou encaminha ao admin.

Fluxo principal: download do arquivo -> extracao de texto (OCR) -> deduplicacao
-> validacao dos 5 campos (valor, data, origem, destino, transacao) -> registro
no banco e feedback ao jogador. Comprovantes com falha vao para o admin com
botoes Aprovar/Rejeitar.
"""
import asyncio
import logging
import os
import tempfile

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, MessageHandler, filters

from config import ADMIN_IDS, DIA_COBRANCA, TAMANHO_MAX_ARQUIVO
from database import repo
from services import ocr, validador
from services.cobranca import fmt_brl, frase_falta

log = logging.getLogger(__name__)

_DADOS_VAZIO = {"texto": "", "valor": None, "data": None, "chave": None,
                "origem": None, "destino": None, "transacao": None}


def _extrair_arquivo(msg):
    """Extrai o file_id e indica se e PDF a partir de uma mensagem do Telegram.

    Args:
        msg: Objeto ``Message`` do python-telegram-bot.

    Returns:
        Tupla ``(file_id, is_pdf)``. ``file_id`` e ``None`` se a mensagem nao
        contiver foto nem documento reconhecido (imagem ou PDF).
    """
    if msg.photo:
        return msg.photo[-1].file_id, False
    doc = msg.document
    if doc:
        mime = (doc.mime_type or "").lower()
        nome = (doc.file_name or "").lower()
        if "pdf" in mime or nome.endswith(".pdf"):
            return doc.file_id, True
        if mime.startswith("image/") or nome.endswith((".jpg", ".jpeg", ".png")):
            return doc.file_id, False
    return None, False


async def receber_comprovante(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o comprovante enviado pelo jogador (foto ou PDF).

    Etapas:
    1. Verifica se o jogador esta cadastrado.
    2. Extrai e baixa o arquivo (com verificacao de tamanho).
    3. Roda o OCR (em thread separada para nao bloquear o event loop).
    4. Verifica duplicidade por transacao/file_id.
    5. Valida os 5 campos via ``validador.validar`` (incl. transacao legivel).
    6. Auto-aprova se todos os campos batem; caso contrario, registra como
       ``"pendente_admin"`` e encaminha o comprovante aos admins com botoes.
    7. Envia feedback de saldo ao jogador apos aprovacao automatica.
    """
    uid = update.effective_user.id
    jog = await asyncio.to_thread(repo.obter_jogador, uid)
    if jog is None:
        await update.message.reply_text("Você ainda não está cadastrado. Use /start primeiro.")
        return

    msg = update.message
    file_id, is_pdf = _extrair_arquivo(msg)
    if file_id is None:
        await msg.reply_text("Envie o comprovante como *foto* ou *PDF*.", parse_mode="Markdown")
        return

    tg_file = await context.bot.get_file(file_id)
    if tg_file.file_size and tg_file.file_size > TAMANHO_MAX_ARQUIVO:
        await msg.reply_text("Arquivo muito grande. Envie um comprovante menor (até 10 MB).")
        return

    await msg.reply_text("Recebi seu comprovante, analisando... ⏳")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf" if is_pdf else ".jpg")
    tmp.close()
    try:
        await tg_file.download_to_drive(tmp.name)
        dados = await asyncio.to_thread(ocr.extrair_dados, tmp.name, is_pdf)
    except Exception as e:  # OCR/IO falhou -> trata como dados vazios (vai pro admin)
        log.exception("erro ao processar comprovante: %s", e)
        dados = dict(_DADOS_VAZIO)
    finally:
        _remover(tmp.name)

    # dedup: o mesmo comprovante (transacao ou arquivo) nao conta duas vezes
    if await asyncio.to_thread(repo.comprovante_duplicado, dados.get("transacao"), file_id):
        await msg.reply_text("Esse comprovante já foi registrado. 👍")
        return

    piso, teto = await asyncio.to_thread(repo.limites_validacao, uid, DIA_COBRANCA)
    ok, falhas = validador.validar(dados, jog, piso, teto)
    pago_fmt = fmt_brl(dados.get("valor") or 0)
    if ok:
        novo = await asyncio.to_thread(
            repo.registrar_pagamento, uid, dados, file_id, "auto_aprovado", DIA_COBRANCA, is_pdf
        )
        if novo is None:  # transacao duplicada (corrida) -> dedup atomico
            await msg.reply_text("Esse comprovante já foi registrado. 👍")
            return
        primeiro = jog.nome.split()[0] if jog.nome else "jogador"
        depois = await asyncio.to_thread(repo.situacao, uid, DIA_COBRANCA)
        if depois["falta"] > 0:
            falta_txt = frase_falta(depois["falta"], depois["meses_atraso"], depois["parcial"])
            await msg.reply_text(
                f"Pagamento de R$ {pago_fmt} confirmado! ✅\n"
                f"Ainda faltam {falta_txt}. Envie outro comprovante para quitar o restante."
            )
        elif depois["credito"] > 0:
            c = fmt_brl(depois["credito"])
            await msg.reply_text(
                f"Pagamento de R$ {pago_fmt} confirmado! ✅ Você está em dia, {primeiro}!\n"
                f"Ficou com crédito de R$ {c} para o próximo mês."
            )
        else:
            await msg.reply_text(
                f"Pagamento de R$ {pago_fmt} confirmado! ✅ Você está em dia. Valeu, {primeiro}!"
            )
    else:
        # nunca descarta: tudo que nao auto-aprova vai para o admin conferir
        pid = await asyncio.to_thread(
            repo.registrar_pagamento, uid, dados, file_id, "pendente_admin", DIA_COBRANCA, is_pdf
        )
        if pid is None:  # transacao duplicada (corrida)
            await msg.reply_text("Esse comprovante já foi registrado. 👍")
            return
        await msg.reply_text("Recebido! O admin vai conferir e confirmar. 👍")
        await _encaminhar_admin(context.bot, jog, dados, falhas, pid, file_id, is_pdf, teto)


def _valor_txt(valor, teto) -> str:
    """Texto do valor para a legenda do admin (nao lido / suspeito / R$)."""
    if valor is None or valor <= 0:
        return "não lido"
    txt = f"R$ {fmt_brl(valor)}"
    return f"{txt} (suspeito)" if valor > teto else txt


def _teclado_revisao(pid: int, valor, teto) -> InlineKeyboardMarkup:
    """Botoes de revisao. Se o valor e utilizavel (lido, >0, <=teto) permite aprovar
    direto; senao obriga o admin a informar o valor antes de aprovar."""
    valor_ok = valor is not None and 0 < valor <= teto
    if valor_ok:
        vtxt = fmt_brl(valor)
        linhas = [
            [InlineKeyboardButton(f"✅ Aprovar (R$ {vtxt})", callback_data=f"aprovar:{pid}"),
             InlineKeyboardButton("✏️ Corrigir valor", callback_data=f"setvalor:{pid}")],
            [InlineKeyboardButton("❌ Rejeitar", callback_data=f"rejeitar:{pid}")],
        ]
    else:
        linhas = [
            [InlineKeyboardButton("✏️ Informar valor e aprovar", callback_data=f"setvalor:{pid}")],
            [InlineKeyboardButton("❌ Rejeitar", callback_data=f"rejeitar:{pid}")],
        ]
    return InlineKeyboardMarkup(linhas)


async def enviar_revisao(bot, *, jog_nome, jog_id, pid, file_id, is_pdf, valor, origem,
                         destino, chave, data, teto, motivo) -> None:
    """Envia (ou reenvia, via /pendentes) um comprovante aos admins com os botoes."""
    legenda = (
        "⚠️ Comprovante para revisão\n"
        f"Jogador: {jog_nome} (id {jog_id})\n"
        f"{motivo}\n"
        f"Valor: {_valor_txt(valor, teto)} | Data: {data}\n"
        f"Origem: {origem}\n"
        f"Destino: {destino} | chave: {chave}"
    )
    teclado = _teclado_revisao(pid, valor, teto)
    for admin_id in ADMIN_IDS:
        try:
            if is_pdf:
                await bot.send_document(admin_id, file_id, caption=legenda, reply_markup=teclado)
            else:
                await bot.send_photo(admin_id, file_id, caption=legenda, reply_markup=teclado)
        except Exception as e:
            log.warning("falha ao enviar revisao ao admin %s: %s", admin_id, e)


async def _encaminhar_admin(bot, jog, dados, falhas, pid, file_id, is_pdf, teto) -> None:
    """Encaminha um comprovante recém-recebido (com as falhas do validador) aos admins."""
    await enviar_revisao(
        bot, jog_nome=jog.nome, jog_id=jog.telegram_id, pid=pid, file_id=file_id,
        is_pdf=is_pdf, valor=dados.get("valor"), origem=dados.get("origem"),
        destino=dados.get("destino"), chave=dados.get("chave"), data=dados.get("data"),
        teto=teto, motivo=f"Não bateu: {', '.join(falhas) or '—'}",
    )


def _remover(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def build_comprovante_handler() -> MessageHandler:
    """Constroi o MessageHandler que captura fotos e documentos (imagem/PDF).

    Returns:
        ``MessageHandler`` configurado para acionar ``receber_comprovante`` em
        mensagens com foto, imagem como documento ou PDF.
    """
    return MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF)
        & filters.ChatType.PRIVATE,  # so no privado: em grupos o bot nao responde a fotos
        receber_comprovante,
    )
