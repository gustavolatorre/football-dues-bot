"""Acesso a dados (sincrono). Chamado dos handlers/jobs via asyncio.to_thread.

Contem toda a logica de persistencia: CRUD de jogadores, registro e decisao de
pagamentos, e o calculo de situacao financeira (saldo acumulado em dinheiro).
"""
import calendar
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import MAX_MENSALIDADES_ADIANTADO, MENSALIDADE_VALOR
from database import SessionLocal
from database.models import Jogador, Pagamento

_APROVADOS = ("auto_aprovado", "aprovado")


class PagamentoJaDecidido(Exception):
    """Levantada ao tentar decidir um pagamento que nao esta mais pendente.

    Protege contra decisao dupla (dois admins, clique repetido no botao ou
    resposta de valor a uma mensagem antiga): a primeira decisao e definitiva.
    """

    def __init__(self, status: str):
        super().__init__(f"pagamento ja decidido (status: {status})")
        self.status = status


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager que abre uma sessao, faz commit no sucesso ou rollback na excecao.

    Yields:
        Session: Sessao SQLAlchemy ativa.

    Raises:
        Exception: Qualquer excecao lancada dentro do bloco ``with`` e propagada apos
            o rollback.
    """
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def obter_jogador(telegram_id: int) -> Jogador | None:
    """Busca um jogador pela chave primaria (telegram_id).

    Args:
        telegram_id: ID do usuario no Telegram.

    Returns:
        Instancia de ``Jogador`` ou ``None`` se nao encontrado.
    """
    with session_scope() as s:
        return s.get(Jogador, telegram_id)


def criar_jogador(telegram_id: int, nome: str, nome_pix: str | None,
                  telefone: str | None) -> Jogador:
    """Cria ou atualiza o cadastro do jogador (upsert).

    Se o jogador ja existir, atualiza nome, nome_pix e telefone sem alterar
    data_adesao, valor_mensalidade (snapshot imutavel) nem ``ativo`` — /editar
    NAO reativa um jogador desativado (so o admin, via ``set_ativo``, que ajusta
    a data_adesao para nao cobrar o periodo pausado).

    Args:
        telegram_id: ID do usuario no Telegram.
        nome: Nome completo do jogador.
        nome_pix: Nome como aparece no comprovante PIX; se None, usa ``nome``.
        telefone: Numero de contato opcional.

    Returns:
        Instancia de ``Jogador`` ja persistida e com dados atualizados.
    """
    with session_scope() as s:
        jog = s.get(Jogador, telegram_id)
        if jog is None:
            jog = Jogador(telegram_id=telegram_id, ativo=True)
            s.add(jog)
        jog.nome = (nome or "")[:120]
        jog.nome_pix = (nome_pix or nome or "")[:120]
        jog.telefone = telefone[:32] if telefone else None
        if jog.data_adesao is None:
            jog.data_adesao = date.today()
        if jog.status is None:
            jog.status = "em_dia"
        if jog.valor_mensalidade is None:
            jog.valor_mensalidade = MENSALIDADE_VALOR  # snapshot: nao muda retroativo
        s.flush()
        return jog


def set_status(telegram_id: int, status: str) -> None:
    """Atualiza o campo ``status`` do jogador (``"em_dia"`` ou ``"inadimplente"``).

    Args:
        telegram_id: ID do usuario no Telegram.
        status: Novo valor do status.
    """
    with session_scope() as s:
        jog = s.get(Jogador, telegram_id)
        if jog:
            jog.status = status


def set_ultima_notificacao(telegram_id: int, quando: date) -> None:
    """Registra a data do ultimo lembrete de cobranca enviado ao jogador.

    Usada para garantir idempotencia: no maximo um aviso por jogador por dia.

    Args:
        telegram_id: ID do usuario no Telegram.
        quando: Data em que o lembrete foi enviado (geralmente hoje).
    """
    with session_scope() as s:
        jog = s.get(Jogador, telegram_id)
        if jog:
            jog.ultima_notificacao = quando


def jogadores_ativos() -> list[Jogador]:
    """Retorna todos os jogadores com ``ativo=True``.

    Returns:
        Lista de ``Jogador`` ativos, sem ordenacao garantida.
    """
    with session_scope() as s:
        return list(s.scalars(select(Jogador).where(Jogador.ativo.is_(True))))


def listar_jogadores(incluir_inativos: bool = True) -> list[Jogador]:
    """Lista jogadores em ordem alfabetica por nome.

    Args:
        incluir_inativos: Se True (padrao), retorna ativos e inativos.
            Se False, filtra apenas os ativos.

    Returns:
        Lista de ``Jogador`` ordenada por ``nome``.
    """
    with session_scope() as s:
        stmt = select(Jogador)
        if not incluir_inativos:
            stmt = stmt.where(Jogador.ativo.is_(True))
        return list(s.scalars(stmt.order_by(Jogador.nome)))


def _add_meses(d: date, n: int) -> date:
    """Soma ``n`` meses a uma data, ajustando o dia para o ultimo do mes se necessario.

    Args:
        d: Data base.
        n: Numero de meses a somar (positivo).

    Returns:
        Nova data com n meses adicionados.
    """
    total = d.month - 1 + n
    ano = d.year + total // 12
    mes = total % 12 + 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def set_ativo(telegram_id: int, ativo: bool) -> str | None:
    """Ativa ou desativa um jogador, ajustando a data de adesao ao reativar.

    Ao desativar, registra ``desativado_em = hoje``.
    Ao reativar, avanca ``data_adesao`` pelo mesmo numero de meses em que o jogador
    ficou inativo, de forma que o periodo parado nao entre no calculo de debito.

    Granularidade (regra F5): o gap e contado por **mes-calendario** (diferenca de
    ano*12+mes), nao por dias. Consequencias a documentar para o admin nao se
    surpreender:

    - desativar e reativar **dentro do mesmo mes** -> gap 0 -> nao isenta nada;
    - desativar 31/12 e reativar 01/01 -> gap 1 -> isenta 1 mes (mesmo parado ~1 dia);
    - desativar 01/06 e reativar 30/06 -> gap 0 -> mes cobrado inteiro (parado ~29 dias).

    Para um grupo de futebol e uma aproximacao aceitavel; se precisar de ajuste fino,
    o admin aprova/ajusta um pagamento manualmente. ``/editar`` **nao** chama esta
    funcao — edicao de cadastro nunca reativa um jogador desativado (so ``/reativar``).

    Args:
        telegram_id: ID do usuario no Telegram.
        ativo: True para reativar, False para desativar.

    Returns:
        Nome do jogador se encontrado, ou None se o ID nao existir.
    """
    hoje = date.today()
    with session_scope() as s:
        jog = s.get(Jogador, telegram_id)
        if jog is None:
            return None
        if not ativo:
            jog.desativado_em = hoje
        elif jog.desativado_em:
            gap = (hoje.year - jog.desativado_em.year) * 12 + (hoje.month - jog.desativado_em.month)
            if gap > 0:
                jog.data_adesao = _add_meses(jog.data_adesao, gap)  # pula os meses parados
            jog.desativado_em = None
        jog.ativo = ativo
        return jog.nome


def remover_jogador(telegram_id: int) -> str | None:
    """Exclui definitivamente o jogador e seus pagamentos. Retorna o nome ou None."""
    with session_scope() as s:
        jog = s.get(Jogador, telegram_id)
        if jog is None:
            return None
        nome = jog.nome
        s.query(Pagamento).filter_by(jogador_id=telegram_id).delete()
        s.delete(jog)
        return nome


def comprovante_duplicado(transacao: str | None, file_id: str) -> bool:
    """True se ja existe um comprovante (nao rejeitado) com mesma transacao ou arquivo."""
    with session_scope() as s:
        conds = [Pagamento.file_id == file_id]
        if transacao:
            conds.append(Pagamento.transacao == transacao)
        stmt = select(Pagamento.id).where(
            or_(*conds),
            Pagamento.status_validacao != "rejeitado",
        )
        return s.scalars(stmt).first() is not None


def _meses_esperados(data_adesao: date, dia_cobranca: int, hoje: date | None = None) -> int:
    """Calcula quantas mensalidades deveriam ter sido pagas desde a adesao ate hoje.

    O mes atual so entra na conta a partir de ``dia_cobranca``; antes disso, o mes
    corrente ainda nao e considerado vencido.

    Args:
        data_adesao: Data em que o jogador entrou no grupo.
        dia_cobranca: Dia do mes em que a mensalidade vence (1–28).
        hoje: Data de referencia; usa ``date.today()`` se omitida.

    Returns:
        Numero inteiro de mensalidades vencidas (>= 0).
    """
    hoje = hoje or date.today()
    n = (hoje.year - data_adesao.year) * 12 + (hoje.month - data_adesao.month) + 1
    if hoje.day < dia_cobranca:
        n -= 1  # mes atual ainda nao venceu
    return max(0, n)


def _pago_total(s: Session, jogador_id: int) -> Decimal:
    """Soma todos os pagamentos aprovados do jogador.

    Args:
        s: Sessao SQLAlchemy ativa.
        jogador_id: ID do usuario no Telegram.

    Returns:
        Total pago acumulado como ``Decimal``; retorna 0 se nao houver pagamentos.
    """
    pago = s.scalar(
        select(func.coalesce(func.sum(Pagamento.valor), 0)).where(
            Pagamento.jogador_id == jogador_id,
            Pagamento.status_validacao.in_(_APROVADOS),
        )
    )
    # str() intermediario: no SQLite o SUM pode voltar float (ex.: 66.66) e
    # Decimal(float) carregaria o residuo binario; no Postgres ja vem Decimal.
    return Decimal(str(pago or 0))


def _situacao_de(jog: Jogador, pago: Decimal, dia_cobranca: int) -> dict:
    """Calcula a situacao financeira acumulada do jogador a partir do total pago.

    Formula: ``saldo = pago - (meses_vencidos * valor_mensalidade)``

    - ``saldo < 0`` -> devendo; ``falta = -saldo``.
    - ``saldo > 0`` -> em dia com credito.
    - ``saldo = 0`` -> em dia exato.

    Args:
        jog: Instancia do jogador (usa data_adesao e valor_mensalidade).
        pago: Total ja pago em pagamentos aprovados.
        dia_cobranca: Dia do mes que define o vencimento (1–28).

    Returns:
        Dicionario com chaves: ``saldo``, ``falta``, ``credito``, ``meses_atraso``,
        ``parcial`` (se a falta nao e multiplo exato da mensalidade) e ``em_dia``.
    """
    mens = jog.valor_mensalidade or MENSALIDADE_VALOR
    esperado = mens * _meses_esperados(jog.data_adesao, dia_cobranca)
    saldo = Decimal(pago) - esperado
    falta = -saldo if saldo < 0 else Decimal("0")
    credito = saldo if saldo > 0 else Decimal("0")
    meses_atraso = int(falta // mens) if (falta > 0 and mens > 0) else 0
    parcial = bool(falta > 0 and mens > 0 and falta % mens != 0)
    return {
        "saldo": saldo,
        "falta": falta,
        "credito": credito,
        "meses_atraso": meses_atraso,
        "parcial": parcial,
        "em_dia": falta == 0,
    }


def _situacao_calc(s: Session, jog: Jogador, dia_cobranca: int) -> dict:
    return _situacao_de(jog, _pago_total(s, jog.telegram_id), dia_cobranca)


def _aplicar_status(s, jog: Jogador, dia_cobranca: int) -> None:
    jog.status = "em_dia" if _situacao_calc(s, jog, dia_cobranca)["em_dia"] else "inadimplente"


def situacao(jogador_id: int, dia_cobranca: int) -> dict:
    """Retorna a situacao financeira atual do jogador (falta, credito, saldo).

    Conveniencia publica que abre sessao, calcula e retorna o dicionario de
    ``_situacao_de``. Se o jogador nao existir, retorna situacao neutra (em dia).

    Args:
        jogador_id: ID do usuario no Telegram.
        dia_cobranca: Dia do mes que define o vencimento (1–28).

    Returns:
        Dicionario com ``saldo``, ``falta``, ``credito``, ``meses_atraso``,
        ``parcial`` e ``em_dia``.
    """
    with session_scope() as s:
        jog = s.get(Jogador, jogador_id)
        if jog is None:
            return {"saldo": Decimal("0"), "falta": Decimal("0"), "credito": Decimal("0"),
                    "meses_atraso": 0, "parcial": False, "em_dia": True}
        return _situacao_calc(s, jog, dia_cobranca)


def limites_validacao(jogador_id: int, dia_cobranca: int) -> tuple[date, Decimal]:
    """Retorna (piso_data, teto_valor) para decidir auto-aprovacao de um comprovante.

    - piso_data: 1o dia do mes mais antigo ainda em aberto (limitado ao mes atual).
      Aceita pagamento do mes corrente/adiantado; barra comprovante de periodo anterior.
    - teto_valor: falta atual + MAX_MENSALIDADES_ADIANTADO * mensalidade. Acima disso o
      valor e "absurdo" (ex.: OCR com digito a mais) -> vai ao admin.
    """
    hoje = date.today()
    with session_scope() as s:
        jog = s.get(Jogador, jogador_id)
        if jog is None:
            return (hoje, MENSALIDADE_VALOR * MAX_MENSALIDADES_ADIANTADO)
        mens = jog.valor_mensalidade or MENSALIDADE_VALOR
        pago = _pago_total(s, jog.telegram_id)
        esperado_incl = ((hoje.year - jog.data_adesao.year) * 12
                         + (hoje.month - jog.data_adesao.month) + 1)
        quitados = int(pago // mens) if mens > 0 else 0
        alvo_idx = max(0, min(quitados, esperado_incl - 1))
        piso = _add_meses(date(jog.data_adesao.year, jog.data_adesao.month, 1), alvo_idx)
        falta = _situacao_de(jog, pago, dia_cobranca)["falta"]
        return (piso, falta + mens * MAX_MENSALIDADES_ADIANTADO)


def pendentes() -> list[dict]:
    """Lista os comprovantes aguardando decisao do admin (mais antigos primeiro)."""
    with session_scope() as s:
        stmt = (
            select(Pagamento, Jogador.nome)
            .join(Jogador, Jogador.telegram_id == Pagamento.jogador_id)
            .where(Pagamento.status_validacao == "pendente_admin")
            .order_by(Pagamento.criado_em)
        )
        return [
            {"id": pg.id, "jogador_id": pg.jogador_id, "nome": nome, "file_id": pg.file_id,
             "is_pdf": bool(pg.is_pdf), "valor": pg.valor_extraido, "origem": pg.origem_extraida,
             "destino": pg.destino_extraido, "data": pg.data_extraida}
            for pg, nome in s.execute(stmt).all()
        ]


def registrar_pagamento(jogador_id: int, dados: dict, file_id: str,
                        status_validacao: str, dia_cobranca: int,
                        is_pdf: bool = False) -> int | None:
    """Persiste um novo registro de pagamento e, se aprovado, recalcula o status.

    A coluna ``transacao`` tem indice UNIQUE: se outro comprovante com o mesmo ID
    de transacao ja existir (corrida sob concorrencia), o INSERT viola a unicidade
    e a funcao retorna ``None`` em vez de levantar excecao — o handler trata como
    "ja registrado". Garante deduplicacao atomica mesmo com varios updates em paralelo.

    Args:
        jogador_id: ID do usuario no Telegram.
        dados: Dicionario com os campos extraidos pelo OCR (valor, data, origem,
            destino, transacao).
        file_id: Identificador do arquivo no Telegram (para deduplicacao).
        status_validacao: ``"auto_aprovado"``, ``"aprovado"``, ``"pendente_admin"``
            ou ``"rejeitado"``.
        dia_cobranca: Dia do mes que define o vencimento, usado para recalcular status.

    Returns:
        ID (PK) do ``Pagamento`` recem-criado, ou ``None`` se a transacao for duplicada.
    """
    try:
        with session_scope() as s:
            pg = Pagamento(
                jogador_id=jogador_id,
                competencia=(dados.get("data") or date.today()).strftime("%Y-%m"),
                valor=dados.get("valor") or Decimal("0"),
                data_pagamento=dados.get("data"),
                file_id=file_id,
                origem_extraida=dados.get("origem"),
                destino_extraido=dados.get("destino"),
                valor_extraido=dados.get("valor"),
                data_extraida=dados.get("data"),
                transacao=dados.get("transacao"),
                is_pdf=is_pdf,
                status_validacao=status_validacao,
            )
            s.add(pg)
            s.flush()
            if status_validacao in _APROVADOS:
                jog = s.get(Jogador, jogador_id)
                if jog:
                    _aplicar_status(s, jog, dia_cobranca)
            return pg.id
    except IntegrityError:  # transacao duplicada (corrida) -> dedup atomico
        return None


def obter_pagamento(pagamento_id: int) -> Pagamento | None:
    """Busca um pagamento pela chave primaria.

    Args:
        pagamento_id: ID do pagamento na tabela ``pagamentos``.

    Returns:
        Instancia de ``Pagamento`` ou ``None`` se nao encontrado.
    """
    with session_scope() as s:
        return s.get(Pagamento, pagamento_id)


def decidir_pagamento(pagamento_id: int, aprovado: bool, dia_cobranca: int,
                      valor: Decimal | None = None) -> Pagamento | None:
    """Aplica a decisao do admin (aprovar/rejeitar) em um pagamento pendente.

    Atualiza ``status_validacao`` do pagamento e recalcula ``status`` do jogador. Se o
    admin informar um ``valor`` ao aprovar (ex.: OCR nao leu), ele e gravado antes do
    recalculo do saldo — o excedente vira credito automaticamente.

    So age em pagamentos ``"pendente_admin"``: decidir duas vezes (outro admin,
    clique repetido, resposta a mensagem antiga) levanta ``PagamentoJaDecidido``.

    Args:
        pagamento_id: ID do pagamento na tabela ``pagamentos``.
        aprovado: True para aprovar, False para rejeitar.
        dia_cobranca: Dia do mes do vencimento, necessario para recalcular o status.
        valor: Valor informado pelo admin ao aprovar (sobrescreve o valor gravado).

    Returns:
        Instancia de ``Pagamento`` com o novo status, ou None se nao encontrado.

    Raises:
        PagamentoJaDecidido: Se o pagamento ja foi aprovado/rejeitado antes.
    """
    with session_scope() as s:
        pg = s.get(Pagamento, pagamento_id)
        if pg is None:
            return None
        if pg.status_validacao != "pendente_admin":
            raise PagamentoJaDecidido(pg.status_validacao)
        if aprovado and valor is not None:
            pg.valor = valor
        pg.status_validacao = "aprovado" if aprovado else "rejeitado"
        jog = s.get(Jogador, pg.jogador_id)
        if jog:
            _aplicar_status(s, jog, dia_cobranca)
        s.flush()
        s.expunge(pg)
        return pg


def relatorio(competencia: str, dia_cobranca: int) -> dict:
    """Gera o relatorio financeiro do mes para o painel do admin.

    Agrega: total de ativos, em dia vs. inadimplentes, valor arrecadado na
    competencia, total devido (saldo acumulado de todos os devedores), lista de
    devedores ordenada por debito decrescente e lista de jogadores com credito.

    Args:
        competencia: Mes de referencia no formato ``"AAAA-MM"``.
        dia_cobranca: Dia do mes do vencimento, usado para calcular situacao de cada jogador.

    Returns:
        Dicionario com chaves: ``total``, ``em_dia``, ``inadimplentes``,
        ``arrecadado``, ``total_devido``, ``devedores`` e ``creditos``.
    """
    with session_scope() as s:
        ativos = list(s.scalars(select(Jogador).where(Jogador.ativo.is_(True))))
        arrecadado = s.scalar(
            select(func.coalesce(func.sum(Pagamento.valor), 0)).where(
                Pagamento.competencia == competencia,
                Pagamento.status_validacao.in_(_APROVADOS),
            )
        ) or Decimal("0")

        pagos = dict(s.execute(
            select(Pagamento.jogador_id, func.coalesce(func.sum(Pagamento.valor), 0))
            .where(Pagamento.status_validacao.in_(_APROVADOS))
            .group_by(Pagamento.jogador_id)
        ).all())

        em_dia = inadimplentes = 0
        total_devido = Decimal("0")
        devedores = []
        creditos = []
        for jog in ativos:
            sit = _situacao_de(jog, Decimal(str(pagos.get(jog.telegram_id, 0))), dia_cobranca)
            if sit["falta"] > 0:
                inadimplentes += 1
                total_devido += sit["falta"]
                devedores.append({"nome": jog.nome, "falta": sit["falta"],
                                  "meses": sit["meses_atraso"], "parcial": sit["parcial"]})
            else:
                em_dia += 1
                if sit["credito"] > 0:
                    creditos.append({"nome": jog.nome, "credito": sit["credito"]})

        devedores.sort(key=lambda d: d["falta"], reverse=True)
        return {
            "total": len(ativos),
            "em_dia": em_dia,
            "inadimplentes": inadimplentes,
            "arrecadado": Decimal(str(arrecadado)),
            "total_devido": total_devido,
            "devedores": devedores,
            "creditos": creditos,
        }
