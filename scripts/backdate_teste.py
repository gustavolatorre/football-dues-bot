"""APENAS PARA TESTE: faz um jogador "dever" o mes atual, backdatando a adesao
para o 1o dia do mes passado e zerando a ultima notificacao.

Depois disso, o admin manda /cobrar e o jogador recebe a cobranca.

Rode no MESMO ambiente do bot (mesmo DATABASE_URL):
    # local (sqlite):
    python scripts/backdate_teste.py <telegram_id>
    # docker compose (postgres):
    docker compose exec bot python scripts/backdate_teste.py <telegram_id>
"""
import os
import sys
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from database.models import Jogador  # noqa: E402
from database import repo  # noqa: E402


def _primeiro_dia_mes_passado() -> date:
    hoje = date.today()
    if hoje.month == 1:
        return date(hoje.year - 1, 12, 1)
    return date(hoje.year, hoje.month - 1, 1)


def main():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("uso: python scripts/backdate_teste.py <telegram_id>")
        return
    tid = int(sys.argv[1])
    alvo = _primeiro_dia_mes_passado()
    with repo.session_scope() as s:
        jog = s.get(Jogador, tid)
        if jog is None:
            print(f"Jogador {tid} nao encontrado. Faca /start no bot primeiro.")
            return
        jog.data_adesao = alvo
        jog.status = "inadimplente"
        jog.ultima_notificacao = None
    print(f"OK: jogador {tid} agora deve o mes atual (adesao={alvo}). Mande /cobrar no bot.")


if __name__ == "__main__":
    main()
