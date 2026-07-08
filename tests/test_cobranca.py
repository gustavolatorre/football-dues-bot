"""Testa reconciliacao e debito acumulado (mensalidade=40 via conftest).

Nomes de jogadores sao FICTICIOS — nao usar dados reais aqui.
"""
import asyncio
from datetime import date
from decimal import Decimal

import database.repo as repo_mod
from database import repo
from database.models import Jogador
from services import cobranca


class _FakeBot:
    def __init__(self):
        self.enviadas = []

    async def send_message(self, chat_id, text, **kwargs):
        self.enviadas.append((chat_id, text))


class _DataFixa(date):
    @classmethod
    def today(cls):
        return date(2026, 6, 25)  # depois do DIA_COBRANCA (10)


class _Antes(date):
    @classmethod
    def today(cls):
        return date(2026, 6, 5)  # antes do dia 10


def _patch_hoje(monkeypatch, klass):
    monkeypatch.setattr(cobranca, "date", klass)
    monkeypatch.setattr(repo_mod, "date", klass)


def _seed(tid, nome, data_adesao, status="em_dia"):
    repo.criar_jogador(tid, nome, nome, None)
    repo.set_status(tid, status)
    with repo.session_scope() as s:
        s.get(Jogador, tid).data_adesao = data_adesao


def _pagar(tid, valor):
    repo.registrar_pagamento(
        tid, {"valor": Decimal(str(valor)), "data": date(2026, 6, 25)},
        "file", "auto_aprovado", 10,
    )


