"""Testa o handler de comprovante fim a fim (com OCR mockado).

Cobre: nao cadastrado, mensagem sem arquivo, arquivo grande, auto-aprovacao,
encaminhamento ao admin (incl. PDF e falha de OCR) e deduplicacao.
O admin de teste e o id 999 (conftest). Nomes/chaves FICTICIOS (conftest).
"""
import asyncio
from datetime import date
from decimal import Decimal

import services.ocr as ocr_mod
from database import repo
from handlers import comprovante
from tests.fakes_telegram import (
    FakeContext,
    FakeMessage,
    FakeUpdate,
    update_foto,
    update_pdf,
)

ADMIN = 999


def _run(coro):
    return asyncio.run(coro)


def _seed_jogador(tid=20, nome="Marcos Antunes Barbosa"):
    return repo.criar_jogador(tid, nome, nome, None)


def _dados_ok(transacao="EHANDLER12345678AB90"):
    """Dados de OCR que passam nas 5 checagens (batem com o conftest)."""
    return {"texto": "x", "valor": Decimal("40.00"), "data": date.today(),
            "chave": "5511912345678", "origem": "Marcos Antunes Barbosa",
            "destino": "Ricardo Alves Pereira", "transacao": transacao}


def _mock_ocr(monkeypatch, dados):
    monkeypatch.setattr(ocr_mod, "extrair_dados", lambda path, is_pdf: dados)


def _status_pagamentos(tid):
    from database.models import Pagamento
    with repo.session_scope() as s:
        return [p.status_validacao for p in
                s.query(Pagamento).filter_by(jogador_id=tid).all()]


def test_nao_cadastrado_orienta_start(monkeypatch):
    upd = update_foto(77)
    _run(comprovante.receber_comprovante(upd, FakeContext()))
    assert "não está cadastrado" in upd.message.replies[-1]


def test_mensagem_sem_foto_nem_pdf():
    _seed_jogador(20)
    upd = FakeUpdate(20, message=FakeMessage(text="paguei!"))
    _run(comprovante.receber_comprovante(upd, FakeContext()))
    assert "foto" in upd.message.replies[-1]
    assert _status_pagamentos(20) == []


def test_arquivo_grande_rejeitado():
    _seed_jogador(21)
    upd = update_foto(21)
    ctx = FakeContext()
    ctx.bot._file_size = 20 * 1024 * 1024   # 20 MB > limite de 10 MB
    _run(comprovante.receber_comprovante(upd, ctx))
    assert "muito grande" in upd.message.replies[-1]
    assert _status_pagamentos(21) == []


def test_comprovante_valido_auto_aprova(monkeypatch):
    _seed_jogador(22)
    _mock_ocr(monkeypatch, _dados_ok())
    upd = update_foto(22)
    _run(comprovante.receber_comprovante(upd, FakeContext()))
    assert "confirmado! ✅" in upd.message.replies[-1]
    assert _status_pagamentos(22) == ["auto_aprovado"]


def test_sem_transacao_vai_ao_admin(monkeypatch):
    """F2 fim a fim: sem ID de transacao nao auto-aprova; admin recebe a foto."""
    _seed_jogador(23)
    _mock_ocr(monkeypatch, _dados_ok(transacao=None))
    upd = update_foto(23)
    ctx = FakeContext()
    _run(comprovante.receber_comprovante(upd, ctx))
    assert "admin vai conferir" in upd.message.replies[-1]
    assert _status_pagamentos(23) == ["pendente_admin"]
    envios_admin = ctx.bot.enviados_para(ADMIN)
    assert len(envios_admin) == 1
    assert "transacao" in envios_admin[0]        # motivo na legenda
    assert ctx.bot.sent[-1][0] == "photo"        # foto reenviada ao admin


def test_pdf_pendente_reenviado_como_documento(monkeypatch):
    _seed_jogador(24)
    _mock_ocr(monkeypatch, _dados_ok(transacao=None))
    upd = update_pdf(24)
    ctx = FakeContext()
    _run(comprovante.receber_comprovante(upd, ctx))
    assert _status_pagamentos(24) == ["pendente_admin"]
    assert ctx.bot.sent[-1][0] == "document"     # PDF vai como documento


def test_ocr_quebrado_nunca_descarta(monkeypatch):
    """Se o OCR explode, o comprovante vira pendente_admin (nada se perde)."""
    _seed_jogador(25)

    def boom(path, is_pdf):
        raise RuntimeError("ocr quebrou")

    monkeypatch.setattr(ocr_mod, "extrair_dados", boom)
    upd = update_foto(25)
    ctx = FakeContext()
    _run(comprovante.receber_comprovante(upd, ctx))
    assert _status_pagamentos(25) == ["pendente_admin"]
    assert len(ctx.bot.enviados_para(ADMIN)) == 1


def test_mesma_transacao_nao_conta_duas_vezes(monkeypatch):
    """Reenvio do mesmo comprovante (mesma transacao, outro arquivo) e barrado."""
    _seed_jogador(26)
    _mock_ocr(monkeypatch, _dados_ok(transacao="EDUPHANDLER123456789"))
    upd1 = update_foto(26, file_id="arquivo-a")
    _run(comprovante.receber_comprovante(upd1, FakeContext()))
    upd2 = update_foto(26, file_id="arquivo-b")   # novo upload, novo file_id
    _run(comprovante.receber_comprovante(upd2, FakeContext()))
    assert "já foi registrado" in upd2.message.replies[-1]
    assert _status_pagamentos(26) == ["auto_aprovado"]   # so 1 registro


def test_builder_constroi_message_handler():
    from telegram.ext import MessageHandler
    assert isinstance(comprovante.build_comprovante_handler(), MessageHandler)
