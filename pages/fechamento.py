"""Página de fechamento mensal: os três valores informados uma vez por mês."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core import categories as cat
from core.database import session_scope
from core.models import AdjustmentKind, ClosingField
from core.utils import month_label, to_cents, to_decimal
from ui.shared import (
    aviso_vazio,
    cabecalho,
    configurar_pagina,
    dinheiro,
    dinheiro_html,
    garantir_banco,
    mostrar_cartoes,
    linha,
    percentual,
    secao,
    seletor_periodo,
    subtitulo,
    valor_colorido,
)

from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()


configurar_pagina("Fechamento mensal")
garantir_banco()

cabecalho(
    "📅 Fechamento mensal",
    "Uma vez por mês: confira os saldos reais e informe o que rendeu.",
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
                }
                for h in reversed(historico)
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Cada mês guarda o que foi informado nele — nada é sobrescrito depois.")


# --------------------------------------------------------------------------
# Conferir meus saldos
# --------------------------------------------------------------------------
secao("🔍 Conferir meus saldos")
st.caption(
    "Compare o que o app calculou com o que está de verdade na sua conta. "
    "A diferença vira um lançamento explicado — nada é sobrescrito."
)

with session_scope() as s:
    conferiveis = [
        item
        for item in budget.get_month_plan(s, periodo.month).separacoes
        if item.categoria.active and item.categoria.include_in_net_worth
    ]
    ajustes_do_mes = repo.list_ajustes(s, periodo)
    nomes_cat = {v.id: v.label for v in cat.resolve_all(s, periodo.month)}

if not conferiveis:
    aviso_vazio("Nenhuma categoria marcada como parte do patrimônio.")
else:
    MOTIVOS = {
        "Rendimento": AdjustmentKind.RENDIMENTO,
        "Correção": AdjustmentKind.CORRECAO,
        "Ajuste manual": AdjustmentKind.MANUAL,
        "Outro": AdjustmentKind.OUTRO,
    }
    for item in conferiveis:
        with st.container(border=True):
            st.markdown(f"**{item.categoria.label}**")
            st.markdown(
                linha("O app calculou", dinheiro_html(item.saldo_cents)),
                unsafe_allow_html=True,
            )
            if item.categoria.receives_dividends:
                # Dividendo pertence à categoria que o gerou: entra nela e
                # aumenta o saldo, porque reinvestir é o padrão.
                dividendo = st.number_input(
                    "Dividendos recebidos neste mês (R$)",
                    min_value=0.0,
                    step=10.0,
                    value=0.0,
                    key=f"div_{item.categoria.id}_{periodo.month}",
                    help="Entra nesta categoria e soma ao saldo dela.",
                )
                if st.button(
                    "Registrar dividendo",
                    key=f"div_bt_{item.categoria.id}_{periodo.month}",
                    disabled=dividendo <= 0,
                ):
                    with session_scope() as s:
                        investimentos.registrar_dividendo(
                            s,
                            month=periodo.month,
                            category_id=item.categoria.id,
                            amount_cents=to_cents(dividendo),
                        )
                    st.toast(
                        f"{dinheiro(to_cents(dividendo))} lançados em "
                        f"{item.categoria.name}.",
                        icon="💰",
                    )
                    st.rerun()

            coluna_valor, coluna_motivo = st.columns([3, 2])
            real = coluna_valor.number_input(
                "Saldo real (deixe igual se estiver certo)",
                min_value=0.0,
                step=10.0,
                value=float(to_decimal(max(0, item.saldo_cents))),
                key=f"conf_{item.categoria.id}_{periodo.month}",
            )
            motivo = coluna_motivo.selectbox(
                "Motivo da diferença",
                options=list(MOTIVOS),
                key=f"conf_mot_{item.categoria.id}_{periodo.month}",
            )
            if st.button(
                "Registrar diferença",
                key=f"conf_bt_{item.categoria.id}_{periodo.month}",
            ):
                with session_scope() as s:
                    diferenca = budget.conferir_saldo(
                        s,
                        month=periodo.month,
                        category_id=item.categoria.id,
                        saldo_real_cents=to_cents(real),
                        motivo=MOTIVOS[motivo],
                    )
                if diferenca:
                    st.toast(
                        f"Registrado {dinheiro(diferenca)} como {motivo.lower()}.",
                        icon="✅",
                    )
                else:
                    st.toast("Já estava batendo — nada a registrar.")
                st.rerun()

if ajustes_do_mes:
    subtitulo("Ajustes deste período")
    for ajuste in ajustes_do_mes:
        st.markdown(
            linha(
                f"{nomes_cat.get(ajuste.category_id, '—')} · {ajuste.kind.label}",
                valor_colorido(ajuste.amount_cents),
            ),
            unsafe_allow_html=True,
        )
