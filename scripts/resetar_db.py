"""Apaga TODOS os jogadores e pagamentos (reset para testes).

Uso (no mesmo ambiente do bot):
    docker compose exec bot python scripts/resetar_db.py        # mostra o que sera apagado
    docker compose exec bot python scripts/resetar_db.py --sim  # confirma e apaga
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from database.models import Jogador, Pagamento  # noqa: E402
from database import repo  # noqa: E402


def main():
    confirmar = "--sim" in sys.argv
    with repo.session_scope() as s:
        total_jog = s.query(Jogador).count()
        total_pag = s.query(Pagamento).count()
        if not confirmar:
            print(f"Isso vai apagar {total_jog} jogadores e {total_pag} pagamentos.")
            print("Rode de novo com --sim para confirmar:")
            print("  docker compose exec bot python scripts/resetar_db.py --sim")
            return
        s.query(Pagamento).delete()  # FK: pagamentos antes de jogadores
        s.query(Jogador).delete()
    print(f"OK: apagados {total_jog} jogadores e {total_pag} pagamentos.")


if __name__ == "__main__":
    main()
