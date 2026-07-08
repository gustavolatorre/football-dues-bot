"""Testa as 5 checagens do validador (origem, destino, valor, data, transacao).

A auto-aprovacao exige: origem = jogador, destino = grupo, valor lido no intervalo
``[25% da mensalidade, teto]``, data no intervalo ``[piso, hoje]`` e ID de transacao
legivel (sem ele a deduplicacao nao funciona). O que nao passa vai ao admin.

Nomes/chaves sao FICTICIOS (definidos no conftest) — nao usar dados reais aqui.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from services import validador

HOJE = date(2026, 6, 25)
PISO = date(2026, 6, 1)        # 1o dia do mes mais antigo em aberto
TETO = Decimal("120.00")       # divida + 3 mensalidades (ex.: 3 x 40)


def _jogador(nome_pix="Marcos Antunes Barbosa"):
    return SimpleNamespace(nome="Marcos Antunes Barbosa", nome_pix=nome_pix)


def _dados(**over):
    base = {
        "valor": 40.0,
        "data": date(2026, 6, 15),
        "chave": "5511912345678",      # bate com PIX_DESTINO (conftest)
        "origem": "Marcos Antunes Barbosa",
        "destino": "Ricardo Alves Pereira",
        "transacao": "E12345678202606251234ABCDEF",
    }
    base.update(over)
    return base


def test_comprovante_valido_auto_aprova():
    ok, falhas = validador.validar(_dados(), _jogador(), PISO, TETO, HOJE)
    assert ok is True
    assert falhas == []


def test_valor_parcial_e_aceito():
    # pagamento menor que a mensalidade e valido (abate do saldo);
    # 10 = exatamente 25% da mensalidade de 40 -> no limite, passa
    ok, falhas = validador.validar(_dados(valor=10.0), _jogador(), PISO, TETO, HOJE)
    assert "valor" not in falhas


def test_valor_ausente_falha():
    ok, falhas = validador.validar(_dados(valor=None), _jogador(), PISO, TETO, HOJE)
    assert "valor" in falhas


def test_valor_zero_falha():
    ok, falhas = validador.validar(_dados(valor=0), _jogador(), PISO, TETO, HOJE)
    assert "valor" in falhas


def test_valor_absurdo_acima_do_teto_falha():
    # OCR leu um digito a mais -> acima do teto -> vai ao admin informar o valor
    ok, falhas = validador.validar(_dados(valor=400.0), _jogador(), PISO, TETO, HOJE)
    assert "valor" in falhas


def test_valor_suspeito_baixo_falha():
    # OCR pegou um numero errado (ex.: taxa "1,38") -> abaixo de 25% da
    # mensalidade -> admin confere em vez de auto-aprovar valor a menor
    ok, falhas = validador.validar(_dados(valor=1.38), _jogador(), PISO, TETO, HOJE)
    assert "valor" in falhas


def test_valor_no_limite_do_teto_passa():
    ok, falhas = validador.validar(_dados(valor=120.0), _jogador(), PISO, TETO, HOJE)
    assert "valor" not in falhas


def test_data_antes_do_piso_falha():
    ok, falhas = validador.validar(_dados(data=date(2026, 4, 1)), _jogador(), PISO, TETO, HOJE)
    assert "data" in falhas


def test_data_no_piso_passa():
    ok, falhas = validador.validar(_dados(data=PISO), _jogador(), PISO, TETO, HOJE)
    assert "data" not in falhas


def test_data_hoje_passa():
    ok, falhas = validador.validar(_dados(data=HOJE), _jogador(), PISO, TETO, HOJE)
    assert "data" not in falhas


def test_data_futura_falha():
    ok, falhas = validador.validar(_dados(data=date(2026, 7, 1)), _jogador(), PISO, TETO, HOJE)
    assert "data" in falhas


def test_data_ausente_falha():
    ok, falhas = validador.validar(_dados(data=None), _jogador(), PISO, TETO, HOJE)
    assert "data" in falhas


def test_destino_errado_falha():
    ok, falhas = validador.validar(
        _dados(chave="5511000000000", destino="Outra Pessoa"), _jogador(), PISO, TETO, HOJE)
    assert "destino" in falhas


def test_destino_por_nome_quando_sem_chave():
    ok, falhas = validador.validar(_dados(chave=None), _jogador(), PISO, TETO, HOJE)
    assert "destino" not in falhas


def test_origem_diferente_falha():
    ok, falhas = validador.validar(_dados(origem="Joao Ninguem"), _jogador(), PISO, TETO, HOJE)
    assert "origem" in falhas


def test_transacao_ausente_nao_auto_aprova():
    # sem ID de transacao a dedup nao pega reenvio da mesma foto -> admin decide
    ok, falhas = validador.validar(_dados(transacao=None), _jogador(), PISO, TETO, HOJE)
    assert ok is False
    assert falhas == ["transacao"]


def test_mensalidade_do_jogador_define_o_valor_minimo():
    # jogador com mensalidade propria de 20 -> minimo 5; valor 6 passa
    jog = SimpleNamespace(nome="Marcos Antunes Barbosa",
                          nome_pix="Marcos Antunes Barbosa",
                          valor_mensalidade=Decimal("20.00"))
    ok, falhas = validador.validar(_dados(valor=6.0), jog, PISO, TETO, HOJE)
    assert "valor" not in falhas
