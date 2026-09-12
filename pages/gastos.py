"""Página de gastos: registrar compras (à vista ou parceladas) e acompanhar o mês.

O formulário vem de :mod:`ui.forms` — o mesmo usado pelo atalho da Home.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import budget_service as budget
from core import categories as cat
from core import repositories as repo
from core.database import session_scope
from core.utils import month_label
from ui.forms import DadosGasto, categorias_para, formulario_gasto
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
    seletor_periodo,
    valor_colorido,
)

from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()


configurar_pagina("Gastos")
garantir_banco()

cabecalho("💳 Gastos", "Compras do mês, parcelas e compromissos futuros.", chave="gas")
periodo = seletor_periodo("gas", permitir_anual=False)
mes = periodo.month

with session_scope() as session:
    plano = budget.get_month_plan(session, mes)
    parcelas_mes = [
        {
            "id": gasto.id,
            "numero": parcela.number,
            "total_parcelas": gasto.installments_count,
            "data": gasto.purchase_date,
            "descricao": gasto.description,
            "categoria_id": gasto.category_id,
            "valor_parcela": parcela.amount_cents,
            "valor_total": gasto.total_cents,
            "meio": gasto.payment_method,
            "primeira": gasto.first_installment_month,
            "obs": gasto.note or "",
        }
        for parcela, gasto in repo.list_installments(session, periodo)
    ]
    nomes = {v.id: v.label for v in cat.resolve_all(session, mes)}
    ativas = cat.resolve_active(session, mes)
    futuras = repo.future_installments(session, mes)

for item in parcelas_mes:
    item["categoria"] = nomes.get(item["categoria_id"], "—")

mostrar_cartoes(
    [
        ("Gasto no mês", dinheiro(plano.gasto_cents), "Somando parcelas", ""),
        (
            "Compromissos futuros",
            dinheiro(sum(v for _, v in futuras)),
            f"{len(futuras)} meses com parcelas",
            "",
        ),
    ]
)


# --------------------------------------------------------------------------
# Nova compra
# --------------------------------------------------------------------------
secao("Registrar gasto")
if formulario_gasto("gastos", mes_referencia=mes):
    st.rerun()


# --------------------------------------------------------------------------
# Gastos do mês
# --------------------------------------------------------------------------
secao(f"Gastos de {month_label(mes)}")

filtro = st.selectbox(
    "Filtrar por categoria",
    options=["Todas", *[v.label for v in ativas]],
    key="filtro_categoria",
)
visiveis = [d for d in parcelas_mes if filtro == "Todas" or d["categoria"] == filtro]

if not visiveis:
    aviso_vazio("Nenhum gasto neste mês para o filtro escolhido.")
else:
    tabela = pd.DataFrame(
        [
            {
                "Data": d["data"].strftime("%d/%m"),
                "Descrição": d["descricao"]
                + (
                    f" ({d['numero']}/{d['total_parcelas']})"
                    if d["total_parcelas"] > 1
                    else ""
                ),
                "Categoria": d["categoria"],
                "Valor": dinheiro_html(d["valor_parcela"]),
            }
            for d in visiveis
        ]
    )
    st.dataframe(tabela, use_container_width=True, hide_index=True)
    st.markdown(
        f"**Total exibido:** {dinheiro(sum(d['valor_parcela'] for d in visiveis))}"
    )

    # ----------------------------------------------------------------------
    # Editar / excluir
    # ----------------------------------------------------------------------
    with st.expander("Editar ou excluir um gasto"):
        unicos = {d["id"]: d for d in visiveis}
        rotulos = {
            i: f"{d['data'].strftime('%d/%m')} · {d['descricao']} · "
               f"{dinheiro_html(d['valor_total'])}"
            for i, d in unicos.items()
        }
        escolhido = st.selectbox(
            "Gasto", options=list(rotulos), format_func=lambda i: rotulos[i]
        )
        atual = unicos[escolhido]

        inicial = DadosGasto(
            purchase_date=atual["data"],
            description=atual["descricao"],
            category_id=atual["categoria_id"],
            total_cents=atual["valor_total"],
            payment_method=atual["meio"],
            installments_count=atual["total_parcelas"],
            first_installment_month=atual["primeira"],
            note=atual["obs"] or None,
        )
        if formulario_gasto(
            f"edit_{escolhido}",
            mes_referencia=mes,
            inicial=inicial,
            expense_id=escolhido,
            rotulo_botao="Salvar alterações",
        ):
            st.rerun()

        if st.button("Excluir este gasto", key=f"del_{escolhido}", use_container_width=True):
            with session_scope() as session:
                repo.delete_expense(session, escolhido)
            st.success("Gasto excluído junto com todas as suas parcelas.")
            st.rerun()


# --------------------------------------------------------------------------
# Situação das categorias e parcelas futuras
# --------------------------------------------------------------------------
secao("Saldo de cada categoria")
for item in plano.separacoes:
    if not item.categoria.active and not item.saldo_cents:
        continue
    st.markdown(
        linha(item.categoria.label, valor_colorido(item.saldo_cents)),
        unsafe_allow_html=True,
    )


secao("Parcelas futuras")
if not futuras:
    aviso_vazio("Nenhuma parcela comprometida depois deste mês.")
else:
    st.dataframe(
        pd.DataFrame(
            [{"Mês": month_label(m), "Valor": dinheiro_html(v)} for m, v in futuras]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Compromissos já assumidos. A compra conta pela data da compra.")
