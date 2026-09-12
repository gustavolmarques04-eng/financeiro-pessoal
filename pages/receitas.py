"""Página de receitas: registrar, editar, excluir e listar entradas do mês."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import budget_service as budget
from core import repositories as repo
from core.database import session_scope
from core.models import IncomeType
from core.utils import month_label, month_start, to_cents, to_decimal
from ui.shared import (
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    dinheiro,
    dinheiro_html,
    garantir_banco,
    mostrar_cartoes,
    secao,
    seletor_periodo,
)

from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()


configurar_pagina("Receitas")
garantir_banco()

cabecalho("💰 Receitas", "Tudo que entrou no mês selecionado.", chave="rec")
periodo = seletor_periodo("rec", permitir_anual=False)
mes = periodo.month

with session_scope() as session:
    plano = budget.get_month_plan(session, mes)
    dados = [
        {
            "id": r.id,
            "data": r.date,
            "descricao": r.description,
            "tipo": r.type,
            "valor_cents": r.amount_cents,
            "orcamento": r.counts_in_budget,
            "obs": r.note or "",
        }
        for r in repo.list_incomes(session, periodo)
    ]

mostrar_cartoes(
    [
        ("Recebido no mês", dinheiro(plano.recebido_cents), "Exclui saldos iniciais", ""),
        (
            "Base de distribuição",
            dinheiro(plano.base_cents),
            "O que é rateado pelos percentuais",
            "",
        ),
    ]
)


# --------------------------------------------------------------------------
# Nova receita
# --------------------------------------------------------------------------
secao("Adicionar receita")

with st.form("nova_receita", clear_on_submit=True):
    coluna_a, coluna_b = st.columns(2)
    with coluna_a:
        data = st.date_input(
            "Data",
            value=date.today() if month_start(date.today()) == mes else mes,
            format="DD/MM/YYYY",
        )
        tipo = st.selectbox(
            "Tipo", options=IncomeType.selecionaveis(), format_func=lambda t: t.value
        )
    with coluna_b:
        descricao = st.text_input("Descrição", placeholder="Ex.: salário de setembro")
        valor = st.number_input("Valor (R$)", min_value=0.0, step=50.0, value=0.0)

    entra_no_orcamento = st.checkbox(
        "Entra na base de distribuição",
        value=True,
        help="Desmarque quando o dinheiro não deve ser rateado pelos percentuais.",
    )
    observacao = st.text_input("Observação (opcional)", placeholder="")

    enviado = st.form_submit_button("Adicionar receita", use_container_width=True)

if enviado:
    if not descricao.strip():
        st.error("Informe uma descrição.")
    elif valor <= 0:
        st.error("Informe um valor maior que zero.")
    else:
        with session_scope() as session:
            repo.create_income(
                session,
                on=data,
                description=descricao,
                type_=tipo,
                amount_cents=to_cents(valor),
                counts_in_budget=entra_no_orcamento,
                note=observacao.strip() or None,
            )
        st.success(
            f"Receita adicionada. O plano de {month_label(month_start(data))} foi "
            "recalculado e as separações afetadas voltaram a ficar pendentes."
        )
        st.rerun()


# --------------------------------------------------------------------------
# Lista do mês
# --------------------------------------------------------------------------
secao(f"Receitas de {month_label(mes)}")

if not dados:
    aviso_vazio("Nenhuma receita registrada neste mês.")
else:
    tabela = pd.DataFrame(
        [
            {
                "Data": d["data"].strftime("%d/%m"),
                "Descrição": d["descricao"],
                "Tipo": d["tipo"].value,
                "Valor": dinheiro_html(d["valor_cents"]),
                "No rateio": "Sim" if d["orcamento"] else "Não",
            }
            for d in dados
        ]
    )
    st.dataframe(tabela, use_container_width=True, hide_index=True)
    st.markdown(f"**Total do mês:** {dinheiro(sum(d['valor_cents'] for d in dados))}")

    # ----------------------------------------------------------------------
    # Editar / excluir
    # ----------------------------------------------------------------------
    with st.expander("Editar ou excluir uma receita"):
        rotulos = {
            d["id"]: f"{d['data'].strftime('%d/%m')} · {d['descricao']} · "
                     f"{dinheiro_html(d['valor_cents'])}"
            for d in dados
        }
        escolhido = st.selectbox(
            "Receita", options=list(rotulos), format_func=lambda i: rotulos[i]
        )
        atual = next(d for d in dados if d["id"] == escolhido)

        with st.form(f"editar_receita_{escolhido}"):
            col_a, col_b = st.columns(2)
            with col_a:
                nova_data = st.date_input("Data", value=atual["data"], format="DD/MM/YYYY")
                novo_tipo = st.selectbox(
                    "Tipo",
                    options=IncomeType.selecionaveis(),
                    index=IncomeType.selecionaveis().index(atual["tipo"]),
                    format_func=lambda t: t.value,
                )
            with col_b:
                nova_descricao = st.text_input("Descrição", value=atual["descricao"])
                novo_valor = st.number_input(
                    "Valor (R$)",
                    min_value=0.0,
                    step=50.0,
                    value=float(to_decimal(atual["valor_cents"])),
                )
            novo_orcamento = st.checkbox(
                "Entra na base de distribuição", value=atual["orcamento"]
            )
            nova_obs = st.text_input("Observação", value=atual["obs"])

            botao_a, botao_b = st.columns(2)
            salvar = botao_a.form_submit_button("Salvar", use_container_width=True)
            excluir = botao_b.form_submit_button("Excluir", use_container_width=True)

        if salvar:
            if not nova_descricao.strip():
                st.error("Informe uma descrição.")
            elif novo_valor <= 0:
                st.error("Informe um valor maior que zero.")
            else:
                with session_scope() as session:
                    repo.update_income(
                        session,
                        escolhido,
                        on=nova_data,
                        description=nova_descricao,
                        type_=novo_tipo,
                        amount_cents=to_cents(novo_valor),
                        counts_in_budget=novo_orcamento,
                        note=nova_obs.strip() or None,
                    )
                st.success("Receita atualizada e plano recalculado.")
                st.rerun()

        if excluir:
            with session_scope() as session:
                repo.delete_income(session, escolhido)
            st.success("Receita excluída e plano recalculado.")
            st.rerun()

st.caption(
    "Ao adicionar, alterar ou excluir uma receita, o plano do mês ganha uma nova "
    "revisão. As separações já feitas continuam registradas, mas voltam a ficar "
    "pendentes se passarem a cobrir menos que o novo planejado."
)
