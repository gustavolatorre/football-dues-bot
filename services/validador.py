"""Valida o comprovante extraido: origem + destino + valor + data + transacao.

Executa cinco checagens independentes sobre os dados retornados pelo OCR:
valor lido e dentro dos limites, data dentro do periodo (piso..hoje), origem com
similaridade fuzzy ao nome do jogador, destino compativel com a chave PIX ou nome
do recebedor, e ID de transacao legivel (exigido para a deduplicacao funcionar).
Retorna (auto_aprova, falhas) para que o handler decida entre auto-aprovar ou
encaminhar ao admin. O bot nunca descarta um comprovante: o que nao auto-aprova
vai para revisao humana.
"""
import re
from datetime import date
from decimal import Decimal

from rapidfuzz import fuzz

from config import MENSALIDADE_VALOR, NOME_RECEBEDOR, OCR_CONFIANCA_MIN, PIX_DESTINO
from services.normalizacao import normalizar as _norm

# Valor lido abaixo desta fracao da mensalidade e suspeito (ex.: OCR pegou uma
# taxa "1,38" em vez do valor real) -> vai ao admin em vez de auto-aprovar.
FRACAO_VALOR_MINIMO = Decimal("0.25")


def _digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def validar(dados: dict, jogador, piso: date, teto: Decimal,
            hoje: date | None = None) -> tuple[bool, list[str]]:
    """Valida os dados extraidos do comprovante contra o perfil do jogador.

    As cinco checagens realizadas:
    1. **valor**: lido, positivo, ``<= teto`` (divida + N mensalidades) e
       ``>= 25% da mensalidade``. Acima do teto (ex.: OCR com digito a mais) ou
       suspeito de ser um numero errado (muito baixo) -> falha -> admin decide.
    2. **data**: dentro do periodo ``[piso, hoje]`` (piso = 1o dia do mes mais antigo em
       aberto). Aceita pagamento do mes/adiantado; barra comprovante de periodo anterior.
    3. **origem**: similaridade fuzzy (token_sort_ratio >= OCR_CONFIANCA_MIN) entre o
       nome extraido e ``jogador.nome_pix`` (ou ``jogador.nome`` como fallback).
    4. **destino**: a chave PIX extraida contem os ultimos 11 digitos de ``PIX_DESTINO``
       OU o nome do recebedor tem similaridade suficiente com ``NOME_RECEBEDOR``.
    5. **transacao**: ID fim-a-fim lido. Sem ele a deduplicacao nao funciona (reenviar
       a mesma foto gera outro file_id) -> nunca auto-aprova sem transacao.

    Args:
        dados: Dicionario retornado por ``ocr.extrair_dados`` (valor, data, chave,
            origem, destino, transacao).
        jogador: Instancia de ``Jogador`` com ``nome``, ``nome_pix`` e
            ``valor_mensalidade`` (opcional; usa o global como fallback).
        piso: Data minima aceita (1o dia do mes mais antigo em aberto).
        teto: Valor maximo para auto-aprovar (divida + N mensalidades).
        hoje: Data de referencia (teto da data); usa ``date.today()`` se omitida.

    Returns:
        Tupla ``(auto_aprova, falhas)`` onde ``auto_aprova`` e True somente se todas
        as cinco checagens passarem, e ``falhas`` e a lista dos nomes das que nao
        passaram (``"valor"``, ``"data"``, ``"origem"``, ``"destino"``, ``"transacao"``).
    """
    hoje = hoje or date.today()
    falhas = []

    # valor lido, positivo e dentro dos limites: acima do teto = "absurdo";
    # abaixo de 25% da mensalidade = suspeito de leitura errada (ambos -> admin)
    valor = dados.get("valor")
    mens = getattr(jogador, "valor_mensalidade", None) or MENSALIDADE_VALOR
    minimo = mens * FRACAO_VALOR_MINIMO
    if not valor or valor <= 0 or valor > teto or (mens > 0 and valor < minimo):
        falhas.append("valor")

    # data no periodo: do 1o dia do mes mais antigo em aberto ate hoje (nao futura)
    data = dados.get("data")
    if data is None or not (piso <= data <= hoje):
        falhas.append("data")

    origem = dados.get("origem")
    alvo_origem = jogador.nome_pix or jogador.nome
    if not origem or fuzz.token_sort_ratio(_norm(origem), _norm(alvo_origem)) < OCR_CONFIANCA_MIN:
        falhas.append("origem")

    # destino: chave PIX (principal) OU nome do recebedor (fallback)
    alvo_chave = _digitos(PIX_DESTINO)
    chave = dados.get("chave")
    chave_ok = len(alvo_chave) >= 10 and bool(chave) and alvo_chave[-11:] in chave
    destino = dados.get("destino")
    nome_ok = bool(destino) and fuzz.token_sort_ratio(
        _norm(destino), _norm(NOME_RECEBEDOR)) >= OCR_CONFIANCA_MIN
    if not (chave_ok or nome_ok):
        falhas.append("destino")

    # transacao legivel e obrigatoria para auto-aprovar: sem ela, reenviar a
    # mesma foto (novo file_id) contaria o mesmo pagamento duas vezes
    if not dados.get("transacao"):
        falhas.append("transacao")

    return (len(falhas) == 0, falhas)
