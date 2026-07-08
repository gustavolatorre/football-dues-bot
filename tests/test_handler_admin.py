"""Testa os handlers do admin: callbacks de decisão, informar valor por reply,
gestão de jogadores (com confirmação do /remover), relatório, cobrança e /pendentes.

Admin de teste = id 999 (conftest); 555 é um usuário comum. Nomes FICTICIOS.
"""
import asyncio
from datetime import date
from decimal import Decimal

from database import repo
from database.models import Jogador
from handlers import admin
from tests.fakes_telegram import (
    FakeCallbackQuery,
    FakeContext,
    FakeMessage,
    FakeUpdate,
    update_texto,
)

ADMIN = 999
COMUM = 555


def _run(coro):
    return asyncio.run(coro)


def _seed_jogador(tid=1, nome="Marcos Antunes"):
    repo.criar_jogador(tid, nome, nome, None)


def _seed_pendente(tid=1, transacao="THADM1234567890ABCD"):
    return repo.registrar_pagamento(
        tid, {"valor": Decimal("40.00"), "data": date.today(), "transacao": transacao},
        f"file-{transacao}", "pendente_admin", 10,
    )


def _backdate_adesao_mes_passado(tid):
    hoje = date.today()
    alvo = date(hoje.year - 1, 12, 1) if hoje.month == 1 else date(hoje.year, hoje.month - 1, 1)
    with repo.session_scope() as s:
        s.get(Jogador, tid).data_adesao = alvo


# ------------------------------------------------------------ callback aprovar/rejeitar

def test_callback_aprovar_atualiza_e_avisa_jogador():
    _seed_jogador(1)
    pid = _seed_pendente(1)
    query = FakeCallbackQuery(f"aprovar:{pid}", FakeUpdate(ADMIN).effective_user)
    ctx = FakeContext()
    _run(admin.callback_decisao(FakeUpdate(ADMIN, callback_query=query), ctx))

    assert repo.obter_pagamento(pid).status_validacao == "aprovado"
    assert any("APROVADO" in c for c in query.captions)          # legenda editada
    assert any("aprovado" in t for t in ctx.bot.enviados_para(1))  # jogador avisado


def test_callback_rejeitar_atualiza_e_avisa_jogador():
    _seed_jogador(2)
    pid = _seed_pendente(2, transacao="THADM2REJ34567890ABC")
    query = FakeCallbackQuery(f"rejeitar:{pid}", FakeUpdate(ADMIN).effective_user)
    ctx = FakeContext()
    _run(admin.callback_decisao(FakeUpdate(ADMIN, callback_query=query), ctx))

    assert repo.obter_pagamento(pid).status_validacao == "rejeitado"
    assert any("não foi aprovado" in t for t in ctx.bot.enviados_para(2))


def test_callback_nao_admin_bloqueado():
    _seed_jogador(3)
    pid = _seed_pendente(3, transacao="THADM3NAO4567890ABCD")
    query = FakeCallbackQuery(f"aprovar:{pid}", FakeUpdate(COMUM).effective_user)
    _run(admin.callback_decisao(FakeUpdate(COMUM, callback_query=query), FakeContext()))

    assert repo.obter_pagamento(pid).status_validacao == "pendente_admin"  # intacto
    texto, alerta = query.answers[-1]
    assert "administradores" in texto and alerta is True


def test_callback_segunda_decisao_bloqueada():
    """F3 fim a fim: clique repetido/segundo admin nao reverte a decisao."""
    _seed_jogador(4)
    pid = _seed_pendente(4, transacao="THADM4DUP567890ABCDE")
    repo.decidir_pagamento(pid, True, 10)                        # 1a decisao
    query = FakeCallbackQuery(f"rejeitar:{pid}", FakeUpdate(ADMIN).effective_user)
    _run(admin.callback_decisao(FakeUpdate(ADMIN, callback_query=query), FakeContext()))

    assert repo.obter_pagamento(pid).status_validacao == "aprovado"  # nao reverteu
    texto, alerta = query.answers[-1]
    assert "já foi decidido" in texto and alerta is True


# ------------------------------------------------------------ informar valor (ForceReply)

def _update_reply_valor(pid, texto_valor, user=ADMIN):
    prompt = FakeMessage(text=f"Comprovante #{pid} — digite o valor que caiu na conta "
                              f"(ex.: 40 ou 40,00):")
    return FakeUpdate(user, message=FakeMessage(text=texto_valor, reply_to_message=prompt))