def test_reconciliar_notifica_e_e_idempotente(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(1, "Marcos Antunes", date(2026, 5, 1))    # deve mai+jun
    _seed(2, "Novato Silva", date(2026, 6, 20))     # entrou no mes -> deve jun

    bot = _FakeBot()
    resumo = asyncio.run(cobranca.reconciliar(bot))

    assert resumo["notificados"] == 2
    assert {c for c, _ in bot.enviadas} == {1, 2}
    assert repo.obter_jogador(1).status == "inadimplente"
    assert repo.obter_jogador(1).ultima_notificacao == date(2026, 6, 25)

    bot2 = _FakeBot()
    asyncio.run(cobranca.reconciliar(bot2))          # idempotente no mesmo dia
    assert bot2.enviadas == []


def test_quem_pagou_nao_e_notificado(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(3, "Pagante Costa", date(2026, 6, 1), status="inadimplente")
    _pagar(3, 40)                                    # deve 1 mes, paga 1 -> em dia

    bot = _FakeBot()
    asyncio.run(cobranca.reconciliar(bot))
    assert bot.enviadas == []
    assert repo.obter_jogador(3).status == "em_dia"


def test_cobrar_forcado_reenvia_mesmo_ja_avisado(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(7, "Ja Avisado", date(2026, 5, 1))
    asyncio.run(cobranca.reconciliar(_FakeBot()))    # 1o aviso do dia
    bot_idem = _FakeBot()
    asyncio.run(cobranca.reconciliar(bot_idem))      # automatico: idempotente
    assert bot_idem.enviadas == []
    bot_forcado = _FakeBot()
    r = asyncio.run(cobranca.reconciliar(bot_forcado, forcar=True))  # manual: reenvia
    assert [c for c, _ in bot_forcado.enviadas] == [7]
    assert r["notificados"] == 1


def test_debito_acumulado_e_pagamento_parcial(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(8, "Devedor", date(2026, 5, 1))            # deve mai+jun = 2 meses
    assert repo.situacao(8, 10)["falta"] == Decimal("80.00")
    _pagar(8, 40)                                    # paga 1 mes -> resta 1
    assert repo.situacao(8, 10)["falta"] == Decimal("40.00")
    assert repo.obter_jogador(8).status == "inadimplente"
    _pagar(8, 40)                                    # paga o restante -> em dia
    assert repo.situacao(8, 10)["em_dia"] is True
    assert repo.obter_jogador(8).status == "em_dia"


def test_paga_dois_meses_de_uma_vez(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(9, "Devedor Duplo", date(2026, 5, 1))      # deve 2 meses = 80
    _pagar(9, 80)                                    # quita os 2 de uma vez
    sit = repo.situacao(9, 10)
    assert sit["em_dia"] is True and sit["falta"] == Decimal("0")
    assert repo.obter_jogador(9).status == "em_dia"


def test_comprovante_duplicado(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(40, "Dup", date(2026, 6, 1))
    repo.registrar_pagamento(
        40, {"valor": Decimal("40.00"), "data": date(2026, 6, 25), "transacao": "EABC123"},
        "file-x", "auto_aprovado", 10,
    )
    assert repo.comprovante_duplicado("EABC123", "outro") is True   # mesma transacao
    assert repo.comprovante_duplicado(None, "file-x") is True        # mesmo arquivo
    assert repo.comprovante_duplicado("EZZZ999", "novo") is False    # inexistente


def test_registrar_pagamento_transacao_duplicada_retorna_none(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(61, "DupTx", date(2026, 6, 1))
    d = {"valor": Decimal("40.00"), "data": date(2026, 6, 25), "transacao": "EDUP999"}
    assert repo.registrar_pagamento(61, d, "f1", "auto_aprovado", 10) is not None
    assert repo.registrar_pagamento(61, d, "f2", "auto_aprovado", 10) is None  # mesma transacao
    # transacao None nao conflita (varios NULLs permitidos no indice unico)
    n = {"valor": Decimal("40.00"), "data": date(2026, 6, 25), "transacao": None}
    assert repo.registrar_pagamento(61, n, "f3", "auto_aprovado", 10) is not None
    assert repo.registrar_pagamento(61, n, "f4", "auto_aprovado", 10) is not None


def test_valor_mensalidade_por_jogador_nao_e_retroativo(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(50, "Travado", date(2026, 5, 1))           # deve 2 meses
    with repo.session_scope() as s:
        s.get(Jogador, 50).valor_mensalidade = Decimal("20.00")  # valor proprio do jogador
    sit = repo.situacao(50, 10)
    assert sit["falta"] == Decimal("40.00")          # 2 x 20, ignora o global (40)


def test_editar_nao_reativa_jogador_desativado(monkeypatch):
    """/editar (criar_jogador em cadastro existente) NAO pode reativar nem
    mexer em desativado_em — senao o jogador se auto-reativa e e cobrado
    retroativamente pelo periodo pausado (bug F1 da auditoria)."""
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(82, "Pausado", date(2026, 2, 1))
    repo.set_ativo(82, False)                              # admin desativa
    repo.criar_jogador(82, "Pausado Editado", "Pausado", "11999990000")  # jogador roda /editar
    jog = repo.obter_jogador(82)
    assert jog.ativo is False                              # continua desativado
    assert jog.desativado_em is not None                   # marcador preservado
    assert jog.nome == "Pausado Editado"                   # edicao dos dados funcionou
    # /reativar depois ainda desconta o periodo parado normalmente
    repo.set_ativo(82, True)
    assert repo.obter_jogador(82).desativado_em is None


def test_reativar_nao_cobra_periodo_parado(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)                    # hoje 2026-06-25
    _seed(80, "Sumido", date(2026, 4, 1))                  # entrou abr (deveria 3 meses)
    with repo.session_scope() as s:
        s.get(Jogador, 80).desativado_em = date(2026, 4, 25)  # ficou parado desde abr
    repo.set_ativo(80, True)                               # reativa hoje
    jog = repo.obter_jogador(80)
    assert jog.data_adesao == date(2026, 6, 1)             # adesao avancou 2 meses (gap)
    assert repo.situacao(80, 10)["meses_atraso"] == 1      # cobra so o mes atual


def test_pagamento_parcial_marca_flag(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(81, "Parcial", date(2026, 6, 1))                 # deve 40
    _pagar(81, 25)                                         # paga 25 -> falta 15
    sit = repo.situacao(81, 10)
    assert sit["falta"] == Decimal("15.00")
    assert sit["parcial"] is True
    assert sit["meses_atraso"] == 0


def test_frase_falta():
    assert cobranca.frase_falta(Decimal("80.00"), 2, False) == "R$ 80,00 (2 meses)"
    assert cobranca.frase_falta(Decimal("40.00"), 1, False) == "R$ 40,00 (1 mês)"
    assert cobranca.frase_falta(Decimal("15.00"), 0, True) == "R$ 15,00"


def test_credito_por_adiantamento(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(30, "Adiantado", date(2026, 6, 1))         # deve 1 mes = 40
    _pagar(30, 100)                                  # paga 100 -> credito 60
    sit = repo.situacao(30, 10)
    assert sit["falta"] == Decimal("0")
    assert sit["credito"] == Decimal("60.00")
    assert repo.obter_jogador(30).status == "em_dia"


def test_atrasado_de_mes_anterior_cobrado_antes_do_vencimento(monkeypatch):
    _patch_hoje(monkeypatch, _Antes)                 # hoje 2026-06-05 (antes do dia 10)
    _seed(5, "Atrasado Maio", date(2026, 5, 1))      # maio ja venceu
    bot = _FakeBot()
    resumo = asyncio.run(cobranca.reconciliar(bot))
    assert resumo["notificados"] == 1
    assert [c for c, _ in bot.enviadas] == [5]


def test_novo_do_mes_nao_e_cobrado_antes_do_vencimento(monkeypatch):
    _patch_hoje(monkeypatch, _Antes)                 # hoje 2026-06-05
    _seed(4, "Novo Junho", date(2026, 6, 1))         # so deve junho, ainda nao venceu
    bot = _FakeBot()
    resumo = asyncio.run(cobranca.reconciliar(bot))
    assert resumo["notificados"] == 0
    assert bot.enviadas == []


def test_relatorio_total_devido_e_devedores(monkeypatch):
    _patch_hoje(monkeypatch, _DataFixa)
    _seed(20, "Deve Dois", date(2026, 5, 1))         # 2 meses = 80
    _seed(21, "Deve Um", date(2026, 6, 1))           # 1 mes = 40
    _seed(22, "Em Dia", date(2026, 6, 1))
    _pagar(22, 40)

    r = repo.relatorio("2026-06", 10)
    assert r["total"] == 3
    assert r["em_dia"] == 1
    assert r["inadimplentes"] == 2
    assert r["total_devido"] == Decimal("120.00")
    assert [d["nome"] for d in r["devedores"]] == ["Deve Dois", "Deve Um"]  # maior primeiro
