"""Roda o OCR REAL (pdfplumber + Tesseract) sobre samples/comprovantes/.

Use na SUA maquina, com Tesseract instalado, para conferir a taxa de extracao
de verdade antes de subir. Imprime valor/data/chave/origem/destino por arquivo.

    python scripts/validar_ocr_amostras.py
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from services import ocr  # noqa: E402

PASTA = os.path.join(RAIZ, "samples", "comprovantes")
EXT_IMG = (".jpg", ".jpeg", ".png")


def main():
    if not os.path.isdir(PASTA):
        print(f"Pasta nao encontrada: {PASTA}")
        return
    arquivos = sorted(
        f for f in os.listdir(PASTA)
        if f.lower().endswith((".pdf", *EXT_IMG))
    )
    if not arquivos:
        print("Nenhum comprovante em samples/comprovantes/.")
        return

    ok = 0
    for nome in arquivos:
        path = os.path.join(PASTA, nome)
        is_pdf = nome.lower().endswith(".pdf")
        try:
            d = ocr.extrair_dados(path, is_pdf)
            campos = [d["valor"], d["data"], d["chave"] or d["destino"], d["origem"]]
            completo = all(c is not None for c in campos)
            ok += completo
            marca = "OK " if completo else "!! "
            print(f"{marca}{nome}")
            print(f"     valor={d['valor']} data={d['data']} chave={d['chave']}")
            print(f"     origem={d['origem']!r} destino={d['destino']!r}")
        except Exception as e:
            print(f"ERRO {nome}: {type(e).__name__}: {e}")
            print("     (Tesseract instalado? `tesseract --version`)")
    print(f"\nCompletos (valor+data+destino+origem): {ok}/{len(arquivos)}")


if __name__ == "__main__":
    main()
