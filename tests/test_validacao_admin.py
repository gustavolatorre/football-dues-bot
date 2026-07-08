"""Testa os limites de auto-aprovacao (piso/teto), a fila /pendentes, a
aprovacao pelo admin com valor informado e a protecao contra decisao dupla.
Mensalidade=40, DIA_COBRANCA=10 (conftest).

Invariante central do projeto: dinheiro que chegou nunca se perde — o que nao
auto-aprova fica como ``pendente_admin`` e reaparece no /pendentes ate ser decidido.
"""
from datetime import date
from decimal import Decimal

import pytest

import database.repo as repo_mod
from database import repo
from database.models import Jogador, Pagamento


class _DataFixa(date):
    @classmethod
    def today(cls):
        return date(2026, 6, 25)  # depois do DIA_COBRANCA (10)


def _patch_hoje(monkeypatch):
    monkeypatch.setattr(repo_mod, "date", _DataFixa)


def _seed(tid, nome, data_adesao):
    repo.criar_jogador(tid, nome, nome, None)
    with repo.session_scope() as s:
        s.get(Jogador, tid).data_adesao = data_adesao


def _pagar(tid, valor, transacao=None):
    repo.registrar_pagamento(
        tid, {"valor": Decimal(str(valor)), "data": date(2026, 6, 25), "transacao": transacao},
        f"file-{tid}-{valor}", "auto_aprovado", 10,
    )


# ---------------------------------------------------------------- limites (piso/teto)

