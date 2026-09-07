"""Ponto de entrada do aplicativo de finanças pessoais.

Executar com::

    streamlit run app.py

Se ``APP_PASSWORD`` estiver definida no ambiente, uma tela de senha aparece
antes do aplicativo. Sem a variável, roda direto — o caso local.
"""

from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

load_dotenv(RAIZ / ".env")

from core.database import init_db  # noqa: E402
from ui.shared import CSS  # noqa: E402


def _senha_confere(digitada: str) -> bool:
    """Compara a senha em tempo constante para não vazar informação."""
    esperada = os.getenv("APP_PASSWORD", "")
    return bool(esperada) and hmac.compare_digest(digitada, esperada)


def autenticar() -> bool:
    """Mostra a tela de senha quando ``APP_PASSWORD`` está configurada.

    Devolve ``True`` quando o acesso está liberado.
    """
    if not os.getenv("APP_PASSWORD"):
        return True
    if st.session_state.get("_autenticado"):
        return True

    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="fin-header"><h1>💰 Financeiro</h1>'
        "<p>Aplicativo privado. Informe a senha para continuar.</p></div>",
        unsafe_allow_html=True,
    )

    with st.form("login"):
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", use_container_width=True)

    if entrar:
        if _senha_confere(senha):
            st.session_state["_autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    return False


def main() -> None:
    """Monta a navegação e entrega o controle à página escolhida."""
    if not autenticar():
        return

    init_db()

    paginas = [
        st.Page("pages/dashboard.py", title="Início", icon="🏠", default=True),
        st.Page("pages/receitas.py", title="Receitas", icon="💰"),
        st.Page("pages/gastos.py", title="Gastos", icon="💳"),
        st.Page("pages/fechamento.py", title="Fechamento mensal", icon="📅"),
        st.Page("pages/configuracoes.py", title="Configurações", icon="⚙️"),
    ]
    st.navigation(paginas, position="top").run()


main()
