"""Extracao de dados de comprovantes PIX (PDF/imagem).

Os parsers de texto (parse_*) sao puros e calibrados a 100% nas 18 amostras
reais (ver samples/CALIBRACAO_OCR.md). As funcoes de IO (pdfplumber/Tesseract)
usam import tardio para que os parsers possam ser testados sem essas libs.
"""
import re
from datetime import date
from decimal import Decimal

from services.normalizacao import normalizar as _norm

# Config do Tesseract: bloco unico de texto, preservando espacos entre palavras
_TESS_CONFIG = "--psm 6 -c preserve_interword_spaces=1"

# Comprovante real tem 1 pagina. Limitar evita DoS: um PDF de 10 MB pode ter
# milhares de paginas e estourar memoria/CPU no render (pdf2image/pdfplumber).
MAX_PAGINAS_PDF = 3


def _linhas(texto: str) -> list[str]:
    return [ln.strip() for ln in (texto or "").splitlines() if ln.strip()]


MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


# --------------------------------------------------------------------------
# VALOR (tolera "R$" e "RS" do OCR)
# --------------------------------------------------------------------------
RE_VALOR = re.compile(r"R[S$]\s*(\d{1,3}(?:\.\d{3})*,\d{2})")
RE_VALOR_FALLBACK = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")


def parse_valor(texto: str):
    """Extrai o primeiro valor monetario encontrado no texto do comprovante.

    Tenta primeiro o padrao ancorado em "R$"/"RS"; se nao encontrar, usa fallback
    no primeiro numero no formato "0,00" (cobre OCR que distorce o cifrao, ex.: "A$0,38").

    Args:
        texto: Texto bruto extraido do comprovante (OCR ou PDF).

    Returns:
        ``Decimal`` com o valor extraido, ou ``None`` se nenhum valor for encontrado.
    """
    # primario ancorado em "R$"/"RS"; fallback no 1o numero "0,00"
    # (cobre OCR que estraga o cifrao, ex.: "A$0,38")
    m = RE_VALOR.search(texto or "") or RE_VALOR_FALLBACK.search(texto or "")
    if not m:
        return None
    return Decimal(m.group(1).replace(".", "").replace(",", "."))


# --------------------------------------------------------------------------
# DATA (4 formatos; pega a mais ao topo)
# --------------------------------------------------------------------------
RE_DATAS = [
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
    re.compile(r"(\d{1,2})/([a-zA-Zç]{3,9})/(\d{4})"),
    re.compile(r"(\d{1,2})\s+de\s+([a-zA-ZçÇ]+)\s+de\s+(\d{4})"),
    re.compile(r"(\d{1,2})\s+([A-Za-zçÇ]{3,4})\s+(\d{4})"),
]


def _mk_date(d, m, y):
    try:
        if isinstance(m, str) and not m.isdigit():
            m = MESES.get(_norm(m)) or MESES.get(_norm(m)[:3])
            if not m:
                return None
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def parse_data(texto: str):
    """Extrai a data mais ao topo do comprovante dentre os 4 formatos suportados.

    Formatos reconhecidos: ``DD/MM/AAAA``, ``DD/Mon/AAAA``, ``DD de Mes de AAAA``
    e ``DD Mon AAAA``. Datas invalidas (ex.: dia 0) sao ignoradas silenciosamente.

    Args:
        texto: Texto bruto do comprovante.

    Returns:
        Objeto ``date`` da primeira data valida encontrada (por posicao no texto),
        ou ``None`` se nenhuma data puder ser interpretada.
    """
    candidatos = []
    for rgx in RE_DATAS:
        for m in rgx.finditer(texto or ""):  # todas as datas; ignora invalidas (ex.: 02/00/2026)
            dt = _mk_date(*m.groups())
            if dt:
                candidatos.append((m.start(), dt))
    if not candidatos:
        return None
    return min(candidatos, key=lambda x: x[0])[1]


# --------------------------------------------------------------------------
# CHAVE PIX (telefone) -> digitos. Sinal de destino mais confiavel.
# --------------------------------------------------------------------------
RE_CHAVE_TEL = re.compile(r"\+?\s*55\s*\(?\d{2}\)?\s*\d{4,5}[-\s]?\d{4}")


def parse_chave_digitos(texto: str):
    """Extrai apenas os digitos da chave PIX de telefone encontrada no texto.

    Reconhece numeros no padrao brasileiro ``+55 (DDD) NNNNN-NNNN`` com variacoes
    de espacos e hifens que o OCR costuma introduzir.

    Args:
        texto: Texto bruto do comprovante.

    Returns:
        String com somente os digitos da chave (ex.: ``"5511987654321"``),
        ou ``None`` se nenhuma chave for encontrada.
    """
    m = RE_CHAVE_TEL.search(texto or "")
    return re.sub(r"\D", "", m.group(0)) if m else None