def test_setvalor_pede_valor_com_force_reply():
    _seed_jogador(5)
    pid = _seed_pendente(5, transacao="THADM5SET67890ABCDEF")
    query = FakeCallbackQuery(f"setvalor:{pid}", FakeUpdate(ADMIN).effective_user)
    _run(admin.callback_setvalor(FakeUpdate(ADMIN, callback_query=query), FakeContext()))
    assert any(f"Comprovante #{pid}" in r for r in query.message.replies)


def test_valor_por_reply_aprova_com_valor_informado():
    _seed_jogador(6)
    pid = _seed_pendente(6, transacao="THADM6VAL7890ABCDEFG")
    upd = _update_reply_valor(pid, "50")
    ctx = FakeContext()
    _run(admin.receber_valor_admin(upd, ctx))

    pg = repo.obter_pagamento(pid)
    assert pg.status_validacao == "aprovado"
    assert pg.valor == Decimal("50")
    assert any("R$ 50,00" in r for r in upd.message.replies)
    assert any("aprovado" in t for t in ctx.bot.enviados_para(6))


def test_valor_por_reply_invalido_pede_de_novo():
    _seed_jogador(7)
    pid = _seed_pendente(7, transacao="THADM7INV890ABCDEFGH")
    upd = _update_reply_valor(pid, "abc")
    _run(admin.receber_valor_admin(upd, FakeContext()))
    assert "Valor inválido" in upd.message.replies[-1]
    assert repo.obter_pagamento(pid).status_validacao == "pendente_admin"


def test_reply_a_mensagem_qualquer_e_ignorado():
    upd = FakeUpdate(ADMIN, message=FakeMessage(
        text="40", reply_to_message=FakeMessage(text="bom dia, grupo")))
    _run(admin.receber_valor_admin(upd, FakeContext()))
    assert upd.message.replies == []          # nao responde nada


def test_valor_por_reply_em_pagamento_ja_decidido():
    """Responder a confirmacao antiga nao re-aprova nem sobrescreve valor."""
    _seed_jogador(8)
    pid = _seed_pendente(8, transacao="THADM8JAD90ABCDEFGHI")
    repo.decidir_pagamento(pid, True, 10, Decimal("40.00"))
    upd = _update_reply_valor(pid, "99")
    _run(admin.receber_valor_admin(upd, FakeContext()))
    assert any("já foi decidido" in r for r in upd.message.replies)
    assert repo.obter_pagamento(pid).valor == Decimal("40.00")   # intacto


# ------------------------------------------------------------ gestao de jogadores

def test_remover_sem_confirmacao_so_avisa():
    _seed_jogador(30, "Para Remover")
    upd = update_texto(ADMIN, "/remover 30")
    _run(admin.remover(upd, FakeContext(args=["30"])))
    assert "confirmar" in upd.message.replies[-1]
    assert repo.obter_jogador(30) is not None      # nada apagado


def test_remover_com_sim_exclui():
    _seed_jogador(31, "Vai Embora")
    upd = update_texto(ADMIN, "/remover 31 sim")
    _run(admin.remover(upd, FakeContext(args=["31", "sim"])))
    assert "removido definitivamente" in upd.message.replies[-1]
    assert repo.obter_jogador(31) is None


def test_desativar_e_reativar():
    _seed_jogador(32)
    upd1 = update_texto(ADMIN, "/desativar 32")
    _run(admin.desativar(upd1, FakeContext(args=["32"])))
    assert repo.obter_jogador(32).ativo is False

    upd2 = update_texto(ADMIN, "/reativar 32")
    _run(admin.reativar(upd2, FakeContext(args=["32"])))
    assert repo.obter_jogador(32).ativo is True


def test_gerenciar_id_inexistente():
    upd = update_texto(ADMIN, "/desativar 424242")
    _run(admin.desativar(upd, FakeContext(args=["424242"])))
    assert "não encontrado" in upd.message.replies[-1]


def test_gerenciar_sem_id_mostra_uso():
    upd = update_texto(ADMIN, "/desativar")
    _run(admin.desativar(upd, FakeContext(args=[])))
    assert "Uso:" in upd.message.replies[-1]


