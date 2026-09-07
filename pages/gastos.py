"""Página de gastos: registrar compras (à vista ou parceladas) e acompanhar o mês."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import budget_service as budget
from core import repositories as repo
from core.database import session_scope
from core.models import EXPENSE_CATEGORIES, Category, PaymentMethod
from core.utils import (
    add_months,
    format_brl,
    month_label,
    month_start,
    to_cents,
    to_decimal,
)
from ui.shared import (
    brl,
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    garantir_banco,
    mostrar_cartoes,
    secao,
    seletor_mes,
    valor_colorido,
)

configurar_pagina("Gastos")
garantir_banco()

cabecalho("💳 Gastos", "Compras do mês, parcelas e compromissos futuros.")
mes = seletor_mes("gas")

with session_scope() as session:
    plano = budget.get_month_plan(session, mes)
    parcelas_mes = [
        {
            "id": gasto.id,
            "numero": parcela.number,
            "total_parcelas": gasto.installments_count,
            "data": gasto.purchase_date,
            "descricao": gasto.description,
            "categoria": gasto.category,
            "valor_parcela": parcela.amount_cents,
            "valor_total": gasto.total_cents,
            "meio": gasto.payment_method,
            "primeira": gasto.first_installment_month,
            "obs": gasto.note or "",
        }
        for parcela, gasto in repo.list_installments(session, mes)
    ]
    futuras = repo.future_installments(session, mes)

mostrar_cartoes(
    [
        ("Gasto no mês", brl(plano.gasto_cents), "Somando parcelas", ""),
        (
            "Compromissos futuros",
            brl(sum(v for _, v in futuras)),
            f"{len(futuras)} meses com parcelas",
            "",
        ),
    ]
)


# --------------------------------------------------------------------------
# Nova compra
# --------------------------------------------------------------------------
secao("Registrar gasto")

with st.form("novo_gasto", clear_on_submit=True):
    col_a, col_b = st.columns(2)
    with col_a:
        data_compra = st.date_input(
            "Data da compra",
            value=date.today() if month_start(date.today()) == mes else month_start(mes),
            format="DD/MM/YYYY",
        )
        categoria = st.selectbox(
            "Categoria", options=EXPENSE_CATEGORIES, format_func=lambda c: c.value
        )
        meio = st.selectbox(
            "Meio de pagamento", options=list(PaymentMethod), format_func=lambda m: m.value
        )
    with col_b:
        descricao = st.text_input("Descrição", placeholder="Ex.: jantar de aniversário")
        valor_total = st.number_input("Valor total (R$)", min_value=0.0, step=25.0, value=0.0)

    parcelado = st.checkbox("Parcelado?", value=False)
    col_c, col_d = st.columns(2)
    with col_c:
        num_parcelas = st.number_input(
            "Número de parcelas", min_value=1, max_value=72, value=1, step=1
        )
    with col_d:
        primeira_parcela = st.date_input(
            "Mês da primeira parcela",
            value=month_start(data_compra),
            format="DD/MM/YYYY",
            help="Por padrão, o mês da compra. Só o mês é considerado.",
        )

    observacao = st.text_input("Observação (opcional)")
    enviado = st.form_submit_button("Adicionar gasto", use_container_width=True)

if enviado:
    quantidade = int(num_parcelas) if parcelado else 1
    if not descricao.strip():
        st.error("Informe uma descrição.")
    elif valor_total <= 0:
        st.error("Informe um valor maior que zero.")
    else:
        with session_scope() as session:
            gasto = repo.create_expense(
                session,
                purchase_date=data_compra,
                description=descricao,
                category=categoria,
                total_cents=to_cents(valor_total),
                payment_method=meio,
                installments_count=quantidade,
                first_installment_month=primeira_parcela if parcelado else data_compra,
                note=observacao.strip() or None,
            )
            resumo = [(p.month, p.amount_cents) for p in gasto.installments]
        if quantidade > 1:
            detalhe = " · ".join(
                f"{month_label(m).split('/')[0][:3]}/{str(m.year)[2:]} {brl(v)}"
                for m, v in resumo[:6]
            )
            st.success(f"Gasto parcelado registrado em {quantidade}x: {detalhe}"
                       + (" …" if len(resumo) > 6 else ""))
        else:
            st.success("Gasto registrado.")
        st.rerun()


# --------------------------------------------------------------------------
# Gastos do mês
# --------------------------------------------------------------------------
secao(f"Gastos de {month_label(mes)}")

filtro = st.selectbox(
    "Filtrar por categoria",
    options=["Todas", *[c.value for c in EXPENSE_CATEGORIES]],
    key="filtro_categoria",
)
visiveis = [
    d for d in parcelas_mes if filtro == "Todas" or d["categoria"].value == filtro
]

if not visiveis:
    aviso_vazio("Nenhum gasto neste mês para o filtro escolhido.")
else:
    tabela = pd.DataFrame(
        [
            {
                "Data": d["data"].strftime("%d/%m"),
                "Descrição": d["descricao"]
                + (f" ({d['numero']}/{d['total_parcelas']})" if d["total_parcelas"] > 1 else ""),
                "Categoria": d["categoria"].value,
                "Valor": format_brl(d["valor_parcela"]),
            }
            for d in visiveis
        ]
    )
    st.dataframe(tabela, use_container_width=True, hide_index=True)
    st.markdown(
        f"**Total exibido:** {brl(sum(d['valor_parcela'] for d in visiveis))}"
    )

    # ----------------------------------------------------------------------
    # Editar / excluir
    # ----------------------------------------------------------------------
    with st.expander("Editar ou excluir um gasto"):
        unicos = {d["id"]: d for d in visiveis}
        rotulos = {
            i: f"{d['data'].strftime('%d/%m')} · {d['descricao']} · "
               f"{brl(d['valor_total'])}"
            for i, d in unicos.items()
        }
        escolhido = st.selectbox(
            "Gasto", options=list(rotulos), format_func=lambda i: rotulos[i]
        )
        atual = unicos[escolhido]

        with st.form(f"editar_gasto_{escolhido}"):
            col_e, col_f = st.columns(2)
            with col_e:
                nova_data = st.date_input(
                    "Data da compra", value=atual["data"], format="DD/MM/YYYY"
                )
                nova_categoria = st.selectbox(
                    "Categoria",
                    options=EXPENSE_CATEGORIES,
                    index=EXPENSE_CATEGORIES.index(atual["categoria"]),
                    format_func=lambda c: c.value,
                )
                novo_meio = st.selectbox(
                    "Meio de pagamento",
                    options=list(PaymentMethod),
                    index=list(PaymentMethod).index(atual["meio"]),
                    format_func=lambda m: m.value,
                )
            with col_f:
                nova_descricao = st.text_input("Descrição", value=atual["descricao"])
                novo_total = st.number_input(
                    "Valor total (R$)",
                    min_value=0.0,
                    step=25.0,
                    value=float(to_decimal(atual["valor_total"])),
                )
                novas_parcelas = st.number_input(
                    "Número de parcelas",
                    min_value=1,
                    max_value=72,
                    value=int(atual["total_parcelas"]),
                    step=1,
                )
            nova_primeira = st.date_input(
                "Mês da primeira parcela", value=atual["primeira"], format="DD/MM/YYYY"
            )
            nova_obs = st.text_input("Observação", value=atual["obs"])

            bot_a, bot_b = st.columns(2)
            salvar = bot_a.form_submit_button("Salvar", use_container_width=True)
            excluir = bot_b.form_submit_button("Excluir", use_container_width=True)

        if salvar:
            if not nova_descricao.strip():
                st.error("Informe uma descrição.")
            elif novo_total <= 0:
                st.error("Informe um valor maior que zero.")
            else:
                with session_scope() as session:
                    repo.update_expense(
                        session,
                        escolhido,
                        purchase_date=nova_data,
                        description=nova_descricao,
                        category=nova_categoria,
                        total_cents=to_cents(novo_total),
                        payment_method=novo_meio,
                        installments_count=int(novas_parcelas),
                        first_installment_month=nova_primeira,
                        note=nova_obs.strip() or None,
                    )
                st.success("Gasto atualizado e parcelas recalculadas.")
                st.rerun()

        if excluir:
            with session_scope() as session:
                repo.delete_expense(session, escolhido)
            st.success("Gasto excluído junto com todas as suas parcelas.")
            st.rerun()


# --------------------------------------------------------------------------
# Situação das categorias e parcelas futuras
# --------------------------------------------------------------------------
secao("Situação do orçamento")
for linha in plano.gastos:
    st.markdown(
        f'<div class="fin-linha"><span class="chave">{linha.categoria.value}</span>'
        f"<span class=\"val\">{format_brl(linha.gasto_cents)} de "
        f"{format_brl(linha.orcamento_cents)} · "
        f"{valor_colorido(linha.disponivel_cents)}</span></div>",
        unsafe_allow_html=True,
    )
with session_scope() as session:
    envelopes = {
        categoria: budget.saldo_envelope(session, categoria, mes)
        for categoria in (Category.COMPRAS, Category.VIAGEM)
    }
for categoria, saldo in envelopes.items():
    st.markdown(
        f'<div class="fin-linha"><span class="chave">{categoria.value} (envelope)</span>'
        f"<span class=\"val\">{valor_colorido(saldo)}</span></div>",
        unsafe_allow_html=True,
    )


secao("Parcelas futuras")
if not futuras:
    aviso_vazio("Nenhuma parcela comprometida depois deste mês.")
else:
    st.dataframe(
        pd.DataFrame(
            [
                {"Mês": month_label(m), "Valor": brl(v)}
                for m, v in futuras
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Compromissos já assumidos. A compra conta pela data da compra.")
