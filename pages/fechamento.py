"""Página de fechamento mensal: os três valores informados uma vez por mês."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core.database import session_scope
from core.utils import format_brl, format_brl, month_label, to_cents, to_decimal
from ui.shared import (
    brl,
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    garantir_banco,
    mostrar_cartoes,
    secao,
    seletor_mes,
)

configurar_pagina("Fechamento mensal")
garantir_banco()

cabecalho("📅 Fechamento mensal", "Uma vez por mês: reserva, investimentos e dividendos.")
mes = seletor_mes("fech")

with session_scope() as session:
    fechamento = repo.get_closing(session, mes)
    atual = (
        {
            "reserva": fechamento.reserva_cents,
            "investimentos": fechamento.investimentos_cents,
            "dividendos": fechamento.dividendos_cents,
            "obs": fechamento.note or "",
        }
        if fechamento
        else None
    )
    patrimonio = budget.get_patrimonio(session, mes)
    resumo_inv = investimentos.get_resumo(session, mes)
    historico = [
        {
            "mes": f.month,
            "reserva": f.reserva_cents,
            "investimentos": f.investimentos_cents,
            "dividendos": f.dividendos_cents,
            "obs": f.note or "",
        }
        for f in repo.list_closings(session)
    ]

if atual is None:
    st.warning(f"Fechamento de {month_label(mes)}: **ainda não informado**.", icon="⚠️")
else:
    mostrar_cartoes(
        [
            ("Reserva", brl(atual["reserva"]), "Informado por você", ""),
            ("Investimentos", brl(atual["investimentos"]), "Informado por você", ""),
            (
                "Patrimônio acompanhado",
                brl(patrimonio.total_cents),
                "Reserva + investimentos + envelopes",
                "",
            ),
        ],
        por_linha=3,
    )


# --------------------------------------------------------------------------
# Formulário
# --------------------------------------------------------------------------
secao(f"Informar fechamento de {month_label(mes)}")

with st.form("fechamento"):
    col_a, col_b = st.columns(2)
    with col_a:
        reserva = st.number_input(
            "Reserva de emergência atual (R$)",
            min_value=0.0,
            step=50.0,
            value=float(to_decimal(atual["reserva"])) if atual else 0.0,
        )
        dividendos = st.number_input(
            "Dividendos recebidos no mês (R$)",
            min_value=0.0,
            step=10.0,
            value=float(to_decimal(atual["dividendos"])) if atual else 0.0,
        )
    with col_b:
        invest = st.number_input(
            "Investimentos totais (R$)",
            min_value=0.0,
            step=50.0,
            value=float(to_decimal(atual["investimentos"])) if atual else 0.0,
            help="Valor atual da carteira. Só o que já foi investido de fato.",
        )
        observacao = st.text_input("Observação (opcional)", value=atual["obs"] if atual else "")

    col_c, col_d = st.columns(2)
    salvar = col_c.form_submit_button("Salvar fechamento", use_container_width=True)
    apagar = col_d.form_submit_button("Apagar fechamento", use_container_width=True)

if salvar:
    with session_scope() as session:
        repo.upsert_closing(
            session,
            mes,
            reserva_cents=to_cents(reserva),
            investimentos_cents=to_cents(invest),
            dividendos_cents=to_cents(dividendos),
            note=observacao.strip() or None,
        )
    st.success("Fechamento salvo. Patrimônio e gráficos já refletem o novo valor.")
    st.rerun()

if apagar:
    with session_scope() as session:
        repo.delete_closing(session, mes)
    st.success("Fechamento removido.")
    st.rerun()

st.caption(
    "A reserva informada aqui também alimenta a regra da meta: quando faltar menos "
    "que o percentual para chegar à meta, a sobra vai para Independência financeira."
)


# --------------------------------------------------------------------------
# Investimentos
# --------------------------------------------------------------------------
secao("Investimentos no mês")
mostrar_cartoes(
    [
        (
            "Capital destinado",
            brl(resumo_inv.capital_destinado_cents),
            "Separado para Independência até aqui",
            "",
        ),
        (
            "Ganho/perda estimado",
            brl(resumo_inv.resultado_cents),
            f"{resumo_inv.rentabilidade_pct:+.1f}% sobre o capital",
            "negativo" if resumo_inv.resultado_cents < 0 else "positivo",
        ),
    ]
)
st.caption("Dividendos não entram nesta conta, para não contar o mesmo dinheiro duas vezes.")


# --------------------------------------------------------------------------
# Histórico
# --------------------------------------------------------------------------
secao("Histórico de fechamentos")
if not historico:
    aviso_vazio("Nenhum fechamento informado ainda.")
else:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Mês": month_label(h["mes"]),
                    "Reserva": format_brl(h["reserva"]),
                    "Investimentos": format_brl(h["investimentos"]),
                    "Dividendos": format_brl(h["dividendos"]),
                }
                for h in reversed(historico)
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Cada mês guarda o que foi informado naquele mês — nada é sobrescrito depois.")