def test_limites_novo_do_mes(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(1, "Novo", date(2026, 6, 1))            # deve 1 mes (40)
    piso, teto = repo.limites_validacao(1, 10)
    assert piso == date(2026, 6, 1)               # mes atual sempre pagavel
    assert teto == Decimal("160.00")              # falta 40 + 3 x 40


def test_limites_devedor_piso_no_mes_mais_antigo(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(2, "Devedor", date(2026, 5, 1))         # deve mai+jun (80)
    piso, teto = repo.limites_validacao(2, 10)
    assert piso == date(2026, 5, 1)               # mes mais antigo em aberto
    assert teto == Decimal("200.00")              # falta 80 + 120


def test_limites_meses_pagos_avancam_o_piso(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(3, "Parcialmente Pago", date(2026, 4, 1))   # deve abr+mai+jun (120)
    _pagar(3, 80)                                      # quita abr+mai
    piso, teto = repo.limites_validacao(3, 10)
    assert piso == date(2026, 6, 1)               # abr/mai pagos -> piso vai p/ junho
    assert teto == Decimal("160.00")              # falta 40 + 120


def test_limites_em_dia_ainda_aceita_mes_atual(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(4, "Em Dia", date(2026, 6, 1))          # deve 1 mes
    _pagar(4, 40)                                  # ja pagou junho -> em dia
    piso, teto = repo.limites_validacao(4, 10)
    assert piso == date(2026, 6, 1)               # continua aceitando adiantamento no mes
    assert teto == Decimal("120.00")              # falta 0 + 120


# ---------------------------------------------------------------- pendentes()

def test_pendentes_lista_apenas_pendentes(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(10, "Ana", date(2026, 6, 1))
    _seed(11, "Bruno", date(2026, 6, 1))
    _seed(12, "Carlos", date(2026, 6, 1))
    # dois pendentes e um auto-aprovado
    repo.registrar_pagamento(
        10, {"valor": None, "data": None, "transacao": "T10"}, "f10", "pendente_admin", 10,
    )
    repo.registrar_pagamento(
        11, {"valor": Decimal("40.00"), "data": date(2026, 6, 20), "transacao": "T11"},
        "f11", "pendente_admin", 10, True,  # is_pdf=True
    )
    _pagar(12, 40, transacao="T12")

    itens = repo.pendentes()
    assert len(itens) == 2
    por_jog = {it["jogador_id"]: it for it in itens}
    assert set(por_jog) == {10, 11}
    assert por_jog[11]["is_pdf"] is True
    assert por_jog[11]["valor"] == Decimal("40.00")
    assert por_jog[10]["valor"] is None            # OCR nao leu
    assert por_jog[10]["nome"] == "Ana"


def test_pendentes_vazio(monkeypatch):
    _patch_hoje(monkeypatch)
    assert repo.pendentes() == []


# ---------------------------------------------------------------- decidir com valor

def test_admin_informa_valor_e_aprova(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(20, "Sem Valor", date(2026, 6, 1))       # deve 40
    pid = repo.registrar_pagamento(
        20, {"valor": None, "data": date(2026, 6, 20), "transacao": "TSV"},
        "fsv", "pendente_admin", 10,
    )
    assert repo.situacao(20, 10)["falta"] == Decimal("40.00")  # pendente nao abate
    pg = repo.decidir_pagamento(pid, True, 10, Decimal("40.00"))
    assert pg is not None
    assert repo.situacao(20, 10)["em_dia"] is True             # valor informado abateu


def test_admin_informa_valor_excedente_vira_credito(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(21, "Pagou Mais", date(2026, 6, 1))      # deve 40
    pid = repo.registrar_pagamento(
        21, {"valor": None, "data": date(2026, 6, 20), "transacao": "TPM"},
        "fpm", "pendente_admin", 10,
    )
    repo.decidir_pagamento(pid, True, 10, Decimal("100.00"))   # admin informa 100
    sit = repo.situacao(21, 10)
    assert sit["falta"] == Decimal("0")
    assert sit["credito"] == Decimal("60.00")                  # excedente vira credito


def test_admin_rejeita_nao_abate(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(22, "Rejeitado", date(2026, 6, 1))       # deve 40
    pid = repo.registrar_pagamento(
        22, {"valor": Decimal("40.00"), "data": date(2026, 6, 20), "transacao": "TRJ"},
        "frj", "pendente_admin", 10,
    )
    repo.decidir_pagamento(pid, False, 10)
    assert repo.situacao(22, 10)["falta"] == Decimal("40.00")  # rejeitado nao conta


def test_decidir_duas_vezes_levanta_excecao(monkeypatch):
    """Decisao dupla (2 admins / clique repetido) nao pode reverter nem
    sobrescrever a primeira — a segunda tentativa levanta PagamentoJaDecidido."""
    _patch_hoje(monkeypatch)
    _seed(23, "Decidido", date(2026, 6, 1))
    pid = repo.registrar_pagamento(
        23, {"valor": Decimal("40.00"), "data": date(2026, 6, 20), "transacao": "TDD"},
        "fdd", "pendente_admin", 10,
    )
    repo.decidir_pagamento(pid, True, 10)                  # 1a decisao: aprova
    with pytest.raises(repo.PagamentoJaDecidido):
        repo.decidir_pagamento(pid, False, 10)             # 2a decisao: bloqueada
    assert repo.situacao(23, 10)["em_dia"] is True         # aprovacao original intacta
    with pytest.raises(repo.PagamentoJaDecidido):
        repo.decidir_pagamento(pid, True, 10, Decimal("99.00"))  # nem sobrescrever valor
    with repo.session_scope() as s:
        assert s.get(Pagamento, pid).valor == Decimal("40.00")


def test_decidir_auto_aprovado_levanta_excecao(monkeypatch):
    """Responder um valor a mensagem de confirmacao ('Comprovante #N aprovado...')
    nao pode re-aprovar um pagamento que ja foi auto-aprovado."""
    _patch_hoje(monkeypatch)
    _seed(24, "Auto", date(2026, 6, 1))
    pid = repo.registrar_pagamento(
        24, {"valor": Decimal("40.00"), "data": date(2026, 6, 20), "transacao": "TAA"},
        "faa", "auto_aprovado", 10,
    )
    with pytest.raises(repo.PagamentoJaDecidido):
        repo.decidir_pagamento(pid, True, 10, Decimal("10.00"))


def test_is_pdf_persistido(monkeypatch):
    _patch_hoje(monkeypatch)
    _seed(30, "PDF", date(2026, 6, 1))
    pid = repo.registrar_pagamento(
        30, {"valor": Decimal("40.00"), "data": date(2026, 6, 20), "transacao": "TPDF"},
        "fpdf", "pendente_admin", 10, True,
    )
    with repo.session_scope() as s:
        assert s.get(Pagamento, pid).is_pdf is True
