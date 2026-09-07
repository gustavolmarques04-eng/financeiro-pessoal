"""Regras de investimentos e dividendos.

Esta versão não controla ativos individuais. O aplicativo sabe quanto foi
**separado** para Independência financeira ao longo do tempo (capital
destinado) e quanto o usuário informou de patrimônio investido no
fechamento — a diferença é o resultado estimado.

Dividendos ficam de fora dessa conta de propósito: se entrassem, seriam
contados duas vezes (uma no resultado, outra quando virassem renda).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from . import repositories as repo
from .models import Category
from .utils import month_start


@dataclass(frozen=True)
class ResumoInvestimentos:
    """Retrato dos investimentos em um mês."""

    month: date
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


def capital_destinado(session: Session, month: date) -> int:
    """Soma de tudo que já foi separado para Independência até o mês."""
    return repo.sum_allocations_until(session, month, Category.INDEPENDENCIA)


def get_resumo(session: Session, month: date) -> ResumoInvestimentos:
    """Resumo de investimentos do mês.

    O valor atual vem do fechamento mais recente até o mês. Enquanto ele não
    for informado, o resultado fica zerado em vez de mostrar um prejuízo de
    100% que não existe — quem separou dinheiro mas ainda não preencheu o
    fechamento não perdeu nada.
    """
    alvo = month_start(month)
    fechamento = repo.latest_closing_until(session, alvo)
    destinado = capital_destinado(session, alvo)
    if fechamento is None or fechamento.investimentos_cents == 0:
        return ResumoInvestimentos(alvo, destinado, 0, informado=False)
    return ResumoInvestimentos(
        alvo, destinado, fechamento.investimentos_cents, informado=True
    )


def dividendos_do_mes(session: Session, month: date) -> int:
    """Dividendos informados no fechamento do mês."""
    fechamento = repo.get_closing(session, month)
    return fechamento.dividendos_cents if fechamento else 0


def dividendos_acumulados(session: Session, month: date) -> int:
    """Soma dos dividendos informados até o mês (inclusive)."""
    alvo = month_start(month)
    return sum(
        f.dividendos_cents for f in repo.list_closings(session) if f.month <= alvo
    )


def serie_dividendos(session: Session, meses: list[date]) -> list[tuple[date, int, int]]:
    """Série ``(mês, dividendos do mês, acumulado)`` para os gráficos."""
    por_mes = {f.month: f.dividendos_cents for f in repo.list_closings(session)}
    saida, acumulado = [], 0
    for mes in meses:
        valor = por_mes.get(mes, 0)
        acumulado += valor
        saida.append((mes, valor, acumulado))
    return saida


def serie_investimentos(
    session: Session, meses: list[date]
) -> list[tuple[date, int, int]]:
    """Série ``(mês, capital destinado, valor atual)`` para os gráficos."""
    saida = []
    for mes in meses:
        resumo = get_resumo(session, mes)
        saida.append((mes, resumo.capital_destinado_cents, resumo.valor_atual_cents))
    return saida