# ID fim-a-fim do PIX (E + ISPB(digitos) + data + aleatorio). Usado p/ deduplicar.
# Tolera "/" e "-" que o OCR as vezes injeta no meio do codigo.
RE_TRANSACAO = re.compile(r"E[0-9A-Za-z/\-]{17,45}")
_LABELS_TRANSACAO = ("id da transacao", "id/transacao", "numero de controle")


def _limpa_token(t: str) -> str | None:
    limpo = re.sub(r"[^0-9A-Za-z]", "", t)
    return limpo if len(limpo) >= 18 else None


def parse_transacao(texto: str):
    """Extrai o ID fim-a-fim (E2E) da transacao PIX para deduplicacao de comprovantes.

    Estrategia em dois passos:
    1. Procura o ID imediatamente apos rotulos como "ID da transacao" (mais confiavel).
    2. Fallback: varre todo o texto e prefere IDs no formato E + digitos do ISPB.

    Args:
        texto: Texto bruto do comprovante.

    Returns:
        String com o ID da transacao (somente alfanumericos, >= 18 chars),
        ou ``None`` se nenhum candidato valido for encontrado.
    """
    texto = texto or ""
    linhas = _linhas(texto)
    # 1) preferir o ID logo apos o rotulo "ID da transacao" (evita o cod. de autenticacao)
    for i, ln in enumerate(linhas):
        if any(lbl in _norm(ln) for lbl in _LABELS_TRANSACAO):
            m = RE_TRANSACAO.search(" ".join(linhas[i:i + 3]))
            if m and (tok := _limpa_token(m.group(0))):
                return tok
    # 2) fallback: todos os candidatos, preferindo o formato E2E (E + digitos do ISPB)
    cands = [c for m in RE_TRANSACAO.finditer(texto) if (c := _limpa_token(m.group(0)))]
    if not cands:
        return None
    return next((c for c in cands if re.match(r"E\d{6}", c)), cands[0])


# --------------------------------------------------------------------------
# NOMES (origem / destino)
# --------------------------------------------------------------------------
_LABELS_IGNORAR = {
    "nome", "de", "para", "cpf", "cpf/cnpj", "cnpj", "instituicao", "agencia",
    "conta", "chave", "chave pix", "banco", "tipo", "tipo de conta",
    "tipo de transferencia", "valor", "valor pago", "id da transacao",
    "codigo de autenticacao", "autenticacao", "forma de pagamento",
    "dados bancarios do recebedor", "dados da transacao",
    "data e hora da transacao", "data do debito", "numero de controle",
    "id/transacao",
}
_BLOQUEIO = {
    "pix", "banco", "comprovante", "realizado", "andamento", "transferencia",
    "transacao", "pagamento", "pagamentos", "instituicao", "central",
    "atendimento", "ouvidoria", "sac", "bco", "unibanco", "bank", "ip", "sa",
    "dados", "forma", "tipo", "valor", "chave", "agencia", "conta", "codigo",
}


def _limpa_nome(linha: str) -> str:
    return re.sub(r"^\s*(nome|de|para|recebedor|pagador)\s*:?\s*", "",
                  linha, flags=re.IGNORECASE).strip()


def _eh_nome(linha: str) -> bool:
    n = _norm(linha)
    if n in _LABELS_IGNORAR or any(c.isdigit() for c in linha):
        return False
    if any(tok in _BLOQUEIO for tok in n.split()):
        return False
    if len([c for c in linha if c.isalpha()]) < 4:
        return False
    return len(linha.split()) >= 2 or linha.isupper()


def _header_match(linha_norm: str, h: str) -> bool:
    if len(h) <= 6:
        return (linha_norm == h or linha_norm.startswith(h + " ")
                or linha_norm.startswith(h + ":"))
    return h in linha_norm


def nome_apos(texto: str, headers: list[str]):
    """Extrai o nome que aparece logo apos um dos cabecalhos indicados no texto.

    Percorre as linhas do comprovante e, ao encontrar uma linha cujo conteudo
    normalizado corresponda a um dos ``headers``, coleta as proximas linhas
    que parecem ser um nome proprio (via ``_eh_nome``), parando ao encontrar
    a primeira linha que nao seja nome.

    Args:
        texto: Texto bruto do comprovante.
        headers: Lista de rotulos a procurar (ex.: ``["origem", "pagador"]``).
            Comparacao e case-insensitive e sem acentos.

    Returns:
        Nome extraido (pode ser multiplas palavras de linhas consecutivas)
        ou ``None`` se nenhum cabecalho for encontrado ou nenhum nome valido seguir.
    """
    linhas = _linhas(texto)
    headers = [_norm(h) for h in headers]
    for i, ln in enumerate(linhas):
        if any(_header_match(_norm(ln), h) for h in headers):
            partes = []
            for l2 in linhas[i + 1:i + 8]:
                cand = _limpa_nome(l2)
                if _eh_nome(cand):
                    partes.append(cand)
                elif partes:
                    break
            if partes:
                return " ".join(partes)
    return None


