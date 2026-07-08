"""Normalizacao de texto compartilhada entre o OCR e o validador.

Fornece funcoes leves (sem dependencias externas) para padronizar strings antes
de comparacoes: remocao de acentos e normalizacao para minusculas.
"""
import unicodedata


def sem_acentos(s: str) -> str:
    """Remove acentos/diacriticos (NFKD)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def normalizar(s) -> str:
    """minuscula, sem acentos e sem espacos nas pontas (tolera nao-str)."""
    return sem_acentos(s if isinstance(s, str) else "").lower().strip()
