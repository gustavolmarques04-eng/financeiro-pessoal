"""Porta de entrada: nada financeiro aparece antes daqui.

``require_auth()`` é a primeira linha de toda página. Ela não "esconde" a
tela — ela **interrompe** a execução antes de qualquer consulta ao banco,
com ``st.stop()``. Assim não existe o instante em que os valores piscam e
depois somem: eles nunca chegam a ser lidos.
"""

from __future__ import annotations

import streamlit as st

from core import auth

#: Onde o usuário logado fica guardado entre os reruns do Streamlit.
CHAVE_SESSAO = "_usuario"


def usuario_logado() -> auth.Usuario | None:
    """Quem está nesta sessão do navegador, ou ``None``."""
    return st.session_state.get(CHAVE_SESSAO)


def _entrar(login: str, senha: str) -> None:
    """Tenta autenticar e guarda o usuário na sessão do navegador."""
    try:
        usuario = auth.entrar(login, senha)
    except auth.AuthError as erro:
        st.error(str(erro))
        return
    st.session_state[CHAVE_SESSAO] = usuario
    auth.definir_atual(usuario)
    st.rerun()


def tela_de_login() -> None:
    """Desenha o formulário de entrada e nada mais."""
    st.markdown(
        '<div class="fin-hero"><h1>💰 Financeiro</h1>'
        "<p>Entre para ver suas contas.</p></div>",
        unsafe_allow_html=True,
    )
    with st.form("login"):
        login = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        enviado = st.form_submit_button("Entrar", use_container_width=True)
    if enviado:
        _entrar(login, senha)


def require_auth() -> auth.Usuario:
    """Exige um usuário autenticado. Interrompe a página se não houver.

    Toda página chama isto **antes** de qualquer leitura: é o que garante
    que abrir `/gastos` direto na barra de endereços não mostre nada.
    """
    usuario = usuario_logado()
    if usuario is None:
        tela_de_login()
        st.stop()

    # O ``ContextVar`` vale por execução do script; o Streamlit reexecuta a
    # página inteira a cada clique, então é preciso reamarrar aqui.
    auth.definir_atual(usuario)
    return usuario


def sair() -> None:
    """Encerra a sessão do navegador."""
    st.session_state.pop(CHAVE_SESSAO, None)
    auth.definir_atual(None)
    st.rerun()
