"""Configuração vinda do ambiente ou dos segredos do Streamlit.

Rodando no seu computador, os valores vêm do arquivo ``.env``. Publicado no
Streamlit Community Cloud, vêm do painel de *Secrets*. Este módulo esconde
essa diferença do resto do aplicativo — e nunca importa o Streamlit à
força, para que testes e scripts continuem funcionando sem ele.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _from_streamlit(nome: str) -> str | None:
    """Lê um segredo do Streamlit, se estivermos rodando dentro dele."""
    try:
        import streamlit as st
    except ModuleNotFoundError:  # pragma: no cover - só fora do app
        return None

    try:
        valor = st.secrets.get(nome)
    except Exception:  # pragma: no cover - sem secrets.toml configurado
        return None
    return str(valor) if valor else None


def get(nome: str, padrao: str = "") -> str:
    """Valor de uma configuração: variável de ambiente ou segredo do Streamlit.

    A variável de ambiente tem prioridade, para que dá para sobrescrever
    localmente sem mexer em nada publicado.
    """
    do_ambiente = os.getenv(nome, "").strip()
    if do_ambiente:
        return do_ambiente
    return _from_streamlit(nome) or padrao


def database_url(padrao: str = "") -> str:
    """URL do banco configurada, se houver."""
    return get("DATABASE_URL", padrao)


def admin_database_url() -> str:
    """URL com poder de alterar o esquema.

    O aplicativo em produção conecta com uma role sem privilégio, que não
    pode criar nem alterar tabelas — é justamente isso que faz a RLS valer
    para ele. Migrações e scripts administrativos precisam de outra
    conexão, e é esta. Quando não houver, cai na de sempre: é o caso do
    SQLite local e dos testes, onde não existe essa separação.
    """
    return get("ADMIN_DATABASE_URL") or database_url()
