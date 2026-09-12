"""Página de fechamento mensal: os três valores informados uma vez por mês."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core.database import session_scope
from core.models import ClosingField
from core.utils import month_label, to_cents, to_decimal
from ui.shared import (
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    dinheiro,
    dinheiro_html,
    garantir_banco,
    mostrar_cartoes,
    percentual,
    secao,
    seletor_periodo,
)

from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()


configurar_pagina("Fechamento mensal")
garantir_banco()

cabecalho(
    "📅 Fechamento mensal",
    "Uma vez por mês: reserva, investimentos e dividendos.",
    chave="fech",
)
periodo = seletor_periodo("fech", permitir_anual=False)
mes = periodo.month

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
    rotulos = budget.rotulos_do_fechamento(session, mes)
    patrimonio = budget.get_patrimonio(session, periodo)
    resumo_inv = investimentos.get_resumo(session, periodo)
    historico = [
        {
            "mes": f.month,
            "reserva": f.reserva_cents,
            "investimentos": f.investimentos_cents,
            "dividendos": f.dividendos_cents,
        }
        for f in repo.list_closings(session)
    ]

if atual is None:
    st.warning(f"Fechamento de {month_label(mes)}: **ainda não informado**.", icon="⚠️")
else:
    mostrar_cartoes(
        [
            (rotulos[ClosingField.RESERVA], dinheiro(atual["reserva"]), "Informado por você", ""),
            (
                rotulos[ClosingField.INVESTIMENTOS],
                dinheiro(atual["investimentos"]),
                "Informado por você",
                "",
            ),
            (
                "Patrimônio acompanhado",
                dinheiro(patrimonio.total_cents),
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
            f"{rotulos[ClosingField.RESERVA]} — valor atual (R$)",
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
            f"{rotulos[ClosingField.INVESTIMENTOS]} — valor atual (R$)",
            min_value=0.0,
            step=50.0,
            value=float(to_decimal(atual["investimentos"])) if atual else 0.0,
            help="Valor atual da carteira. Só o que já foi investido de fato.",
        )
        observacao = st.text_input(
            "Observação (opcional)", value=atual["obs"] if atual else ""
        )

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

st.info(
    "Informe aqui o que você **realmente tem** hoje nessas contas. A partir "
    "desse valor, tudo que você separar depois é somado automaticamente — não "
    "precisa voltar aqui a cada separação. Quando informar de novo, o número "
    "digitado vira a nova verdade e o acúmulo recomeça, sem contar duas vezes.",
    icon="💡",
)
st.caption(
    "O valor informado também alimenta a regra da meta: quando faltar menos que o "
    "percentual para chegar ao alvo, a sobra vai para a categoria de destino."
)


# --------------------------------------------------------------------------
# Investimentos
# --------------------------------------------------------------------------
secao("Investimentos no mês")
mostrar_cartoes(
    [
        (
            "Capital destinado",
            dinheiro(resumo_inv.capital_destinado_cents),
            "Separado para investir até aqui",
            "",
        ),
        (
            "Ganho/perda estimado",
            dinheiro(resumo_inv.resultado_cents)
            if resumo_inv.informado
            else "Não informado",
            percentual(resumo_inv.rentabilidade_pct) + " sobre o capital"
            if resumo_inv.rentabilidade_valida
            else "aguardando o valor da carteira",
            ("negativo" if resumo_inv.resultado_cents < 0 else "positivo")
            if resumo_inv.informado
            else "",
        ),
    ]
)
st.caption(
    "Dividendos não entram nesta conta, para não contar o mesmo dinheiro duas vezes."
)


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
                    rotulos[ClosingField.RESERVA]: dinheiro_html(h["reserva"]),
                    rotulos[ClosingField.INVESTIMENTOS]: dinheiro_html(h["investimentos"]),
                    "Dividendos": dinheiro_html(h["dividendos"]),
                }
                for h in reversed(historico)
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Cada mês guarda o que foi informado nele — nada é sobrescrito depois.")
