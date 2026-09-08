"""Página inicial: análise rápida do período e atalho para registrar gasto.

A tabela completa de separações fica em 🎯 Separações; aqui só aparece o
aviso do que falta — vindo do mesmo service, para nunca divergir.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core.database import session_scope
from core.utils import month_label, month_short
from ui.forms import formulario_gasto
from ui.money import chart_hover, chart_values
from ui.shared import (
    NAVY,
    TEAL,
    aviso_valores_ocultos,
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    dinheiro,
    dinheiro_html,
    garantir_banco,
    linha,
    mostrar_cartoes,
    percentual,
    privacidade,
    secao,
    seletor_periodo,
    subtitulo,
    valor_colorido,
)

configurar_pagina("Início")
garantir_banco()

cabecalho("💰 Painel financeiro", "Seu período em poucos segundos.", chave="dash")
periodo = seletor_periodo("dash")
oculto = privacidade()


# --------------------------------------------------------------------------
# Leitura de dados (uma única sessão para a página inteira)
# --------------------------------------------------------------------------
with session_scope() as session:
    resumo = budget.get_resumo_periodo(session, periodo)
    resumo_inv = investimentos.get_resumo(session, periodo)
    mes_ref = budget.mes_de_referencia(session, periodo)
    plano = budget.get_month_plan(session, mes_ref)
    resumo_sep = budget.resumo_separacoes(session, mes_ref)
    envelopes = budget.envelopes(session, periodo)
    metas = budget.metas(session, periodo)

    if periodo.is_year:
        receitas_mes = repo.incomes_by_month(session, periodo)
        gastos_mes = repo.expenses_by_month(session, periodo)
        dividendos_mes = repo.dividends_by_month(session, periodo)
        patrimonio_mes = dict(budget.serie_patrimonio(session, periodo.months))
    else:
        conhecidos = [m for m in repo.known_months(session) if m <= periodo.month]
        serie_patrimonio = budget.serie_patrimonio(session, conhecidos)
        serie_div = investimentos.serie_dividendos(session, conhecidos)


# --------------------------------------------------------------------------
# Cards principais
# --------------------------------------------------------------------------
apoio_patrimonio = (
    f"posição de {month_label(resumo.patrimonio.posicao)}"
    if resumo.patrimonio.informado
    else "informe o fechamento do mês"
)
apoio_resultado = (
    percentual(resumo_inv.rentabilidade_pct) + " sobre o capital"
    if resumo_inv.rentabilidade_valida
    else "aguardando o fechamento"
)

mostrar_cartoes(
    [
        (
            "Recebido",
            dinheiro(resumo.recebido_cents),
            f"base do rateio: {dinheiro(resumo.base_cents)}"
            if resumo.base_cents != resumo.recebido_cents
            else periodo.label,
            "",
        ),
        ("Gasto", dinheiro(resumo.gasto_cents), periodo.label, ""),
        ("Patrimônio total", dinheiro(resumo.patrimonio.total_cents), apoio_patrimonio, ""),
        (
            "Resultado dos investimentos",
            dinheiro(resumo_inv.resultado_cents)
            if resumo_inv.informado
            else "Não informado",
            apoio_resultado,
            ("negativo" if resumo_inv.resultado_cents < 0 else "positivo")
            if resumo_inv.informado
            else "",
        ),
    ]
)


# --------------------------------------------------------------------------
# Aviso de separações (mesmo service da página Separações)
# --------------------------------------------------------------------------
if periodo.is_month and resumo_sep.total:
    if resumo_sep.tudo_feito:
        st.success(
            f"Todas as separações de {month_label(resumo_sep.month)} estão concluídas.",
            icon="✅",
        )
    else:
        st.warning(
            f"{resumo_sep.pendentes} separação(ões) pendente(s) · "
            f"falta separar {dinheiro(resumo_sep.falta_cents)}",
            icon="⚠️",
        )
    if st.button("Ver separações", use_container_width=True, key="ir_separacoes"):
        st.switch_page("pages/separacoes.py")


# --------------------------------------------------------------------------
# Registrar gasto (mesmo formulário e service da página Gastos)
# --------------------------------------------------------------------------
with st.expander("➕ Registrar gasto", expanded=False):
    if formulario_gasto(
        "home", mes_referencia=mes_ref, rotulo_botao="Salvar gasto", compacto=True
    ):
        st.rerun()


# --------------------------------------------------------------------------
# Disponível para gastar
# --------------------------------------------------------------------------
secao("Disponível para gastar")

subtitulo("Orçamento do mês")
if not plano.gastos:
    aviso_vazio("Nenhuma categoria de orçamento mensal ativa.")
else:
    colunas = st.columns(min(len(plano.gastos), 3))
    for indice, item in enumerate(plano.gastos):
        with colunas[indice % len(colunas)]:
            with st.container(border=True):
                st.markdown(f"**{item.categoria.label}**")
                st.markdown(
                    linha("Orçado", dinheiro_html(item.orcamento_cents))
                    + linha("Gasto", dinheiro_html(item.gasto_cents))
                    + linha("Disponível", valor_colorido(item.disponivel_cents)),
                    unsafe_allow_html=True,
                )
    st.caption(
        f"Orçamento de {month_label(plano.month)}. Estas categorias não acumulam."
    )

subtitulo("Saldos acumulados")
if not envelopes:
    aviso_vazio("Nenhuma categoria acumulativa ativa.")
else:
    colunas = st.columns(min(len(envelopes), 3))
    for indice, item in enumerate(envelopes):
        with colunas[indice % len(colunas)]:
            with st.container(border=True):
                st.markdown(f"**{item.categoria.label}**")
                st.markdown(
                    linha("Saldo disponível", valor_colorido(item.saldo_cents))
                    + linha("Gasto no período", dinheiro_html(item.gasto_no_periodo_cents)),
                    unsafe_allow_html=True,
                )
    st.caption("Saldo acumulado de todos os meses, menos os gastos da categoria.")


# --------------------------------------------------------------------------
# Metas
# --------------------------------------------------------------------------
if metas:
    secao("Metas")
    for meta in metas:
        col_grafico, col_texto = st.columns([2, 3])
        with col_grafico:
            # No modo privado a rosca recebe proporções, não centavos: assim
            # nem o JSON do gráfico carrega o valor guardado.
            valores = (
                [meta.percentual, 100 - meta.percentual]
                if oculto
                else [meta.atual_cents, meta.falta_cents]
            )
            rosca = go.Figure(
                go.Pie(
                    values=valores,
                    labels=["Guardado", "Falta"],
                    hole=0.65,
                    marker_colors=[TEAL, "#E4EAF1"],
                    sort=False,
                    textinfo="none",
                    hoverinfo="skip" if oculto else "label+value",
                )
            )
            rosca.update_layout(
                showlegend=False,
                margin=dict(t=6, b=6, l=6, r=6),
                height=170,
                annotations=[
                    dict(
                        text=f"<b>{percentual(meta.percentual)}</b>",
                        x=0.5,
                        y=0.5,
                        showarrow=False,
                        font_size=20,
                    )
                ],
            )
            st.plotly_chart(
                rosca, use_container_width=True, config={"displayModeBar": False}
            )
        with col_texto:
            st.markdown(f"**{meta.categoria.label}**")
            st.markdown(
                linha("Atual", dinheiro_html(meta.atual_cents))
                + linha("Meta", dinheiro_html(meta.meta_cents))
                + linha("Falta", dinheiro_html(meta.falta_cents)),
                unsafe_allow_html=True,
            )
            if not meta.informado:
                st.caption("Valor vem do 📅 Fechamento mensal. Ainda não informado.")


# --------------------------------------------------------------------------
# Investimentos
# --------------------------------------------------------------------------
secao("Investimentos")
mostrar_cartoes(
    [
        (
            "Investimentos atuais",
            dinheiro(resumo_inv.valor_atual_cents)
            if resumo_inv.informado
            else "Não informado",
            f"posição de {month_label(resumo_inv.posicao)}"
            if resumo_inv.informado
            else "preencha no fechamento",
            "",
        ),
        (
            "Capital destinado",
            dinheiro(resumo_inv.capital_destinado_cents),
            "separado ao longo dos meses",
            "",
        ),
        (
            "Resultado total",
            dinheiro(resumo_inv.resultado_cents) if resumo_inv.informado else "—",
            apoio_resultado,
            ("negativo" if resumo_inv.resultado_cents < 0 else "positivo")
            if resumo_inv.informado
            else "",
        ),
    ],
    por_linha=3,
)


# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------
def _grafico_barras(titulo: str, dados: dict, cor: str) -> None:
    """Barra mensal de um valor monetário, respeitando o modo privado."""
    subtitulo(titulo)
    valores = chart_values(list(dados.values()), oculto)
    if valores is None:
        aviso_valores_ocultos()
        return
    figura = go.Figure(
        go.Bar(
            x=[month_short(m) for m in dados],
            y=valores,
            marker_color=cor,
            hovertemplate=chart_hover(oculto),
        )
    )
    figura.update_layout(
        height=230,
        margin=dict(t=8, b=8, l=8, r=8),
        plot_bgcolor="white",
        yaxis_title=None,
    )
    figura.update_yaxes(gridcolor="#EEF3F7")
    st.plotly_chart(figura, use_container_width=True, config={"displayModeBar": False})


if periodo.is_year:
    secao(f"Evolução em {periodo.label}")
    mostrar_cartoes(
        [
            ("Receita no ano", dinheiro(resumo.recebido_cents), periodo.label, ""),
            ("Gasto no ano", dinheiro(resumo.gasto_cents), periodo.label, ""),
            ("Dividendos no ano", dinheiro(resumo.dividendos_cents), periodo.label, ""),
        ],
        por_linha=3,
    )
    _grafico_barras("Receitas por mês", receitas_mes, TEAL)
    _grafico_barras("Gastos por mês", gastos_mes, "#B3261E")
    _grafico_barras("Patrimônio por mês", patrimonio_mes, NAVY)
    _grafico_barras("Dividendos por mês", dividendos_mes, "#8A5A00")
else:
    secao("Evolução do patrimônio")
    if not serie_patrimonio:
        aviso_vazio("Ainda não há meses com dados para montar a evolução.")
    else:
        valores = chart_values([v for _, v in serie_patrimonio], oculto)
        if valores is None:
            aviso_valores_ocultos()
        else:
            rotulos = [month_short(m) for m, _ in serie_patrimonio]
            if len(serie_patrimonio) < 3:
                figura = go.Figure(
                    go.Bar(
                        x=rotulos,
                        y=valores,
                        marker_color=NAVY,
                        hovertemplate=chart_hover(oculto),
                    )
                )
                figura.update_layout(bargap=0.75)
            else:
                figura = go.Figure(
                    go.Scatter(
                        x=rotulos,
                        y=valores,
                        mode="lines+markers",
                        line=dict(color=NAVY, width=3),
                        marker=dict(size=8),
                        hovertemplate=chart_hover(oculto),
                        fill="tozeroy",
                        fillcolor="rgba(23,50,77,0.08)",
                    )
                )
            figura.update_layout(
                height=250,
                margin=dict(t=8, b=8, l=8, r=8),
                plot_bgcolor="white",
            )
            figura.update_yaxes(gridcolor="#EEF3F7")
            st.plotly_chart(
                figura, use_container_width=True, config={"displayModeBar": False}
            )

    secao("Dividendos")
    if not any(valor for _, valor, _ in serie_div):
        aviso_vazio("Nenhum dividendo informado ainda. Registre no 📅 Fechamento mensal.")
    elif oculto:
        aviso_valores_ocultos()
    else:
        grafico = go.Figure()
        grafico.add_bar(
            x=[month_short(m) for m, _, _ in serie_div],
            y=[v / 100 for _, v, _ in serie_div],
            name="No mês",
            marker_color=TEAL,
            hovertemplate=chart_hover(False),
        )
        grafico.add_scatter(
            x=[month_short(m) for m, _, _ in serie_div],
            y=[a / 100 for _, _, a in serie_div],
            name="Acumulado",
            mode="lines+markers",
            line=dict(color=NAVY, width=3),
            hovertemplate=chart_hover(False),
        )
        grafico.update_layout(
            height=250,
            margin=dict(t=8, b=8, l=8, r=8),
            plot_bgcolor="white",
            legend=dict(orientation="h", y=1.15, x=0),
        )
        grafico.update_yaxes(gridcolor="#EEF3F7")
        st.plotly_chart(grafico, use_container_width=True, config={"displayModeBar": False})

st.caption(
    f"Dividendos em {periodo.label}: {dinheiro(resumo.dividendos_cents)}. "
    "Não entram na renda automaticamente."
)
