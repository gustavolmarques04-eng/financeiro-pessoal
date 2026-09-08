"""Testes do modo privacidade e da camada única de formatação de dinheiro."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from ui import money
from ui.money import (
    MASCARA,
    chart_hover,
    chart_values,
    display_money,
    display_percent,
    display_signed,
    money_md,
    money_plain,
)

RAIZ = Path(__file__).resolve().parent.parent
VALOR = 423_075  # R$ 4.230,75


# --------------------------------------------------------------------------
# 4 — cards
# --------------------------------------------------------------------------
def test_privacidade_mascara_valor_de_card() -> None:
    """No modo privado o número não é produzido, só a máscara."""
    assert display_money(VALOR, privacy=False) == "R$ 4.230,75"
    assert display_money(VALOR, privacy=True) == MASCARA
    assert "4.230" not in display_money(VALOR, privacy=True)
    assert "4230" not in display_money(VALOR, privacy=True)


def test_mascara_vale_para_qualquer_valor() -> None:
    """Zero, negativo e ausente também são mascarados."""
    for valor in (0, -1, -999_999, 1, 10**9):
        oculto = display_money(valor, privacy=True)
        assert oculto == MASCARA
        assert not re.search(r"\d", oculto), f"vazou dígito para {valor}"
    assert display_money(None, privacy=True) == MASCARA


def test_valor_com_sinal_tambem_e_mascarado() -> None:
    """Ganho e perda não vazam nem pelo sinal."""
    assert display_signed(1234, privacy=False) == "+R$ 12,34"
    assert display_signed(-1234, privacy=False) == "-R$ 12,34"
    assert display_signed(-1234, privacy=True) == MASCARA


# --------------------------------------------------------------------------
# 5 — tabelas e markdown
# --------------------------------------------------------------------------
def test_privacidade_mascara_dinheiro_em_tabelas() -> None:
    """Células de tabela usam a mesma camada e não escapam da máscara."""
    linhas = [
        {"Descrição": "Jantar", "Valor": money_plain(15_000, privacy=True)},
        {"Descrição": "Mercado", "Valor": money_plain(32_150, privacy=True)},
    ]
    for linha in linhas:
        assert linha["Valor"] == MASCARA
        assert not re.search(r"\d", linha["Valor"])


def test_versao_markdown_escapa_cifrao_e_respeita_privacidade() -> None:
    """No markdown o cifrão é escapado; no modo privado nada de número."""
    assert money_md(VALOR) == r"R\$ 4.230,75"
    assert money_md(VALOR, privacy=True) == r"R\$ ••••••"
    assert not re.search(r"\d", money_md(VALOR, privacy=True))


def test_percentual_continua_visivel() -> None:
    """Percentual não revela saldo, então segue visível."""
    assert display_percent(29.6, privacy=True) == "29.6%"
    assert display_percent(29.6, privacy=False) == "29.6%"


# --------------------------------------------------------------------------
# 6 — gráficos
# --------------------------------------------------------------------------
def test_privacidade_nao_envia_valores_para_o_grafico() -> None:
    """No modo privado a série vira ``None``: nada vai para o navegador."""
    valores = [100_00, 250_50, 999_99]
    assert chart_values(valores, privacy=False) == [100.0, 250.5, 999.99]
    assert chart_values(valores, privacy=True) is None


def test_tooltip_nao_revela_valor_no_modo_privado() -> None:
    """O template de tooltip perde a parte monetária."""
    normal = chart_hover(privacy=False)
    oculto = chart_hover(privacy=True)

    assert "R$" in normal and "%{y" in normal
    assert "R$" not in oculto
    assert "%{y" not in oculto, "o eixo Y não pode aparecer no tooltip privado"


# --------------------------------------------------------------------------
# Camada central: nenhuma página formata dinheiro por fora
# --------------------------------------------------------------------------
def test_paginas_nao_chamam_format_brl_direto() -> None:
    """Toda exibição monetária passa por ``ui.money`` / ``ui.shared``.

    ``format_brl`` é o formatador cru; usá-lo numa página burlaria o modo
    privacidade.
    """
    infratores = []
    for arquivo in sorted((RAIZ / "pages").glob("*.py")):
        codigo = arquivo.read_text(encoding="utf-8")
        if re.search(r"(?<![\w.])format_brl\s*\(", codigo):
            infratores.append(arquivo.name)
    assert infratores == [], (
        "estas páginas formatam dinheiro fora da camada de privacidade: "
        f"{infratores}"
    )


def test_todas_as_funcoes_de_dinheiro_aceitam_privacidade() -> None:
    """Nenhuma função de exibição monetária esquece o parâmetro."""
    monetarias = [
        money.display_money,
        money.money_md,
        money.money_plain,
        money.display_signed,
        money.chart_values,
        money.chart_hover,
    ]
    for funcao in monetarias:
        assinatura = inspect.signature(funcao)
        assert "privacy" in assinatura.parameters, funcao.__name__


@pytest.mark.parametrize("privacidade", [True, False])
def test_formatadores_nunca_quebram(privacidade: bool) -> None:
    """As funções aguentam qualquer entrada sem levantar exceção."""
    for valor in (0, 1, -1, 10**12, None):
        assert isinstance(display_money(valor, privacidade), str)
        assert isinstance(money_md(valor, privacidade), str)
        assert isinstance(money_plain(valor, privacidade), str)


# --------------------------------------------------------------------------
# Gráficos não podem vazar valores nem pelo payload
# --------------------------------------------------------------------------
def test_rosca_de_meta_nao_carrega_centavos_no_modo_privado() -> None:
    """A rosca da meta recebe proporções, não o valor guardado.

    ``hoverinfo="skip"`` esconde o tooltip, mas os números continuariam no
    JSON do gráfico e apareceriam para quem inspecionasse a página.
    """
    codigo = (RAIZ / "pages" / "dashboard.py").read_text(encoding="utf-8")
    trecho = codigo[codigo.index("go.Pie(") - 800 : codigo.index("go.Pie(")]

    assert "meta.percentual" in trecho, "a rosca precisa usar percentual quando oculto"
    assert "if oculto" in trecho


def test_paginas_nao_passam_centavos_direto_para_o_plotly() -> None:
    """Séries monetárias passam por ``chart_values``, que respeita a privacidade."""
    infratores = []
    for arquivo in sorted((RAIZ / "pages").glob("*.py")):
        codigo = arquivo.read_text(encoding="utf-8")
        if "go.Figure" not in codigo:
            continue
        if re.search(r"y=\[[^\]]*_cents[^\]]*\]", codigo):
            infratores.append(arquivo.name)
    assert infratores == [], infratores
