"""Regras de investimentos e dividendos.

Esta versão não controla ativos individuais. O aplicativo sabe quanto foi
**separado** para as categorias marcadas com ``counts_as_investment_capital``
(capital destinado) e quanto o usuário informou de patrimônio investido no
fechamento — a diferença é o resultado estimado.

Nada aqui depende do nome "Independência": o que vale é a propriedade
declarada na versão da categoria.

Dividendos ficam de fora dessa conta de propósito: se entrassem, seriam
contados duas vezes (uma no resultado, outra quando virassem renda).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from . import categories as cat
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


def categorias_de_investimento(session: Session, month: date):
    """Categorias cujas separações formam capital investido."""
    return [
        v
        for v in cat.resolve_all(session, month)
        if v.counts_as_investment_capital
    ]


def capital_destinado(session: Session, month: date) -> int:
    """Soma de tudo já separado para categorias de capital, até o mês."""
    alvo = month_start(month)
    return sum(
        repo.sum_allocations_until(session, alvo, vista.id)
        for vista in categorias_de_investimento(session, alvo)
    )


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
