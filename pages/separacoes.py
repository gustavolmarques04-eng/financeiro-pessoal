"""Página de separações: confirmar quanto já foi reservado em cada categoria.

No modo mensal há checkbox por categoria. No modo anual a tela é apenas
analítica — um resumo mês a mês, sem checkbox: confirmação é sempre de um
mês específico.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import budget_service as budget
from core.budget_service import SeparacaoLinha
from core.database import session_scope
from core.utils import month_label, to_cents, to_decimal
from ui.shared import (
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    dinheiro,
    dinheiro_html,
    garantir_banco,
    linha,
    mostrar_cartoes,
    secao,
    selo,
    seletor_periodo,
)

configurar_pagina("Separações")
garantir_banco()

cabecalho("🎯 Separações", "Quanto já foi realmente reservado.", chave="sep")
periodo = seletor_periodo("sep")


# --------------------------------------------------------------------------
# Modo anual: só análise
# --------------------------------------------------------------------------
if periodo.is_year:
    with session_scope() as session:
        resumos = [budget.resumo_separacoes(session, mes) for mes in periodo.months]
        planos = {mes: budget.get_month_plan(session, mes) for mes in periodo.months}

    com_dados = [r for r in resumos if r.total > 0]
    secao(f"Separações de {periodo.label}")

    if not com_dados:
        aviso_vazio("Nenhum mês deste ano tem plano de separação ainda.")
    else:
        mostrar_cartoes(
            [
                (
                    "Meses com plano",
                    str(len(com_dados)),
                    f"{sum(1 for r in com_dados if r.tudo_feito)} concluídos",
                    "",
                ),
                (
                    "Falta separar no ano",
                    dinheiro(sum(r.falta_cents for r in com_dados)),
                    "somando os meses pendentes",
                    "",
                ),
            ]
        )
        st.caption(
            "Visão apenas analítica: a confirmação continua sendo feita mês a mês."
        )

        for resumo in com_dados:
            plano = planos[resumo.month]
            estado = "✅" if resumo.tudo_feito else "⚠️"
            titulo = (
                f"{estado}  {month_label(resumo.month).capitalize()}  ·  "
                f"{resumo.concluidas}/{resumo.total} feitas"
            )
            if not resumo.tudo_feito:
                titulo += f"  ·  falta {dinheiro(resumo.falta_cents)}"

            with st.expander(titulo):
                st.markdown(
                    "".join(
                        linha(
                            f"{item.categoria.label} — {item.status}",
                            f"{dinheiro_html(item.separado_cents)} de "
                            f"{dinheiro_html(item.planejado_cents)}",
                        )
                        for item in plano.separacoes
                        if item.planejado_cents > 0 or item.separado_cents > 0
                    ),
                    unsafe_allow_html=True,
                )
                if st.button(
                    f"Abrir {month_label(resumo.month)}",
                    key=f"abrir_{resumo.month}",
                    use_container_width=True,
                ):
                    from core.period import Period
                    from ui.shared import definir_periodo

                    definir_periodo(Period.of_month(resumo.month))
                    st.rerun()
    st.stop()


# --------------------------------------------------------------------------
# Modo mensal: confirmação
# --------------------------------------------------------------------------
with session_scope() as session:
    plano = budget.get_month_plan(session, periodo.month)
    resumo = budget.resumo_separacoes(session, periodo.month)

mostrar_cartoes(
    [
        (
            "Base do rateio",
            dinheiro(plano.base_cents),
            f"recebido: {dinheiro(plano.recebido_cents)}",
            "",
        ),
        (
            "Falta separar",
            dinheiro(resumo.falta_cents),
            f"{resumo.concluidas} de {resumo.total} concluídas",
            "positivo" if resumo.falta_cents == 0 else "negativo",
        ),
    ]
)

secao(f"Separações de {month_label(periodo.month)}")

if plano.base_cents == 0:
    aviso_vazio("Nenhuma receita neste mês ainda. Registre em 💰 Receitas para ver o plano.")
    st.stop()

if plano.sobra_meta_cents:
    st.caption(
        f"↪️ {dinheiro(plano.sobra_meta_cents)} não são mais necessários na meta e "
        "foram redirecionados conforme a configuração da categoria."
    )


def _bloco(item: SeparacaoLinha) -> None:
    """Cartão de uma categoria com números e checkbox de confirmação."""
    with st.container(border=True):
        st.markdown(
            f"**{item.categoria.label}** &nbsp; {selo(item.status)}",
            unsafe_allow_html=True,
        )
        st.markdown(
            linha("Planejado", dinheiro_html(item.planejado_cents))
            + linha("Já separado", dinheiro_html(item.separado_cents))
            + linha("Falta separar", dinheiro_html(item.falta_cents)),
            unsafe_allow_html=True,
        )

        # A chave carrega o plano vigente: se o plano mudar, o widget é
        # recriado e volta a refletir o estado real (desmarcado).
        chave = (
            f"sep_{item.categoria.id}_{periodo.month}_{plano.revision}"
            f"_{item.planejado_cents}_{item.separado_cents}"
        )
        marcado = st.checkbox(
            "Já separei este valor",
            value=item.feito,
            key=chave,
            disabled=item.planejado_cents == 0 and item.separado_cents == 0,
        )

        if marcado and not item.feito:
            with session_scope() as s:
                budget.confirmar_separacao(s, periodo.month, item.categoria.id)
            st.rerun()
        elif not marcado and item.feito:
            with session_scope() as s:
                budget.desfazer_separacao(s, periodo.month, item.categoria.id)
            st.rerun()

        with st.expander("Ajustar valor separado"):
            novo = st.number_input(
                "Valor efetivamente separado (R$)",
                min_value=0.0,
                step=10.0,
                value=float(to_decimal(item.separado_cents)),
                key=f"aj_{item.categoria.id}_{periodo.month}",
            )
            if st.button("Salvar ajuste", key=f"btaj_{item.categoria.id}_{periodo.month}"):
                with session_scope() as s:
                    budget.ajustar_separacao(
                        s, periodo.month, item.categoria.id, to_cents(novo)
                    )
                st.rerun()


linhas = plano.separacoes
for inicio in range(0, len(linhas), 2):
    colunas = st.columns(2)
    for coluna, item in zip(colunas, linhas[inicio : inicio + 2]):
        with coluna:
            _bloco(item)

st.caption(
    f"Total planejado {dinheiro(plano.total_planejado_cents)} · "
    f"falta separar {dinheiro(plano.total_falta_separar_cents)}. "
    "Se entrar renda nova, o planejado sobe e a categoria volta a ficar pendente — "
    "o que você já separou continua registrado."
)
