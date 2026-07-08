"""Testa os parsers de texto do OCR (puros, sem Tesseract).

Os textos reproduzem os LAYOUTS reais de 4 bancos, mas com nomes, chaves e
IDs FICTICIOS — nao usar dados reais de pessoas aqui.
"""
from datetime import date
from decimal import Decimal

from services import ocr

BRADESCO = """Comprovante de transferencia Pix
23/06/2026 - 13:38:21
Valor: R$ 15,38
Dados de quem recebeu
Nome: Renata Souza Lima
Chave Pix: +55 11 98765 4321
Dados de quem fez a transacao
Nome: MARCOS ANTUNES BARBOSA"""

PICPAY = """Comprovante de Pix
11/jun/2026 - 13:50:49
Valor
R$ 23,00
Para
RICARDO ALVES
PEREIRA
De
OTAVIO LIMA
FIGUEIREDO"""

NUBANK = """Comprovante de transferencia
23 JUN 2026 - 08:52:24
Valor R$ 15,00
Destino
Nome Renata Souza Lima
Chave Pix +5511987654321
Origem
Nome Bruno Farias Cardoso Neto"""

C6 = """C6 BANK
RA
RICARDO ALVES PEREIRA
Chave +5511912345678
Valor R$ 12,50
Data e horario da transacao
terca-feira, 02 de junho de 2026, 13:23
Conta de origem
MB
MARCOS ANTUNES BARBOSA"""


def test_valor():
    assert ocr.parse_valor(BRADESCO) == Decimal("15.38")
    assert ocr.parse_valor(PICPAY) == Decimal("23.00")
    assert ocr.parse_valor("RS 9,90") == Decimal("9.90")  # tolera RS do OCR


def test_data_formatos():
    assert ocr.parse_data(BRADESCO) == date(2026, 6, 23)   # dd/mm/aaaa
    assert ocr.parse_data(PICPAY) == date(2026, 6, 11)     # dd/mes/aaaa
    assert ocr.parse_data(NUBANK) == date(2026, 6, 23)     # dd MES aaaa
    assert ocr.parse_data(C6) == date(2026, 6, 2)          # dd de mes de aaaa


def test_chave_digitos():
    assert ocr.parse_chave_digitos(BRADESCO) == "5511987654321"
    assert ocr.parse_chave_digitos(NUBANK) == "5511987654321"
    assert ocr.parse_chave_digitos(C6) == "5511912345678"


def test_transacao_prefere_e2e_sobre_autenticacao():
    texto = ("Autenticação:\n80DC86D6FC97739ED86E65F32F8CED9E3562EA7A\n"
             "ID da transação:\nE607/01190202606252131DY5EK3ZHLO4")
    assert ocr.parse_transacao(texto) == "E60701190202606252131DY5EK3ZHLO4"


def test_transacao_ausente():
    assert ocr.parse_transacao("comprovante sem id") is None


def test_origem_destino():
    assert ocr.parse_origem(BRADESCO) == "MARCOS ANTUNES BARBOSA"
    assert ocr.parse_destino(BRADESCO) == "Renata Souza Lima"
    assert ocr.parse_origem(PICPAY) == "OTAVIO LIMA FIGUEIREDO"
    assert ocr.parse_destino(PICPAY) == "RICARDO ALVES PEREIRA"
    assert ocr.parse_origem(NUBANK) == "Bruno Farias Cardoso Neto"
    assert ocr.parse_destino(C6) == "RICARDO ALVES PEREIRA"  # fallback C6
