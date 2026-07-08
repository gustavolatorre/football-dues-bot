"""Testa o parser de valor digitado pelo admin (pt-BR)."""
from decimal import Decimal

from handlers.admin import _parse_valor


def test_inteiro_simples():
    assert _parse_valor("40") == Decimal("40")


def test_virgula_decimal():
    assert _parse_valor("40,00") == Decimal("40.00")


def test_com_prefixo_moeda():
    assert _parse_valor("R$ 40,00") == Decimal("40.00")


def test_ponto_milhar_e_virgula_decimal():
    assert _parse_valor("1.234,50") == Decimal("1234.50")


def test_ponto_decimal_sem_virgula():
    assert _parse_valor("40.50") == Decimal("40.50")


def test_texto_invalido_retorna_none():
    assert _parse_valor("abc") is None
    assert _parse_valor("") is None
    assert _parse_valor(None) is None


def test_zero_ou_negativo_retorna_none():
    assert _parse_valor("0") is None
    assert _parse_valor("0,00") is None
