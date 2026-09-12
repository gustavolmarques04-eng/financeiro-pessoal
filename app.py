"""Ponto de entrada do aplicativo de finanças pessoais.

Executar com::

    streamlit run app.py

O acesso é por usuário e senha, verificados pelo Supabase Auth. Antes de
entrar não existe menu, página nem consulta ao banco: a navegação só é
montada depois que alguém se identifica.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.database import init_db  # noqa: E402
from ui.login import require_auth, sair  # noqa: E402
from ui.shared import CSS  # noqa: E402


def main() -> None:
    """Autentica e, só então, monta a navegação."""
    st.set_page_config(
        page_title="Financeiro", page_icon="💰", layout="centered"
    )
    st.markdown(CSS, unsafe_allow_html=True)

    # Nada acima desta linha lê dinheiro; nada abaixo dela roda sem dono.
    usuario = require_auth()

    init_db()

    paginas = [
        st.Page("pages/dashboard.py", title="Início", icon="🏠", default=True),
        st.Page("pages/separacoes.py", title="Separações", icon="🎯"),
        st.Page("pages/receitas.py", title="Receitas", icon="💰"),
        st.Page("pages/gastos.py", title="Gastos", icon="💳"),
        st.Page("pages/fechamento.py", title="Fechamento", icon="📅"),
        st.Page("pages/configuracoes.py", title="Configurações", icon="⚙️"),
    ]
    with st.sidebar:
        st.caption(f"Conectado como **{usuario.login}**")
        if st.button("Sair", use_container_width=True):
            sair()

    st.navigation(paginas, position="top").run()


main()
