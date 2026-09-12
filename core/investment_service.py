"""Regras de investimentos e dividendos.

Esta versão não controla ativos individuais. O usuário aponta, no perfil, **qual** das categorias dele representa os
investimentos. O capital aportado sai dos movimentos reais dessa categoria
— separações confirmadas e transferências —, e o valor atual sai do
fechamento. A diferença é o resultado.

Nada aqui depende de nome: nem "Independência", nem "Investimentos". Se
ninguém escolheu uma categoria, o app não mostra resultado nenhum, em vez
de adivinhar.

Dividendos ficam de fora dessa conta de propósito: se entrassem, seriam
contados duas vezes (uma no resultado, outra quando virassem renda).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from . import categories as cat
from . import ledger
from . import profile_service
from . import repositories as repo
from .models import ClosingField
from .period import Period
from .utils import month_start


@dataclass(frozen=True)
class ResumoInvestimentos:
    """Retrato dos investimentos numa posição."""

    posicao: date
    capital_destinado_cents: int
    valor_atual_cents: int
    informado: bool

    @property
    def resultado_cents(self) -> int:
        """Ganho ou perda estimada; negativo quando há prejuízo.

        Fica em zero enquanto o valor atual não for informado, para não
        confundir "ainda não preenchi" com "perdi tudo".
        """
        if not self.informado:
            return 0
        return self.valor_atual_cents - self.capital_destinado_cents

    @property
    def rentabilidade_pct(self) -> float:
        """Resultado sobre o capital destinado, em percentual."""
        if not self.informado or self.capital_destinado_cents <= 0:
            return 0.0
        return self.resultado_cents / self.capital_destinado_cents * 100

    @property
    def rentabilidade_valida(self) -> bool:
        """Se a rentabilidade é matematicamente significativa."""
        return self.informado and self.capital_destinado_cents > 0


def categoria_de_investimento(session: Session, month: date):
    """A categoria que o usuário declarou como "meus investimentos".

    Vem do perfil, por id. Se ninguém escolheu nenhuma, devolve ``None`` —
    e o app simplesmente não mostra resultado de investimento, em vez de
    adivinhar pelo nome.
    """
    escolhida = profile_service.obter(session).investment_category_id
    if escolhida is None:
        return None
    return next(
        (v for v in cat.resolve_all(session, month) if v.id == escolhida), None
    )


def capital_destinado(session: Session, month: date) -> int:
    """Quanto do dinheiro investido saiu do bolso do usuário.

    É o custo de aquisição: separações confirmadas, mais transferências
    recebidas, menos as enviadas, mais o que ele declarou já ter aportado
    antes de usar o aplicativo. Não é o saldo — a diferença entre os dois
    é justamente o rendimento.
    """
    alvo = month_start(month)
    vista = categoria_de_investimento(session, alvo)
    if vista is None:
        return 0

    aportado = profile_service.obter(session).investment_cost_basis_cents or 0
    # Separações cruas, e não o saldo do histórico: o fechamento redefine o
    # saldo com o valor conferido no banco (que já inclui rendimento), e
    # rendimento não é dinheiro que saiu do bolso.
    separado = repo.sum_allocations_until(session, alvo, vista.id)
    recebido = sum(
        efeito
        for (mes, cid), efeito in repo.transferencias_por_mes_e_categoria(
            session
        ).items()
        if cid == vista.id and mes <= alvo
    )
    return aportado + separado + recebido


def get_resumo(session: Session, period: Period) -> ResumoInvestimentos:
    """Resumo de investimentos na posição do período.

    No modo anual a posição é o fechamento mais recente do ano; no mensal, o
    mais recente até o mês.
    """
    if period.is_month:
        fechamento = repo.latest_closing_until(session, period.month)
        posicao = period.month
    else:
        fechamento = repo.latest_closing_in(session, period)
        posicao = fechamento.month if fechamento else period.end

    destinado = capital_destinado(session, posicao)
    if fechamento is None or fechamento.investimentos_cents == 0:
        return ResumoInvestimentos(posicao, destinado, 0, informado=False)
    return ResumoInvestimentos(
        posicao, destinado, fechamento.investimentos_cents, informado=True
    )


def valor_para_o_patrimonio(session: Session, month: date) -> tuple[int, bool]:
    """Quanto os investimentos valem hoje, e se o valor foi conferido.

    Existe para resolver uma dupla contagem: se o patrimônio somasse o
    saldo contábil da categoria **e** o valor informado no fechamento, o
    mesmo dinheiro entraria duas vezes.

    Quando há fechamento com valor informado, ele manda — é o número que
    você conferiu no banco. Quando não há, vale o saldo calculado, e o
    segundo item da resposta vem ``False`` para a tela poder dizer
    "estimado".
    """
    from . import budget_service as budget

    alvo = month_start(month)
    vista = categoria_de_investimento(session, alvo)
    if vista is None:
        return 0, False

    fechamento = repo.latest_closing_until(session, alvo)
    if fechamento is not None and fechamento.investimentos_cents:
        return fechamento.investimentos_cents, True
    return budget.saldo_categoria(session, vista, alvo), False


def dividendos_do_periodo(session: Session, period: Period) -> int:
    """Dividendos informados dentro do período."""
    return repo.sum_dividends(session, period)


def dividendos_acumulados(session: Session, month: date) -> int:
    """Soma dos dividendos informados até o mês (inclusive)."""
    alvo = month_start(month)
    return sum(f.dividendos_cents for f in repo.list_closings(session) if f.month <= alvo)


def serie_dividendos(session: Session, meses: list[date]) -> list[tuple[date, int, int]]:
    """Série ``(mês, dividendos do mês, acumulado)`` para os gráficos."""
    por_mes = {f.month: f.dividendos_cents for f in repo.list_closings(session)}
    saida, acumulado = [], 0
    for mes in meses:
        valor = por_mes.get(mes, 0)
        acumulado += valor
        saida.append((mes, valor, acumulado))
    return saida