def test_gerenciar_nao_admin_restrito():
    _seed_jogador(33)
    upd = update_texto(COMUM, "/remover 33 sim")
    _run(admin.remover(upd, FakeContext(args=["33", "sim"])))
    assert "restrito" in upd.message.replies[-1]
    assert repo.obter_jogador(33) is not None


# ------------------------------------------------------------ consultas e cobranca

def test_ajuda_mostra_secao_admin_so_para_admin():
    upd_admin = update_texto(ADMIN, "/ajuda")
    _run(admin.ajuda(upd_admin, FakeContext()))
    assert "Admin" in upd_admin.message.replies[-1]

    upd_comum = update_texto(COMUM, "/ajuda")
    _run(admin.ajuda(upd_comum, FakeContext()))
    assert "Admin" not in upd_comum.message.replies[-1]


def test_status_sem_cadastro_e_com_cadastro():
    upd1 = update_texto(40, "/status")
    _run(admin.status(upd1, FakeContext()))
    assert "não está cadastrado" in upd1.message.replies[-1]

    _seed_jogador(40, "Consulta Silva")
    upd2 = update_texto(40, "/status")
    _run(admin.status(upd2, FakeContext()))
    assert "Consulta Silva" in upd2.message.replies[-1]
    assert "Situação" in upd2.message.replies[-1]


def test_relatorio_restrito_e_render():
    upd_comum = update_texto(COMUM, "/relatorio")
    _run(admin.relatorio(upd_comum, FakeContext()))
    assert "restrito" in upd_comum.message.replies[-1]

    _seed_jogador(41)
    upd = update_texto(ADMIN, "/relatorio")
    _run(admin.relatorio(upd, FakeContext()))
    assert "Relatório" in upd.message.replies[-1]
    assert "Mensalistas ativos: 1" in upd.message.replies[-1]


def test_jogadores_lista_ativos_e_inativos():
    _seed_jogador(42, "Ativo Um")
    _seed_jogador(43, "Inativo Dois")
    repo.set_ativo(43, False)
    upd = update_texto(ADMIN, "/jogadores")
    _run(admin.jogadores(upd, FakeContext()))
    texto = upd.message.replies[-1]
    assert "Ativo Um" in texto
    assert "Inativo Dois (inativo)" in texto


def test_cobrar_dispara_reconciliacao():
    _seed_jogador(44, "Devedor Handler")
    _backdate_adesao_mes_passado(44)              # deve pelo menos 1 mes
    upd = update_texto(ADMIN, "/cobrar")
    ctx = FakeContext()
    _run(admin.cobrar(upd, ctx))
    assert "Cobrança disparada" in upd.message.replies[-1]
    assert any("mensalidade em aberto" in t for t in ctx.bot.enviados_para(44))


def test_callback_cobrar_todos():
    _seed_jogador(45, "Devedor Botao")
    _backdate_adesao_mes_passado(45)
    query = FakeCallbackQuery("cobrar_todos", FakeUpdate(ADMIN).effective_user)
    ctx = FakeContext()
    _run(admin.callback_cobrar_todos(FakeUpdate(ADMIN, callback_query=query), ctx))
    assert any("Cobrança disparada" in r for r in query.message.replies)
    assert any("mensalidade em aberto" in t for t in ctx.bot.enviados_para(45))


def test_pendentes_vazio_e_com_itens():
    upd1 = update_texto(ADMIN, "/pendentes")
    _run(admin.cmd_pendentes(upd1, FakeContext()))
    assert "Nenhum comprovante pendente" in upd1.message.replies[-1]

    _seed_jogador(46)
    _seed_pendente(46, transacao="THADM46A567890ABCDEF")
    repo.registrar_pagamento(
        46, {"valor": None, "data": None, "transacao": "THADM46B567890ABCDEF"},
        "file-pdf-46", "pendente_admin", 10, True,   # is_pdf
    )
    upd2 = update_texto(ADMIN, "/pendentes")
    ctx = FakeContext()
    _run(admin.cmd_pendentes(upd2, ctx))
    assert "2 comprovante(s)" in upd2.message.replies[0]
    tipos = [t for t, cid, _ in ctx.bot.sent if cid == ADMIN]
    assert tipos.count("photo") == 1
    assert tipos.count("document") == 1


def test_builder_lista_todos_os_handlers():
    assert len(admin.build_admin_handlers()) == 13
