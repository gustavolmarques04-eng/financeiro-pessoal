"""Página inicial: retrato do mês e do patrimônio acompanhado.

Todos os números vêm de ``core.budget_service`` e ``core.investment_service``.
Esta tela só apresenta.
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import streamlit as st

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core.budget_service import SeparacaoLinha
from core.database import session_scope
from core.models import Category
from core.utils import format_brl, month_label, month_short, to_cents, to_decimal
from ui.shared import (
    brl,
    NAVY,
    TEAL,
    VERDE,
    VERMELHO,
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    garantir_banco,
    mostrar_cartoes,
    secao,
    selo,
    seletor_mes,
    valor_colorido,
)

configurar_pagina("Início")
garantir_banco()

cabecalho("💰 Painel financeiro", "Seu mês, suas separações e seu patrimônio.")
mes = seletor_mes("dash")


# --------------------------------------------------------------------------
# Leitura de dados (uma única sessão para a página inteira)
# --------------------------------------------------------------------------
with session_scope() as session:
    plano = budget.get_month_plan(session, mes)
    patrimonio = budget.get_patrimonio(session, mes)
    resumo_inv = investimentos.get_resumo(session, mes)
    reserva_atual, reserva_meta, reserva_pct = budget.progresso_reserva(session, mes)
    div_mes = investimentos.dividendos_do_mes(session, mes)
    div_total = investimentos.dividendos_acumulados(session, mes)
    meses_conhecidos = [m for m in repo.known_months(session) if m <= mes]
    serie_patrimonio = budget.serie_patrimonio(session, meses_conhecidos)
    serie_div = investimentos.serie_dividendos(session, meses_conhecidos)
    saldo_viagem = budget.saldo_viagem(session, mes)
    saldo_compras = budget.saldo_compras(session, mes)


# --------------------------------------------------------------------------
# Cartões do topo
# --------------------------------------------------------------------------
apoio_base = (
    f"Base do rateio: {brl(plano.base_cents)}"
    if plano.base_cents != plano.recebido_cents
    else "Base do rateio"
)
mostrar_cartoes(
    [
        ("Mês atual", month_label(mes), f"Revisão {plano.revision} do plano", ""),
        ("Recebido no mês", brl(plano.recebido_cents), apoio_base, ""),
        ("Gasto no mês", brl(plano.gasto_cents), "Inclui parcelas do mês", ""),
        (
            "Patrimônio total",
            brl(patrimonio.total_cents),
            "Reserva + investimentos + envelopes"
            if patrimonio.informado
            else "Informe o fechamento do mês",
            "",
        ),
    ]
)


# --------------------------------------------------------------------------
# Separações do mês
# --------------------------------------------------------------------------
secao("Separações do mês")

if plano.base_cents == 0:
    aviso_vazio("Nenhuma receita neste mês ainda. Registre em 💰 Receitas para ver o plano.")
else:
    if plano.sobra_reserva_cents:
        st.caption(
            f"↪️ {brl(plano.sobra_reserva_cents)} não são mais necessários na "
            "Reserva e foram somados à Independência financeira."
        )

    def _bloco_separacao(linha: SeparacaoLinha) -> None:
        """Cartão de uma categoria com números e checkbox de confirmação."""
        with st.container(border=True):
            st.markdown(
                f"**{linha.categoria.value}** &nbsp; {selo(linha.status)}",
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="fin-linha"><span class="chave">Planejado</span>'
                f'<span class="val">{format_brl(linha.planejado_cents)}</span></div>'
                f'<div class="fin-linha"><span class="chave">Já separado</span>'
                f'<span class="val">{format_brl(linha.separado_cents)}</span></div>'
                f'<div class="fin-linha"><span class="chave">Falta separar</span>'
                f"<span class=\"val\">{format_brl(linha.falta_cents)}</span></div>",
                unsafe_allow_html=True,
            )

            # A chave carrega o plano vigente: se o plano mudar, o widget é
            # recriado e volta a refletir o estado real (desmarcado).
            chave = (
                f"sep_{linha.categoria.name}_{mes}_{plano.revision}"
                f"_{linha.planejado_cents}_{linha.separado_cents}"
            )
            marcado = st.checkbox(
                "Já separei este valor", value=linha.feito, key=chave,
                disabled=linha.planejado_cents == 0 and linha.separado_cents == 0,
            )

            if marcado and not linha.feito:
                with session_scope() as s:
                    budget.confirmar_separacao(s, mes, linha.categoria)
                st.rerun()
            elif not marcado and linha.feito:
                with session_scope() as s:
                    budget.desfazer_separacao(s, mes, linha.categoria)
                st.rerun()

            with st.expander("Ajustar valor separado"):
                novo = st.number_input(
                    "Valor efetivamente separado (R$)",
                    min_value=0.0,
                    step=10.0,
                    value=float(to_decimal(linha.separado_cents)),
                    key=f"aj_{linha.categoria.name}_{mes}",
                )
                if st.button("Salvar ajuste", key=f"btaj_{linha.categoria.name}_{mes}"):
                    with session_scope() as s:
                        budget.ajustar_separacao(s, mes, linha.categoria, to_cents(novo))
                    st.rerun()

    linhas = plano.separacoes
    for inicio in range(0, len(linhas), 2):
        colunas = st.columns(2)
        for coluna, linha in zip(colunas, linhas[inicio : inicio + 2]):
            with coluna:
                _bloco_separacao(linha)

    st.caption(
        f"Total planejado {brl(plano.total_planejado_cents)} · "
        f"falta separar {brl(plano.total_falta_separar_cents)}"
    )


# --------------------------------------------------------------------------
# Quanto posso gastar
# --------------------------------------------------------------------------
secao("Quanto posso gastar")

colunas = st.columns(len(plano.gastos))
for coluna, linha in zip(colunas, plano.gastos):
    with coluna:
        with st.container(border=True):
            st.markdown(f"**{linha.categoria.value}**")
            st.markdown(
                f'<div class="fin-linha"><span class="chave">Orçamento</span>'
                f'<span class="val">{format_brl(linha.orcamento_cents)}</span></div>'
                f'<div class="fin-linha"><span class="chave">Já gasto</span>'
                f'<span class="val">{format_brl(linha.gasto_cents)}</span></div>'
                f'<div class="fin-linha"><span class="chave">Disponível</span>'
                f"<span class=\"val\">{valor_colorido(linha.disponivel_cents)}</span></div>",
                unsafe_allow_html=True,
            )
st.caption("Estas três categorias não acumulam: recomeçam a cada mês.")


# --------------------------------------------------------------------------
# Envelopes acumulativos
# --------------------------------------------------------------------------
secao("Envelopes acumulativos")
mostrar_cartoes(
    [
        (
            "Saldo de viagem",
            brl(saldo_viagem),
            "Separações confirmadas − gastos de Viagem",
            "negativo" if saldo_viagem < 0 else "",
        ),
        (
            "Saldo de compras",
            brl(saldo_compras),
            "Disponível para comprar hoje",
            "negativo" if saldo_compras < 0 else "",
        ),
    ]
)


# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------
secao("Reserva de emergência")

col_grafico, col_texto = st.columns([2, 3])
with col_grafico:
    falta = max(0, reserva_meta - reserva_atual)
    rosca = go.Figure(
        go.Pie(
            values=[reserva_atual, falta],
            labels=["Guardado", "Falta"],
            hole=0.65,
            marker_colors=[TEAL, "#E4EAF1"],
            sort=False,
            textinfo="none",
            hovertemplate="%{label}: R$ %{value:,.2f}<extra></extra>",
        )
    )
    rosca.update_layout(
        showlegend=False,
        margin=dict(t=10, b=10, l=10, r=10),
        height=180,
        annotations=[
            dict(text=f"<b>{reserva_pct:.1f}%</b>", x=0.5, y=0.5, showarrow=False, font_size=20)
        ],
    )
    st.plotly_chart(rosca, use_container_width=True, config={"displayModeBar": False})

with col_texto:
    st.markdown(
        f'<div class="fin-linha"><span class="chave">Reserva atual</span>'
        f'<span class="val">{format_brl(reserva_atual)}</span></div>'
        f'<div class="fin-linha"><span class="chave">Meta</span>'
        f'<span class="val">{format_brl(reserva_meta)}</span></div>'
        f'<div class="fin-linha"><span class="chave">Falta</span>'
        f'<span class="val">{format_brl(max(0, reserva_meta - reserva_atual))}</span></div>',
        unsafe_allow_html=True,
    )
    if not patrimonio.informado:
        st.caption("Valor vem do 📅 Fechamento mensal. Ainda não informado.")


secao("Evolução do patrimônio")
if not serie_patrimonio:
    aviso_vazio("Ainda não há meses com dados para montar a evolução.")
else:
    rotulos = [month_short(m) for m, _ in serie_patrimonio]
    valores = [float(to_decimal(v)) for _, v in serie_patrimonio]

    # Com um ou dois meses, uma linha vira um ponto solto: barra comunica melhor.
    if len(serie_patrimonio) < 3:
        figura = go.Figure(
            go.Bar(
                x=rotulos,
                y=valores,
                marker_color=NAVY,
                hovertemplate="%{x}: R$ %{y:,.2f}<extra></extra>",
            )
        )
        # Sem isso, uma única barra ocupa a largura toda do gráfico.
        figura.update_layout(bargap=0.75)
    else:
        figura = go.Figure(
            go.Scatter(
                x=rotulos,
                y=valores,
                mode="lines+markers",
                line=dict(color=NAVY, width=3),
                marker=dict(size=8),
                hovertemplate="%{x}: R$ %{y:,.2f}<extra></extra>",
                fill="tozeroy",
                fillcolor="rgba(23,50,77,0.08)",
            )
        )
    figura.update_layout(
        height=260,
        margin=dict(t=10, b=10, l=10, r=10),
        yaxis_title="R$",
        xaxis_title=None,
        plot_bgcolor="white",
    )
    figura.update_yaxes(gridcolor="#EEF3F7")
    st.plotly_chart(figura, use_container_width=True, config={"displayModeBar": False})


secao("Investimentos")
mostrar_cartoes(
    [
        (
            "Capital destinado",
            brl(resumo_inv.capital_destinado_cents),
            "Soma do que já separei para Independência",
            "",
        ),
        (
            "Valor atual",
            brl(resumo_inv.valor_atual_cents),
            "Informado no fechamento" if resumo_inv.informado else "Ainda não informado",
            "",
        ),
        (
            "Ganho/perda estimado",
            brl(resumo_inv.resultado_cents),
            f"{resumo_inv.rentabilidade_pct:+.1f}% sobre o capital",
            "negativo" if resumo_inv.resultado_cents < 0 else "positivo",
        ),
    ],
    por_linha=3,
)
if not resumo_inv.informado:
    st.caption(
        "Sem fechamento informado, o resultado aparece zerado em vez de simular prejuízo."
    )


secao("Dividendos")
if not any(valor for _, valor, _ in serie_div):
    aviso_vazio("Nenhum dividendo informado ainda. Registre no 📅 Fechamento mensal.")
else:
    grafico_div = go.Figure()
    grafico_div.add_bar(
        x=[month_short(m) for m, _, _ in serie_div],
        y=[float(to_decimal(v)) for _, v, _ in serie_div],
        name="No mês",
        marker_color=TEAL,
        hovertemplate="%{x}: R$ %{y:,.2f}<extra></extra>",
    )
    grafico_div.add_scatter(
        x=[month_short(m) for m, _, _ in serie_div],
        y=[float(to_decimal(a)) for _, _, a in serie_div],
        name="Acumulado",
        mode="lines+markers",
        line=dict(color=NAVY, width=3),
        hovertemplate="%{x}: R$ %{y:,.2f}<extra></extra>",
    )
    grafico_div.update_layout(
        height=260,
        margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="white",
        legend=dict(orientation="h", y=1.15, x=0),
        yaxis_title="R$",
    )
    grafico_div.update_yaxes(gridcolor="#EEF3F7")
    st.plotly_chart(grafico_div, use_container_width=True, config={"displayModeBar": False})

st.caption(
    f"Dividendos em {month_label(mes)}: {brl(div_mes)} · "
    f"acumulado: {brl(div_total)}. Não entram na renda automaticamente."
)
