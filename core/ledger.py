"""Histórico mês a mês de cada categoria, numa passada só.

O saldo de uma categoria não é uma foto: é o resultado de tudo que
aconteceu desde o primeiro mês registrado. A sobra do orçamento de
setembro é dinheiro de outubro, e um estouro em outubro é dívida de
novembro.

Calcular isso perguntando ao banco a cada mês faria o custo crescer para
sempre — um ano a mais de uso, uma dezena a mais de consultas por clique.
Por isso tudo que o cálculo precisa é lido em lote e guardado na sessão, e
o histórico é montado em memória, do primeiro mês até o pedido.

É a única implementação de saldo do aplicativo: ``budget_service`` lê
daqui, e por isso nenhuma tela pode discordar de outra.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from . import cache
from . import categories as cat
from . import repositories as repo
from .categories import CategoryView
from .utils import add_months, month_start


@dataclass(frozen=True)
class SaldoMes:
    """O que aconteceu com uma categoria dentro de um mês."""

    month: date
    #: Saldo que veio do mês anterior. Pode ser negativo.
    inicial_cents: int
    #: Dinheiro que entrou: a fatia do rateio, ou o valor separado.
    entrada_cents: int
    gasto_cents: int
    #: Efeito das transferências: positivo recebeu, negativo cedeu.
    transferencia_cents: int
    #: Se o saldo do mês foi redefinido por um fechamento.
    veio_de_fechamento: bool

    @property
    def final_cents(self) -> int:
        """Saldo no fim do mês."""
        return (
            self.inicial_cents
            + self.entrada_cents
            - self.gasto_cents
            + self.transferencia_cents
        )


class Historico:
    """Saldos de todas as categorias em todos os meses conhecidos."""

    def __init__(
        self,
        saldos: dict[tuple[date, int], SaldoMes],
        planejado: dict[tuple[date, int], int],
        meses: list[date],
    ) -> None:
        self._saldos = saldos
        self._planejado = planejado
        self.meses = meses

    def do_mes(self, month: date, category_id: int) -> SaldoMes:
        """Movimento da categoria no mês."""
        alvo = month_start(month)
        achado = self._saldos.get((alvo, category_id))
        if achado is not None:
            return achado
        return SaldoMes(
            month=alvo,
            inicial_cents=self.saldo_em(add_months(alvo, -1), category_id),
            entrada_cents=0,
            gasto_cents=0,
            transferencia_cents=0,
            veio_de_fechamento=False,
        )

    def saldo_em(self, month: date, category_id: int) -> int:
        """Saldo no fim do mês pedido."""
        alvo = month_start(month)
        if not self.meses or alvo < self.meses[0]:
            return 0
        referencia = min(alvo, self.meses[-1])
        movimento = self._saldos.get((referencia, category_id))
        return movimento.final_cents if movimento else 0

    def orcado(self, month: date, category_id: int) -> int:
        """Quanto o rateio destinou à categoria naquele mês."""
        return self._planejado.get((month_start(month), category_id), 0)

    def planejado_do_mes(self, month: date) -> dict[int, int]:
        """Rateio completo de um mês, por categoria."""
        alvo = month_start(month)
        return {
            cid: valor
            for (mes, cid), valor in self._planejado.items()
            if mes == alvo
        }


def obter(session: Session, ate: date) -> Historico:
    """Histórico do primeiro mês registrado até ``ate``, com cache."""
    limite = month_start(ate)
    return cache.obter(session, f"historico:{limite}", lambda: _montar(session, limite))


def _primeiro_mes(*fontes: dict) -> date | None:
    """Mês mais antigo entre as chaves das fontes lidas."""
    candidatos: list[date] = []
    for fonte in fontes:
        for chave in fonte:
            candidatos.append(chave[0] if isinstance(chave, tuple) else chave)
    return min(candidatos) if candidatos else None


def _montar(session: Session, limite: date) -> Historico:
    """Percorre os meses para a frente, acumulando o saldo de cada categoria."""
    bases = repo.base_por_mes(session)
    gastos = repo.gastos_por_mes_e_categoria(session)
    separacoes = repo.separacoes_por_mes_e_categoria(session)
    transferencias = repo.transferencias_por_mes_e_categoria(session)
    quando_separou = repo.separacoes_com_hora(session)
    iniciais = repo.saldos_iniciais(session)

    # Um fechamento sozinho já define uma posição que precisa ser carregada
    # para a frente, mesmo sem nenhuma receita ou gasto registrado.
    fechados = {f.month: 0 for f in repo.fechamentos(session)}
    inicio = _primeiro_mes(bases, gastos, separacoes, transferencias, fechados)
    if inicio is None or inicio > limite:
        inicio = limite

    saldos: dict[tuple[date, int], SaldoMes] = {}
    planejado_por_mes: dict[tuple[date, int], int] = {}
    #: Saldo corrente de cada categoria enquanto a varredura avança.
    corrente: dict[int, int] = dict(iniciais)

    meses: list[date] = []
    mes = inicio
    while mes <= limite:
        meses.append(mes)
        vistas = cat.resolve_all(session, mes)
        fechamento = repo.latest_closing_until(session, mes)

        # A abertura vem antes do plano: a regra da meta precisa saber
        # quanto já havia guardado, e num mês com fechamento isso é o valor
        # que você declarou, não o que o app vinha acumulando.
        abertura = {v.id: _abertura(v, mes, corrente, fechamento) for v in vistas}
        planejado = _planejar(session, vistas, bases.get(mes, 0), abertura)

        for vista in vistas:
            # Só quem participa do rateio ganha linha: guardar zeros faria
            # o plano da tela listar categorias que não recebem nada.
            if vista.id in planejado:
                planejado_por_mes[(mes, vista.id)] = planejado[vista.id]
            movimento = _mover(
                vista,
                mes,
                abertura=abertura,
                planejado=planejado,
                gastos=gastos,
                separacoes=separacoes,
                transferencias=transferencias,
                fechamento=fechamento,
                quando_separou=quando_separou,
            )
            saldos[(mes, vista.id)] = movimento
            corrente[vista.id] = movimento.final_cents

        mes = add_months(mes, 1)

    return Historico(saldos, planejado_por_mes, meses)


def _abertura(
    vista: CategoryView,
    mes: date,
    corrente: dict[int, int],
    fechamento: object | None,
) -> int:
    """Com quanto a categoria começa o mês.

    Normalmente é o saldo que sobrou do mês anterior. Mas se você informou
    o saldo real desta categoria no fechamento deste mês, é ele que vale:
    dinheiro conferido no banco ganha do que o app vinha somando.
    """
    if (
        vista.balance_from_closing is not None
        and fechamento is not None
        and getattr(fechamento, "month", None) == mes
    ):
        return fechamento.valor_de(vista.balance_from_closing)  # type: ignore[union-attr]
    return corrente.get(vista.id, 0) if vista.accumulates else 0


def _planejar(
    session: Session,
    vistas: list[CategoryView],
    base_cents: int,
    corrente: dict[int, int],
) -> dict[int, int]:
    """Rateio do mês, já com a regra das metas aplicada.

    A meta olha o saldo de **antes** do mês: é o que faltava quando o plano
    foi montado, e não muda enquanto você confirma as separações.
    """
    from . import budget_service as budget  # o ciclo só existe em execução

    bruto = budget.distribuir(base_cents, vistas)
    saldos = {
        v.id: corrente.get(v.id, 0) for v in vistas if v.target_amount_cents is not None
    }
    ajustado, _sobra = budget.aplicar_regras_de_meta(bruto, vistas, saldos)
    return ajustado


def _mover(
    vista: CategoryView,
    mes: date,
    *,
    abertura: dict[int, int],
    planejado: dict[int, int],
    gastos: dict[tuple[date, int], int],
    separacoes: dict[tuple[date, int], int],
    transferencias: dict[tuple[date, int], int],
    fechamento: object | None,
    quando_separou: dict[tuple[date, int], datetime | None],
) -> SaldoMes:
    """Calcula o movimento de uma categoria dentro de um mês."""
    gasto = gastos.get((mes, vista.id), 0)
    transferido = transferencias.get((mes, vista.id), 0)

    # Quem exige separação só recebe o que foi de fato separado. Quem é
    # orçamento de consumo recebe a fatia do rateio, que fica na conta
    # corrente sem precisar de nenhuma transferência de banco.
    if vista.requires_separation:
        entrada = separacoes.get((mes, vista.id), 0)
    elif vista.receives_percent:
        entrada = planejado.get(vista.id, 0)
    else:
        entrada = 0

    inicial = abertura.get(vista.id, 0)

    # No mês em que você informa o saldo real, o declarado é a verdade: ele
    # já entrou na abertura, e aqui só se soma o que foi registrado depois
    # de você ter informado.
    veio_de_fechamento = (
        vista.balance_from_closing is not None
        and fechamento is not None
        and getattr(fechamento, "month", None) == mes
    )
    if veio_de_fechamento:
        entrada = _entrada_depois_do_fechamento(
            vista, mes, separacoes, quando_separou, fechamento
        )
        gasto = 0

    return SaldoMes(
        month=mes,
        inicial_cents=inicial,
        entrada_cents=entrada,
        gasto_cents=gasto,
        transferencia_cents=transferido,
        veio_de_fechamento=veio_de_fechamento,
    )


def _entrada_depois_do_fechamento(
    vista: CategoryView,
    mes: date,
    separacoes: dict[tuple[date, int], int],
    quando_separou: dict[tuple[date, int], datetime | None],
    fechamento: object,
) -> int:
    """Separação que ainda não está refletida no saldo declarado.

    Se você informou o saldo real e **depois** confirmou a separação, o
    dinheiro entrou por cima. Se confirmou antes, já está lá dentro.
    """
    valor = separacoes.get((mes, vista.id), 0)
    if not valor:
        return 0
    confirmado = quando_separou.get((mes, vista.id))
    informado = getattr(fechamento, "updated_at", None)
    if confirmado is None or informado is None:
        return 0
    return valor if confirmado > informado else 0
