"""Formatação de dinheiro com modo privacidade — **ponto único**.

Toda exibição monetária do aplicativo passa por aqui. No modo privado o
valor **não é renderizado**: a função devolve a máscara, então o número
sequer chega ao HTML. Esconder por CSS não serviria — bastaria inspecionar
a página para ver o saldo.

Funções puras, sem importar Streamlit, para poderem ser testadas direto.
"""

from __future__ import annotations

from core.utils import format_brl

#: O que aparece no lugar de um valor quando o modo privado está ligado.
MASCARA = "R$ ••••••"
MASCARA_CURTA = "••••"


def display_money(cents: int | None, privacy: bool = False) -> str:
    """Valor em reais, ou a máscara quando o modo privado está ligado.

    É a única porta de saída de qualquer valor monetário para a tela.
    """
    if privacy:
        return MASCARA
    if cents is None:
        return "—"
    return format_brl(cents)


def money_md(cents: int | None, privacy: bool = False) -> str:
    """Igual a :func:`display_money`, pronto para markdown.

    O Streamlit interpreta ``$...$`` como LaTeX; escapar o cifrão evita que
    dois valores no mesmo texto virem fórmula.
    """
    return display_money(cents, privacy).replace("$", r"\$")


def money_plain(cents: int | None, privacy: bool = False) -> str:
    """Igual a :func:`display_money`, para HTML e células de tabela.

    Nestes contextos o markdown não roda, então o cifrão vai sem escape.
    """
    return display_money(cents, privacy)


def display_percent(valor: float, privacy: bool = False, casas: int = 1) -> str:
    """Percentual.

    Percentual não revela saldo, então continua visível no modo privado —
    é o que permite manter a barra de progresso da meta.
    """
    return f"{valor:.{casas}f}%"


def display_signed(cents: int, privacy: bool = False) -> str:
    """Valor com sinal explícito, para ganhos e perdas."""
    if privacy:
        return MASCARA
    sinal = "+" if cents > 0 else ""
    return f"{sinal}{format_brl(cents)}"


def chart_values(valores: list[int], privacy: bool = False) -> list[float] | None:
    """Série de um gráfico monetário, ou ``None`` no modo privado.

    Devolver ``None`` obriga quem chama a não desenhar o gráfico: os
    números não são enviados ao navegador de forma alguma.
    """
    if privacy:
        return None
    return [c / 100 for c in valores]


def chart_hover(privacy: bool = False, prefixo: str = "") -> str:
    """Template de tooltip que não revela valores no modo privado."""
    if privacy:
        return f"{prefixo}%{{x}}<extra></extra>"
    return f"{prefixo}%{{x}}: R$ %{{y:,.2f}}<extra></extra>"
