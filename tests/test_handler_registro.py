"""Testa o handler de cadastro (/start, /editar, fluxo de conversa, /cancelar).

Chama os handlers async diretamente com fakes (ver fakes_telegram.py); o banco
e o de teste do conftest, limpo a cada teste. Nomes FICTICIOS.
"""
import asyncio

from telegram.ext import ConversationHandler

from database import repo
from handlers import registro
from tests.fakes_telegram import FakeContext, update_texto

END = ConversationHandler.END


def _run(coro):
    return asyncio.run(coro)


def test_start_novo_pede_nome():
    upd = update_texto(10, "/start")
    estado = _run(registro.start(upd, FakeContext()))
    assert estado == registro.NOME
    assert "nome completo" in upd.message.replies[-1]


def test_start_ja_cadastrado_encerra():
    repo.criar_jogador(11, "Marcos Antunes", "Marcos Antunes", None)
    upd = update_texto(11, "/start")
    estado = _run(registro.start(upd, FakeContext()))
    assert estado == END
    assert "já está cadastrado" in upd.message.replies[-1]


def test_editar_sem_cadastro_redireciona():
    upd = update_texto(12, "/editar")
    estado = _run(registro.editar(upd, FakeContext()))
    assert estado == END
    assert "não está cadastrado" in upd.message.replies[-1]


def test_editar_com_cadastro_inicia_fluxo():
    repo.criar_jogador(13, "Marcos Antunes", "Marcos Antunes", None)
    upd = update_texto(13, "/editar")
    estado = _run(registro.editar(upd, FakeContext()))
    assert estado == registro.NOME


def test_fluxo_completo_com_igual_e_pular():
    """nome -> /igual (nome_pix = nome) -> /pular (sem telefone) -> persistido."""
    ctx = FakeContext()

    upd1 = update_texto(14, "Marcos Antunes Barbosa")
    assert _run(registro.receber_nome(upd1, ctx)) == registro.NOME_PIX

    upd2 = update_texto(14, "/igual")
    assert _run(registro.nome_pix_igual(upd2, ctx)) == registro.TELEFONE

    upd3 = update_texto(14, "/pular")
    assert _run(registro.telefone_pular(upd3, ctx)) == END
    assert "Cadastro concluído" in upd3.message.replies[-1]

    jog = repo.obter_jogador(14)
    assert jog is not None
    assert jog.nome == "Marcos Antunes Barbosa"
    assert jog.nome_pix == "Marcos Antunes Barbosa"   # /igual copiou
    assert jog.telefone is None                        # /pular
    assert ctx.user_data == {}                         # estado residual limpo


def test_fluxo_com_nome_pix_proprio_e_telefone():
    ctx = FakeContext()
    _run(registro.receber_nome(update_texto(15, "Marcos Antunes"), ctx))
    _run(registro.receber_nome_pix(update_texto(15, "M A Barbosa"), ctx))
    upd = update_texto(15, "11 99999-0000")
    assert _run(registro.receber_telefone(upd, ctx)) == END

    jog = repo.obter_jogador(15)
    assert jog.nome_pix == "M A Barbosa"
    assert jog.telefone == "11 99999-0000"


def test_cancelar_nao_persiste_nada():
    ctx = FakeContext()
    _run(registro.receber_nome(update_texto(16, "Fulano Incompleto"), ctx))
    upd = update_texto(16, "/cancelar")
    assert _run(registro.cancelar(upd, ctx)) == END
    assert ctx.user_data == {}
    assert repo.obter_jogador(16) is None   # cadastro parcial nao salva


def test_builder_constroi_conversation_handler():
    handler = registro.build_registro_handler()
    assert isinstance(handler, ConversationHandler)
