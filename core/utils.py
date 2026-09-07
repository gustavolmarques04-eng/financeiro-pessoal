"""Funções utilitárias puras: dinheiro em centavos, meses e formatação.

Regra do projeto: **dinheiro nunca é float**. Tudo circula como ``int`` de
centavos e só vira ``Decimal``/texto na borda da interface.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

CENTS = Decimal("0.01")

MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


# --------------------------------------------------------------------------
# Dinheiro
# --------------------------------------------------------------------------
def to_cents(value: Decimal | int | float | str) -> int:
    """Converte um valor monetário para centavos inteiros (meio para cima).

    ``float`` é aceito apenas por conveniência da interface; a conversão passa
    por ``str`` para não herdar o erro binário do ponto flutuante.
    """
    if isinstance(value, int):
        return value * 100
    dec = Decimal(str(value))
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_decimal(cents: int) -> Decimal:
    """Converte centavos inteiros para ``Decimal`` com duas casas."""
    return (Decimal(cents) / Decimal(100)).quantize(CENTS)


def format_brl(cents: int) -> str:
    """Formata centavos como moeda brasileira: ``R$ 1.234,56``."""
    negativo = cents < 0
    inteiro, resto = divmod(abs(cents), 100)
    texto = f"{inteiro:,}".replace(",", ".")
    valor = f"R$ {texto},{resto:02d}"
    return f"-{valor}" if negativo else valor


def pct_from_bp(basis_points: int) -> Decimal:
    """Converte pontos-base (4900) em percentual decimal (``49.00``)."""
    return (Decimal(basis_points) / Decimal(100)).quantize(CENTS)


def bp_from_pct(percent: Decimal | float | str) -> int:
    """Converte percentual (``49.0``) em pontos-base inteiros (``4900``)."""
    dec = Decimal(str(percent))
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_bp(cents: int, basis_points: int) -> int:
    """Aplica um percentual em pontos-base sobre um valor em centavos."""
    produto = Decimal(cents) * Decimal(basis_points) / Decimal(10_000)
    return int(produto.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def split_proportionally(total_cents: int, weights_bp: Sequence[int]) -> list[int]:
    """Reparte ``total_cents`` pelos pesos sem perder nem inventar centavos.

    Usa o método do maior resto: cada fatia recebe o piso da sua parte e os
    centavos que sobram vão para as maiores frações. A soma do resultado é
    sempre exatamente ``total_cents`` (quando os pesos somam 10.000).
    """
    if not weights_bp:
        return []
    total_peso = sum(weights_bp)
    if total_peso <= 0 or total_cents == 0:
        return [0] * len(weights_bp)

    exatos = [Decimal(total_cents) * Decimal(w) / Decimal(total_peso) for w in weights_bp]
    pisos = [int(v // 1) for v in exatos]
    sobra = total_cents - sum(pisos)

    if sobra:
        fracoes = sorted(
            range(len(exatos)),
            key=lambda i: (exatos[i] - pisos[i], weights_bp[i]),
            reverse=True,
        )
        passo = 1 if sobra > 0 else -1
        for i in range(abs(sobra)):
            pisos[fracoes[i % len(pisos)]] += passo
    return pisos


def split_installments(total_cents: int, count: int) -> list[int]:
    """Divide uma compra em ``count`` parcelas cujo total fecha exatamente.

    ``R$ 100,00`` em 3 vezes vira ``[33,33; 33,33; 33,34]``: as últimas
    parcelas absorvem os centavos restantes.
    """
    if count < 1:
        raise ValueError("Número de parcelas deve ser >= 1.")
    base, resto = divmod(abs(total_cents), count)
    parcelas = [base] * count
    for i in range(resto):
        parcelas[count - 1 - i] += 1
    if total_cents < 0:
        parcelas = [-p for p in parcelas]
    return parcelas


# --------------------------------------------------------------------------
# Meses
# --------------------------------------------------------------------------
def month_start(value: date) -> date:
    """Normaliza qualquer data para o primeiro dia do seu mês."""
    return date(value.year, value.month, 1)


def add_months(value: date, months: int) -> date:
    """Soma (ou subtrai) meses preservando o primeiro dia do mês."""
    total = (value.year * 12 + value.month - 1) + months
    ano, mes = divmod(total, 12)
    return date(ano, mes + 1, 1)


def month_end(value: date) -> date:
    """Último dia do mês de ``value``."""
    ultimo = calendar.monthrange(value.year, value.month)[1]
    return date(value.year, value.month, ultimo)


def month_label(value: date) -> str:
    """Rótulo legível de um mês: ``setembro/2026``."""
    return f"{MESES_PT[value.month - 1]}/{value.year}"


def month_short(value: date) -> str:
    """Rótulo curto de um mês: ``set/26``."""
    return f"{MESES_PT[value.month - 1][:3]}/{str(value.year)[2:]}"


def month_range(inicio: date, fim: date) -> list[date]:
    """Lista de primeiros-dias-de-mês entre ``inicio`` e ``fim`` (inclusive)."""
    atual, saida = month_start(inicio), []
    limite = month_start(fim)
    while atual <= limite:
        saida.append(atual)
        atual = add_months(atual, 1)
    return saida


def sum_cents(values: Iterable[int]) -> int:
    """Soma segura de centavos, tratando ``None`` como zero."""
    return sum(v or 0 for v in values)
