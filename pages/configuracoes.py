"""Página de configurações: percentuais versionados, envelopes e backup."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from core import backup_service as backup
from core import repositories as repo
from core.database import session_scope
from core.models import Category
from core.utils import (
    bp_from_pct,
    format_brl,
    month_label,
    month_start,
    pct_from_bp,
    to_cents,
    to_decimal,
)
from ui.shared import (
    cabecalho,
    configurar_pagina,
    garantir_banco,
    secao,
    seletor_mes,
)

configurar_pagina("Configurações")
garantir_banco()

cabecalho("⚙️ Configurações", "Percentuais, meta da reserva e backup dos seus dados.")
mes = seletor_mes("cfg")

ORDEM = (
    Category.INDEPENDENCIA,
    Category.RESERVA,
    Category.VIAGEM,
    Category.COMPRAS,
    Category.NAMORADA,
    Category.AMIGOS,
    Category.LIVRE,
)

with session_scope() as session:
    vigente = repo.get_settings_for_month(session, mes)
    pesos_atuais = {c: bp for c, bp in vigente.pesos_bp().items()}
    meta_atual = vigente.meta_reserva_cents
    vigente_desde = vigente.effective_month
    versoes = [
        {
            "mes": v.effective_month,
            "meta": v.meta_reserva_cents,
            "pesos": dict(v.pesos_bp()),
        }
        for v in repo.list_settings_versions(session)
    ]
    saldo_compras = repo.get_opening_balance(session, Category.COMPRAS)
    saldo_viagem = repo.get_opening_balance(session, Category.VIAGEM)

st.caption(
    f"Configuração vigente em {month_label(mes)}: versão de "
    f"{month_label(vigente_desde)}."
)


# --------------------------------------------------------------------------
# Percentuais
# --------------------------------------------------------------------------
secao("Percentuais e meta da reserva")

with st.form("configuracao"):
    meta = st.number_input(
        "Meta da reserva (R$)",
        min_value=0.0,
        step=500.0,
        value=float(to_decimal(meta_atual)),
    )

    novos_bp: dict[Category, int] = {}
    for inicio in range(0, len(ORDEM), 2):
        colunas = st.columns(2)
        for coluna, categoria in zip(colunas, ORDEM[inicio : inicio + 2]):
            with coluna:
                valor = st.number_input(
                    f"% {categoria.value}",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    value=float(pct_from_bp(pesos_atuais[categoria])),
                    key=f"pct_{categoria.name}",
                )
                novos_bp[categoria] = bp_from_pct(valor)

    total_bp = sum(novos_bp.values())
    total_pct = pct_from_bp(total_bp)
    if total_bp == 10_000:
        st.success(f"Total dos percentuais: {total_pct}% ✔")
    else:
        st.error(f"Total dos percentuais: {total_pct}% — precisa somar exatamente 100%.")

    aplicar_de = st.date_input(
        "Aplicar a partir de qual mês?",
        value=month_start(date.today()),
        format="DD/MM/YYYY",
        help="Meses anteriores continuam com a configuração antiga.",
    )

    salvar = st.form_submit_button("Salvar configuração", use_container_width=True)

if salvar:
    if total_bp != 10_000:
        st.error("Não é possível salvar: os percentuais precisam somar 100%.")
    else:
        alvo = month_start(aplicar_de)
        with session_scope() as session:
            repo.upsert_settings_version(
                session,
                effective_month=alvo,
                meta_reserva_cents=to_cents(meta),
                percentuais_bp=novos_bp,
            )
            afetados = repo.bump_revisions_from(session, alvo)
        st.success(
            f"Configuração válida a partir de {month_label(alvo)}. "
            f"{len(afetados)} mês(es) recalculado(s); meses anteriores não mudaram."
        )
        st.rerun()


# --------------------------------------------------------------------------
# Histórico de versões
# --------------------------------------------------------------------------
secao("Versões da configuração")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Vale a partir de": month_label(v["mes"]),
                "Meta reserva": format_brl(v["meta"]),
                **{
                    c.value.split()[0]: f"{pct_from_bp(v['pesos'][c])}%"
                    for c in ORDEM
                },
            }
            for v in versoes
        ]
    ),
    use_container_width=True,
    hide_index=True,
)
st.caption("Cada mês usa a versão mais recente cuja vigência começou até aquele mês.")


# --------------------------------------------------------------------------
# Saldos iniciais dos envelopes
# --------------------------------------------------------------------------
secao("Saldos iniciais dos envelopes")
st.caption("Dinheiro que já existia nos envelopes antes de começar a usar o aplicativo.")

with st.form("saldos_iniciais"):
    col_a, col_b = st.columns(2)
    with col_a:
        inicial_compras = st.number_input(
            "Compras pessoais (R$)",
            min_value=0.0,
            step=5.0,
            value=float(to_decimal(saldo_compras)),
        )
    with col_b:
        inicial_viagem = st.number_input(
            "Viagem (R$)", min_value=0.0, step=5.0, value=float(to_decimal(saldo_viagem))
        )
    salvar_saldos = st.form_submit_button("Salvar saldos iniciais", use_container_width=True)

if salvar_saldos:
    with session_scope() as session:
        repo.set_opening_balance(session, Category.COMPRAS, to_cents(inicial_compras))
        repo.set_opening_balance(session, Category.VIAGEM, to_cents(inicial_viagem))
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
