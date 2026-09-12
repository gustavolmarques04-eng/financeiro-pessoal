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

from core import profile_service  # noqa: E402
from core.database import init_db, session_scope  # noqa: E402
from ui.login import (  # noqa: E402
    definir_usuario,
    sair,
    tela_de_login,
    usuario_logado,
)
from ui.shared import CSS  # noqa: E402


def main() -> None:
    """Autentica e, só então, monta a navegação."""
    st.set_page_config(
        page_title="Financeiro", page_icon="💰", layout="centered"
    )
    st.markdown(CSS, unsafe_allow_html=True)

    # Sem usuário, a navegação declarada é uma só: a tela de entrada.
    # Declarar é indispensável — se ninguém chamar st.navigation, o
    # Streamlit lista a pasta pages/ sozinho, e o menu financeiro inteiro
    # aparece para quem nem entrou.
    usuario = usuario_logado()
    if usuario is None:
        st.navigation([st.Page(tela_de_login, title="Entrar", icon="🔐")]).run()
        return

    # Reamarra o dono: o ContextVar vale por execução do script, e o
    # Streamlit reexecuta a página inteira a cada clique.
    definir_usuario(usuario)

    init_db()

    # Enquanto o primeiro acesso não termina, não existe navegação: o app
    # não tem nada a mostrar, porque o usuário ainda não disse como quer
    # dividir o dinheiro dele.
    with session_scope() as session:
        falta_configurar = profile_service.precisa_de_onboarding(session)
    if falta_configurar:
        st.navigation(
            [st.Page("pages/primeiro_acesso.py", title="Primeiro acesso", icon="👋")]
        ).run()
        return

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
