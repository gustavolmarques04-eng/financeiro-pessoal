"""Página de configurações: categorias versionadas, envelopes e backup.

Toda edição pergunta **a partir de qual mês** vale e cria uma nova versão.
Meses anteriores nunca são reescritos.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from core import backup_service as backup
from core import categories as cat
from core import repositories as repo
from core.database import session_scope
from core.models import CategoryBehavior, ClosingField
from core.utils import bp_from_pct, month_label, month_start, pct_from_bp, to_cents, to_decimal
from ui.shared import (
    cabecalho,
    configurar_pagina,
    dinheiro,
    dinheiro_html,
    garantir_banco,
    percentual,
    secao,
    seletor_periodo,
    subtitulo,
)

from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()


configurar_pagina("Configurações")
garantir_banco()

cabecalho("⚙️ Configurações", "Categorias, percentuais e backup.", chave="cfg")
periodo = seletor_periodo("cfg", permitir_anual=False)
mes = periodo.month

MES_PADRAO = month_start(date.today())

with session_scope() as session:
    vistas = cat.resolve_all(session, mes)
    ativas = [v for v in vistas if v.active]
    saldos_iniciais = {v.id: repo.get_opening_balance(session, v.id) for v in vistas}
    historico_versoes = repo.list_plan_effective_months(session)
    com_historico = {v.id: cat.tem_historico(session, v.id) for v in vistas}

st.caption(f"Mostrando a configuração vigente em **{month_label(mes)}**.")


# --------------------------------------------------------------------------
# Categorias e percentuais
# --------------------------------------------------------------------------
secao("Categorias e percentuais")

aplicar_de = st.date_input(
    "Aplicar alterações a partir de qual mês?",
    value=MES_PADRAO,
    format="DD/MM/YYYY",
    key="cfg_effective",
    help="Meses anteriores continuam com a configuração antiga.",
)
mes_efetivo = month_start(aplicar_de)

no_plano = [v for v in ativas if v.receives_percent]
novos_bp: dict[int, int] = {}

for vista in no_plano:
    col_nome, col_pct = st.columns([3, 2])
    with col_nome:
        st.markdown(f"**{vista.label}**")
        st.caption(vista.behavior.label)
    with col_pct:
        valor = st.number_input(
            f"% {vista.name}",
            min_value=0.0,
            max_value=100.0,
            step=0.5,
            value=float(pct_from_bp(vista.percent_bp)),
            key=f"pct_{vista.id}",
            label_visibility="collapsed",
        )
    novos_bp[vista.id] = bp_from_pct(valor)

total_bp = sum(novos_bp.values())
diferenca = abs(total_bp - cat.TOTAL_BP) / 100

if total_bp == cat.TOTAL_BP:
    st.success(f"Total dos percentuais: {percentual(total_bp / 100, 2)} ✔")
elif total_bp < cat.TOTAL_BP:
    st.error(
        f"Total atual: {percentual(total_bp / 100, 2)} — "
        f"faltam {percentual(diferenca, 2)} para fechar 100%."
    )
else:
    st.error(
        f"Total atual: {percentual(total_bp / 100, 2)} — "
        f"excesso de {percentual(diferenca, 2)} sobre 100%."
    )

if st.button(
    "Salvar percentuais",
    use_container_width=True,
    disabled=total_bp != cat.TOTAL_BP,
    key="salvar_pct",
):
    with session_scope() as session:
        for category_id, bp in novos_bp.items():
            cat.upsert_version(session, category_id, mes_efetivo, percent_bp=bp)
        cat.validar_total(cat.resolve_all(session, mes_efetivo))
        afetados = repo.bump_revisions_from(session, mes_efetivo)
    st.success(
        f"Percentuais válidos a partir de {month_label(mes_efetivo)}. "
        f"{len(afetados)} mês(es) recalculado(s); meses anteriores não mudaram."
    )
    st.rerun()


# --------------------------------------------------------------------------
# Editar uma categoria
# --------------------------------------------------------------------------
with st.expander("✏️ Editar uma categoria"):
    escolhida = st.selectbox(
        "Categoria",
        options=vistas,
        format_func=lambda v: v.label + ("" if v.active else "  (desativada)"),
        key="edit_cat",
    )
    tem_hist = com_historico[escolhida.id]

    col_a, col_b = st.columns(2)
    with col_a:
        novo_nome = st.text_input("Nome", value=escolhida.name, key="edit_nome")
        novo_emoji = st.text_input(
            "Emoji (opcional)", value=escolhida.emoji or "", key="edit_emoji", max_chars=4
        )
    with col_b:
        nova_ordem = st.number_input(
            "Ordem de exibição",
            min_value=0,
            max_value=999,
            value=int(escolhida.display_order),
            step=1,
            key="edit_ordem",
        )
        comportamentos = list(CategoryBehavior)
        novo_comportamento = st.selectbox(
            "Comportamento",
            options=comportamentos,
            index=comportamentos.index(escolhida.behavior),
            format_func=lambda b: b.label,
            key="edit_comp",
            disabled=tem_hist,
            help=(
                "Bloqueado: esta categoria já tem histórico."
                if tem_hist
                else "Define as regras aplicadas à categoria."
            ),
        )

    if tem_hist and novo_comportamento is not escolhida.behavior:
        novo_comportamento = escolhida.behavior

    # Qualquer categoria pode ter meta. Só ALLOCATION_GOAL usa a meta para
    # cortar o plano e redirecionar a sobra; nas outras é acompanhamento.
    tem_meta = st.checkbox(
        "Definir uma meta para esta categoria",
        value=escolhida.target_amount_cents is not None,
        key="edit_tem_meta",
        help="A meta aparece no bloco Metas da tela inicial, com barra de progresso.",
    )

    meta_cents, destino_id = None, None
    if tem_meta:
        col_c, col_d = st.columns(2)
        with col_c:
            valor_meta = st.number_input(
                "Meta (R$)",
                min_value=0.0,
                step=500.0,
                value=float(to_decimal(escolhida.target_amount_cents or 0)),
                key="edit_meta",
            )
            meta_cents = to_cents(valor_meta)
        with col_d:
            if novo_comportamento is CategoryBehavior.ALLOCATION_GOAL:
                destinos = [v for v in ativas if v.id != escolhida.id]
                indice = next(
                    (
                        i
                        for i, v in enumerate(destinos)
                        if v.id == escolhida.overflow_target_category_id
                    ),
                    0,
                )
                destino = st.selectbox(
                    "Sobra vai para",
                    options=destinos,
                    index=indice if destinos else None,
                    format_func=lambda v: v.label,
                    key="edit_overflow",
                    help="Quando a meta é atingida, o percentual restante vai para cá.",
                )
                destino_id = destino.id if destino else None
            else:
                st.caption(
                    "Meta de acompanhamento: mostra o progresso sem alterar o "
                    "rateio. Para que a sobra seja redirecionada ao atingir a "
                    "meta, o comportamento precisa ser “Meta com valor-alvo”."
                )

    col_e, col_f = st.columns(2)
    with col_e:
        capital = st.checkbox(
            "Separações formam capital investido",
            value=escolhida.counts_as_investment_capital,
            key="edit_capital",
        )
    with col_f:
        patrimonio = st.checkbox(
            "Saldo entra no patrimônio total",
            value=escolhida.include_in_net_worth,
            key="edit_patrimonio",
        )

    if tem_hist:
        st.info(
            "Esta categoria já tem histórico, então o comportamento não pode mudar. "
            "Para reclassificá-la, desative-a a partir de um mês e crie uma nova.",
            icon="🔒",
        )

    col_g, col_h = st.columns(2)
    if col_g.button("Salvar categoria", use_container_width=True, key="salvar_cat"):
        with session_scope() as session:
            permitido, motivo = cat.pode_trocar_comportamento(
                session, escolhida.id, novo_comportamento
            )
            if not permitido:
                st.error(motivo)
            else:
                cat.upsert_version(
                    session,
                    escolhida.id,
                    mes_efetivo,
                    name=novo_nome.strip() or escolhida.name,
                    emoji=novo_emoji.strip() or None,
                    display_order=int(nova_ordem),
                    behavior=novo_comportamento,
                    target_amount_cents=meta_cents,
                    overflow_target_category_id=destino_id,
                    counts_as_investment_capital=capital,
                    include_in_net_worth=patrimonio,
                )
                repo.bump_revisions_from(session, mes_efetivo)
                st.success(
                    f"Categoria atualizada a partir de {month_label(mes_efetivo)}."
                )
                st.rerun()

    rotulo_toggle = "Reativar categoria" if not escolhida.active else "Desativar categoria"
    if col_h.button(rotulo_toggle, use_container_width=True, key="toggle_cat"):
        with session_scope() as session:
            if escolhida.active:
                cat.desativar_categoria(session, escolhida.id, mes_efetivo)
            else:
                cat.reativar_categoria(session, escolhida.id, mes_efetivo, 0)
            repo.bump_revisions_from(session, mes_efetivo)
        st.success(
            f"Categoria {'desativada' if escolhida.active else 'reativada'} a partir de "
            f"{month_label(mes_efetivo)}. O histórico anterior continua intacto."
        )
        st.warning("Ajuste os percentuais para o total voltar a 100%.", icon="⚠️")
        st.rerun()


# --------------------------------------------------------------------------
# Adicionar categoria
# --------------------------------------------------------------------------
with st.expander("➕ Adicionar categoria"):
    with st.form("nova_categoria", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        with col_a:
            nome = st.text_input("Nome", placeholder="Ex.: Estudos")
            emoji = st.text_input("Emoji (opcional)", max_chars=4, placeholder="📚")
        with col_b:
            comportamento = st.selectbox(
                "Comportamento",
                options=list(CategoryBehavior),
                index=list(CategoryBehavior).index(CategoryBehavior.MONTHLY_SPENDING),
                format_func=lambda b: b.label,
            )
            pct = st.number_input(
                "% da renda", min_value=0.0, max_value=100.0, step=0.5, value=0.0
            )
        st.caption(
            "Depois de criar, ajuste os percentuais acima para o total voltar a 100%."
        )
        criar = st.form_submit_button("Criar categoria", use_container_width=True)

    if criar:
        if not nome.strip():
            st.error("Informe um nome.")
        else:
            with session_scope() as session:
                nova = cat.criar_categoria(
                    session,
                    name=nome,
                    behavior=comportamento,
                    percent_bp=bp_from_pct(pct),
                    effective_month=mes_efetivo,
                    emoji=emoji.strip() or None,
                )
                repo.bump_revisions_from(session, mes_efetivo)
            st.success(
                f"Categoria “{nome.strip()}” criada a partir de {month_label(mes_efetivo)}."
            )
            st.rerun()


# --------------------------------------------------------------------------
# Situação atual
# --------------------------------------------------------------------------
subtitulo(f"Como está em {month_label(mes)}")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Categoria": v.label,
                "Comportamento": v.behavior.label,
                "%": f"{pct_from_bp(v.percent_bp)}%",
                "Ativa": "Sim" if v.active else "Não",
                "Ordem": v.display_order,
                "Patrimônio": "Sim" if v.include_in_net_worth else "—",
                "Capital": "Sim" if v.counts_as_investment_capital else "—",
                "Meta": dinheiro_html(v.target_amount_cents)
                if v.target_amount_cents
                else "—",
            }
            for v in vistas
        ]
    ),
    use_container_width=True,
    hide_index=True,
)
st.caption(
    "Cada mês usa a versão mais recente cuja vigência começou até ele. "
    f"Versões registradas: {', '.join(month_label(m) for m in historico_versoes[:6])}"
    + (" …" if len(historico_versoes) > 6 else "")
)


# --------------------------------------------------------------------------
# Saldos iniciais dos envelopes
# --------------------------------------------------------------------------
secao("Saldos iniciais dos envelopes")
st.caption("Dinheiro que já existia nos envelopes antes de começar a usar o aplicativo.")

envelopes = [v for v in ativas if v.accumulates]
if not envelopes:
    st.info("Nenhuma categoria acumulativa ativa.", icon="🗒️")
else:
    with st.form("saldos_iniciais"):
        entradas: dict[int, float] = {}
        colunas = st.columns(min(len(envelopes), 3))
        for indice, vista in enumerate(envelopes):
            with colunas[indice % len(colunas)]:
                entradas[vista.id] = st.number_input(
                    f"{vista.label} (R$)",
                    min_value=0.0,
                    step=5.0,
                    value=float(to_decimal(saldos_iniciais.get(vista.id, 0))),
                    key=f"saldo_{vista.id}",
                )
        salvar_saldos = st.form_submit_button(
            "Salvar saldos iniciais", use_container_width=True
        )

    if salvar_saldos:
        with session_scope() as session:
            for category_id, valor in entradas.items():
                repo.set_opening_balance(session, category_id, to_cents(valor))
        st.success("Saldos iniciais atualizados.")
        st.rerun()


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------
secao("Fazer backup dos meus dados")

carimbo = datetime.now().strftime("%Y%m%d_%H%M")
col_a, col_b = st.columns(2)

with col_a:
    if backup.is_sqlite():
        try:
            st.download_button(
                "⬇️ Baixar banco (.db)",
                data=backup.exportar_sqlite_bytes(),
                file_name=f"financeiro_{carimbo}.db",
                mime="application/vnd.sqlite3",
                use_container_width=True,
            )
        except FileNotFoundError:
            st.info("O banco ainda não foi criado.")
    else:
        st.info("Download do arquivo só está disponível no SQLite.")

with col_b:
    st.download_button(
        "⬇️ Baixar tudo (.json)",
        data=backup.exportar_json().encode("utf-8"),
        file_name=f"financeiro_{carimbo}.json",
        mime="application/json",
        use_container_width=True,
    )

st.caption("O `.db` restaura tudo. O `.json` é legível e serve para conferir ou migrar.")

if backup.is_sqlite():
    with st.expander("Restaurar um backup"):
        st.warning(
            "A restauração substitui todos os dados atuais. Uma cópia do banco de "
            "agora é guardada automaticamente em `data/backups` antes da troca.",
            icon="⚠️",
        )
        enviado = st.file_uploader("Arquivo .db do backup", type=["db", "sqlite", "sqlite3"])
        confirmar = st.checkbox("Entendo que os dados atuais serão substituídos.")
        if st.button(
            "Restaurar agora", disabled=not (enviado and confirmar), use_container_width=True
        ):
            try:
                destino = backup.restaurar_sqlite(enviado.getvalue())
                st.session_state["_db_pronto"] = False
                st.success(f"Backup restaurado em {destino.name}.")
                st.rerun()
            except Exception as erro:  # noqa: BLE001 - mensagem vai para a tela
                st.error(f"Não foi possível restaurar: {erro}")
