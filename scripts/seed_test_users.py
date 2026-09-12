"""Cria as contas de acesso no Supabase Auth.

Este é o **único** lugar do projeto que usa a chave ``service_role``. O
aplicativo nunca a carrega: no Streamlit só existe a chave anônima, e cada
consulta vale apenas para quem está logado.

Uso::

    python scripts/seed_test_users.py

Precisa de, no ambiente ou no ``.env``:

    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

A senha das contas vem de ``SEED_USER_PASSWORD``. Se não estiver definida,
o script pergunta — nenhuma senha fica escrita no código nem no histórico
de comandos.

Rodar duas vezes é seguro: quem já existe é preservado, nada é duplicado e
nenhum dado financeiro é tocado.
"""

from __future__ import annotations

import getpass
import sys
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine, text  # noqa: E402

from core import settings  # noqa: E402
from core.auth import USUARIOS_CONHECIDOS  # noqa: E402


def _cliente_admin():
    """Cliente com poderes administrativos, usado só aqui."""
    from supabase import create_client

    url = settings.get("SUPABASE_URL")
    chave = settings.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not chave:
        raise SystemExit(
            "Faltam SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY.\n"
            "Pegue os dois em: Supabase → Project Settings → API."
        )
    return create_client(url, chave)


def _existentes(cliente) -> dict[str, str]:
    """E-mail -> id de quem já tem conta."""
    resposta = cliente.auth.admin.list_users()
    usuarios = resposta if isinstance(resposta, list) else getattr(resposta, "users", [])
    return {u.email: str(u.id) for u in usuarios if getattr(u, "email", None)}


def _criar_perfil(user_id: str, login: str) -> None:
    """Garante a linha de preferências, com o onboarding ainda por fazer."""
    url = settings.database_url()
    if not url:
        print("  (sem DATABASE_URL: perfil não criado)")
        return
    engine = create_engine(url, connect_args={"prepare_threshold": None})
    try:
        with engine.begin() as conexao:
            conexao.execute(
                text(
                    """
                    INSERT INTO profiles (user_id, display_name, onboarding_completed,
                                          created_at, updated_at)
                    VALUES (:uid, :nome, false, now(), now())
                    ON CONFLICT (user_id) DO NOTHING
                    """
                ),
                {"uid": uuid.UUID(user_id), "nome": login},
            )
    finally:
        engine.dispose()


def main() -> int:
    cliente = _cliente_admin()
    ja_existem = _existentes(cliente)

    senha = settings.get("SEED_USER_PASSWORD")
    if not senha:
        senha = getpass.getpass("Senha para as contas: ")
    if not senha:
        raise SystemExit("Sem senha, nada a fazer.")

    for login, email in USUARIOS_CONHECIDOS.items():
        if email in ja_existem:
            user_id = ja_existem[email]
            print(f"  {login:<10} já existe  ({user_id})")
        else:
            criado = cliente.auth.admin.create_user(
                {
                    "email": email,
                    "password": senha,
                    # Contas internas: não há caixa de entrada para confirmar.
                    "email_confirm": True,
                }
            )
            user_id = str(criado.user.id)
            print(f"  {login:<10} criado     ({user_id})")
        _criar_perfil(user_id, login)

    print("\nPronto. Entre no aplicativo com o nome de usuário e a senha.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