def parse_origem(texto: str):
    """Extrai o nome do pagador (origem da transferencia) do comprovante.

    Tenta rotulos especificos como "origem" e "dados do pagador" antes de
    recorrer ao generico "de".

    Args:
        texto: Texto bruto do comprovante.

    Returns:
        Nome do pagador ou ``None`` se nao identificado.
    """
    return nome_apos(texto, ["origem", "dados de quem fez a transacao",
                             "dados do pagador", "pagador", "conta de origem"]) \
        or nome_apos(texto, ["de"])


def parse_destino(texto: str):
    """Extrai o nome do recebedor (destino da transferencia) do comprovante.

    Tenta rotulos como "destino" e "dados do recebedor", depois "para" e, como
    ultimo recurso, aplica o fallback do layout do banco C6 (recebedor aparece
    no topo do comprovante sem rotulo, antes da secao "Conta de origem").

    Args:
        texto: Texto bruto do comprovante.

    Returns:
        Nome do recebedor ou ``None`` se nao identificado.
    """
    nome = nome_apos(texto, ["destino", "dados de quem recebeu",
                             "dados do recebedor", "recebedor"]) \
        or nome_apos(texto, ["para"])
    if nome:
        return nome
    # fallback C6: recebedor no topo, sem rotulo, antes de "Conta de origem".
    # Escolhe o candidato com mais palavras (nome completo > ruido do OCR).
    linhas = _linhas(texto)
    for i, ln in enumerate(linhas):
        if "conta de origem" in _norm(ln):
            candidatos = [_limpa_nome(l2) for l2 in linhas[:i] if _eh_nome(_limpa_nome(l2))]
            if candidatos:
                return max(candidatos, key=lambda s: len(s.split()))
            break
    return None


# --------------------------------------------------------------------------
# IO: extracao de texto (import tardio das libs pesadas)
# --------------------------------------------------------------------------
def _texto_de_pdf(path: str) -> str:
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages[:MAX_PAGINAS_PDF])


def _texto_de_pdf_escaneado(path: str) -> str:
    import pytesseract
    from pdf2image import convert_from_path
    paginas = convert_from_path(path, first_page=1, last_page=MAX_PAGINAS_PDF)
    return "\n".join(
        pytesseract.image_to_string(p, lang="por", config=_TESS_CONFIG) for p in paginas
    )


def _texto_de_imagem(path: str) -> str:
    import cv2
    import pytesseract
    img = cv2.imread(path)
    if img is None:
        return ""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # ampliar 2x melhora a separacao de palavras e a precisao do Tesseract
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return pytesseract.image_to_string(th, lang="por", config=_TESS_CONFIG)


def extrair_texto(path: str, is_pdf: bool) -> str:
    """Extrai o texto de um arquivo de comprovante (PDF ou imagem).

    Para PDFs: tenta extracao direta via pdfplumber; se o resultado for muito
    curto (PDF escaneado), reenvia as paginas para o Tesseract OCR.
    Para imagens: pre-processa com OpenCV (escala 2x + binarizacao Otsu) antes
    de enviar ao Tesseract.

    Args:
        path: Caminho absoluto do arquivo no sistema de arquivos local.
        is_pdf: True se o arquivo for PDF; False para imagem (JPEG/PNG).

    Returns:
        Texto extraido como string; pode ser vazio se o arquivo for ilegivel.
    """
    if is_pdf:
        texto = _texto_de_pdf(path)
        if len(texto.strip()) < 20:  # PDF escaneado -> OCR
            texto = _texto_de_pdf_escaneado(path)
        return texto
    return _texto_de_imagem(path)


def extrair_dados(path: str, is_pdf: bool) -> dict:
    """Extrai e estrutura todos os campos relevantes de um comprovante PIX.

    Orquestra a extracao de texto e aplica todos os parsers em sequencia.

    Args:
        path: Caminho absoluto do arquivo no sistema de arquivos local.
        is_pdf: True se o arquivo for PDF; False para imagem.

    Returns:
        Dicionario com chaves: ``texto`` (bruto), ``valor`` (Decimal ou None),
        ``data`` (date ou None), ``chave`` (str de digitos ou None),
        ``origem`` (str ou None), ``destino`` (str ou None),
        ``transacao`` (str ou None).
    """
    texto = extrair_texto(path, is_pdf)
    return {
        "texto": texto,
        "valor": parse_valor(texto),
        "data": parse_data(texto),
        "chave": parse_chave_digitos(texto),
        "origem": parse_origem(texto),
        "destino": parse_destino(texto),
        "transacao": parse_transacao(texto),
    }
