"""Página de separações: confirmar quanto já foi reservado em cada categoria.

No modo mensal há checkbox por categoria. No modo anual a tela é apenas
analítica — um resumo mês a mês, sem checkbox: confirmação é sempre de um
mês específico.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import budget_service as budget
from core import categories as cat
from core import repositories as repo
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
    subtitulo,
    valor_colorido,
)

from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()


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
        corpo = (
            linha("Planejado este mês", dinheiro_html(item.planejado_cents))
            + linha("Já separado", dinheiro_html(item.separado_cents))
        )
        if item.excedente_cents:
            # Acontece quando a renda é reduzida depois da separação: o
            # dinheiro já saiu, e apagá-lo seria inventar um movimento.
            corpo += linha("Separado a mais", dinheiro_html(item.excedente_cents))
        else:
            corpo += linha("Falta separar", dinheiro_html(item.falta_cents))
        corpo += linha("Saldo acumulado", valor_colorido(item.saldo_cents))
        if item.categoria.target_amount_cents:
            corpo += linha(
                "Meta",
                f"{dinheiro_html(item.saldo_cents)} de "
                f"{dinheiro_html(item.categoria.target_amount_cents)}",
            )
        st.markdown(corpo, unsafe_allow_html=True)

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


# --------------------------------------------------------------------------
# Transferir dinheiro entre categorias
# --------------------------------------------------------------------------
secao("↔ Transferir dinheiro")
st.caption(
    "Move saldo de uma categoria para outra. O total do seu dinheiro não muda "
    "— só o lugar onde ele está."
)

ativas = [item.categoria for item in plano.separacoes if item.categoria.active]
if len(ativas) < 2:
    aviso_vazio("São necessárias ao menos duas categorias para transferir.")
else:
    with st.form("transferir"):
        quando = st.date_input("Data", value=periodo.month, key="tr_data")
        coluna_de, coluna_para = st.columns(2)
        de = coluna_de.selectbox(
            "De", options=ativas, format_func=lambda v: v.label, key="tr_de"
        )
        para = coluna_para.selectbox(
            "Para",
            options=[v for v in ativas if v.id != de.id],
            format_func=lambda v: v.label,
            key="tr_para",
        )
        valor = st.number_input("Valor (R$)", min_value=0.01, step=10.0, key="tr_valor")
        descricao = st.text_input("Descrição (opcional)", key="tr_desc")
        enviou = st.form_submit_button("Transferir", use_container_width=True)

    if enviou:
        try:
            with session_scope() as s:
                budget.transferir(
                    s,
                    month=quando,
                    origem_id=de.id,
                    destino_id=para.id,
                    valor_cents=to_cents(valor),
                    note=descricao or None,
                    on=quando,
                )
        except budget.TransferenciaInvalida as erro:
            st.error(str(erro))
        else:
            st.toast(f"{dinheiro(to_cents(valor))} de {de.name} para {para.name}.")
            st.rerun()


# --------------------------------------------------------------------------
# Histórico
# --------------------------------------------------------------------------
subtitulo("Transferências do período")
with session_scope() as s:
    historico = repo.list_transferencias(s, periodo)
    nomes = {v.id: v.label for v in cat.resolve_all(s, periodo.month)}
    estornadas = {t.reversal_of_id for t in historico if t.reversal_of_id}

if not historico:
    aviso_vazio("Nenhuma transferência neste período.")
else:
    filtro = st.selectbox(
        "Filtrar por categoria",
        options=["Todas", *sorted(nomes.values())],
        key="tr_filtro",
    )
    for movimento in historico:
        origem = nomes.get(movimento.from_category_id, "—")
        destino = nomes.get(movimento.to_category_id, "—")
        if filtro != "Todas" and filtro not in (origem, destino):
            continue

        with st.container(border=True):
            coluna_texto, coluna_botao = st.columns([4, 1])
            rotulo = "↩ estorno · " if movimento.reversal_of_id else ""
            coluna_texto.markdown(
                f"{rotulo}**{origem} → {destino}** · "
                f"{dinheiro(movimento.amount_cents)}  \n"
                f"<small>{movimento.transfer_date:%d/%m/%Y}"
                + (f" · {movimento.description}" if movimento.description else "")
                + "</small>",
                unsafe_allow_html=True,
            )
            ja_estornada = movimento.id in estornadas
            if movimento.reversal_of_id is None:
                if coluna_botao.button(
                    "Estornar",
                    key=f"est_{movimento.id}",
                    disabled=ja_estornada,
                    help=(
                        "Já estornada"
                        if ja_estornada
                        else "Cria o movimento inverso, sem apagar o original."
                    ),
                ):
                    with session_scope() as s:
                        budget.estornar_transferencia(s, movimento.id)
                    st.toast("Estornada.", icon="↩")
                    st.rerun()
    st.caption(
        "Transferências não são apagadas nem editadas: corrigir é estornar, "
        "para o histórico continuar explicando cada centavo."
    )
