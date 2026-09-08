"""Período de análise: mês ou ano.

As telas escolhem um :class:`Period` e o passam adiante. Nenhum service
recebe strings montadas na interface — só este objeto, que sabe traduzir a
escolha em uma lista de meses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .utils import month_label, month_start


class PeriodMode(str, Enum):
    """Como o período é interpretado."""

    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True)
class Period:
    """Um recorte de tempo: um mês específico ou um ano inteiro.

    ``anchor`` é sempre o primeiro dia do mês de referência. No modo anual,
    só o ano dele importa.
    """

    mode: PeriodMode
    anchor: date

    # ------------------------------------------------------------------
    # Construtores
    # ------------------------------------------------------------------
    @classmethod
    def of_month(cls, value: date) -> "Period":
        """Período de um único mês."""
        return cls(PeriodMode.MONTH, month_start(value))

    @classmethod
    def of_year(cls, year: int) -> "Period":
        """Período de um ano inteiro."""
        return cls(PeriodMode.YEAR, date(year, 1, 1))

    # ------------------------------------------------------------------
    # Propriedades
    # ------------------------------------------------------------------
    @property
    def is_month(self) -> bool:
        """Se o período é mensal."""
        return self.mode is PeriodMode.MONTH

    @property
    def is_year(self) -> bool:
        """Se o período é anual."""
        return self.mode is PeriodMode.YEAR

    @property
    def year(self) -> int:
        """Ano de referência."""
        return self.anchor.year

    @property
    def month(self) -> date:
        """Mês de referência.

        No modo anual devolve janeiro do ano — use :attr:`months` para
        percorrer o período inteiro.
        """
        return self.anchor

    @property
    def months(self) -> list[date]:
        """Meses cobertos pelo período, em ordem."""
        if self.is_month:
            return [self.anchor]
        return [date(self.anchor.year, mes, 1) for mes in range(1, 13)]

    @property
    def start(self) -> date:
        """Primeiro mês do período."""
        return self.months[0]

    @property
    def end(self) -> date:
        """Último mês do período."""
        return self.months[-1]

    @property
    def label(self) -> str:
        """Rótulo legível: ``setembro/2026`` ou ``2026``."""
        return month_label(self.anchor) if self.is_month else str(self.anchor.year)

    # ------------------------------------------------------------------
    # Navegação
    # ------------------------------------------------------------------
    def shift(self, passos: int) -> "Period":
        """Avança (ou volta) períodos inteiros, preservando o modo."""
        if self.is_month:
            total = (self.anchor.year * 12 + self.anchor.month - 1) + passos
            ano, mes = divmod(total, 12)
            return Period(self.mode, date(ano, mes + 1, 1))
        return Period(self.mode, date(self.anchor.year + passos, 1, 1))

    def as_month(self) -> "Period":
        """Mesma âncora, no modo mensal."""
        return Period(PeriodMode.MONTH, self.anchor)

    def as_year(self) -> "Period":
        """Mesmo ano, no modo anual."""
        return Period(PeriodMode.YEAR, date(self.anchor.year, 1, 1))

    def contains(self, mes: date) -> bool:
        """Se um mês pertence ao período."""
        alvo = month_start(mes)
        return alvo in self.months if self.is_month else alvo.year == self.anchor.year
