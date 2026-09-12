"""Quem está usando o aplicativo, e como o banco fica sabendo disso.

O login é do Supabase Auth: é ele que guarda a senha, e nenhuma tabela
financeira jamais a vê. O que sai daqui para o resto do sistema é só um
identificador — o ``user_id`` — e é ele que amarra cada consulta ao dono
certo.

A amarração não é um ``WHERE`` que alguém pode esquecer de escrever. Cada
transação declara ao PostgreSQL quem está falando::

    SET LOCAL ROLE authenticated;
    SET LOCAL request.jwt.claims = '{"sub": "<user_id>", ...}';

A partir daí ``auth.uid()`` responde, as políticas de RLS entram em ação e
o banco recusa o que não for do usuário. Como o aplicativo se conecta com
uma role sem ``BYPASSRLS`` e que não é dona das tabelas, um caminho de
código que esqueça de amarrar o usuário **não devolve nada** — falha
fechada, em vez de vazar.

``SET LOCAL`` vale só até o fim da transação, o que é justamente o que se
quer num pool de conexões: a conexão devolvida ao pool não carrega o
usuário anterior.
"""

from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import settings

#: Contas de teste desta fase. O mapeamento é explícito de propósito: são
#: dois usuários conhecidos, não um cadastro aberto.
DOMINIO_INTERNO = "financeiro.local"
USUARIOS_CONHECIDOS: dict[str, str] = {
    "gustavo": f"gustavo@{DOMINIO_INTERNO}",
    "melissa": f"melissa@{DOMINIO_INTERNO}",
}


class AuthError(Exception):
    """Falha de autenticação, com mensagem já pronta para a tela."""


@dataclass(frozen=True)
class Usuario:
    """Quem está logado nesta sessão."""

    id: uuid.UUID
    email: str
    #: Nome curto usado no login ("gustavo"), útil para saudação.
    login: str

    @property
    def claims(self) -> str:
        """Claims mínimas que o PostgreSQL precisa para responder ``auth.uid()``."""
        return json.dumps(
            {"sub": str(self.id), "role": "authenticated", "email": self.email}
        )


def email_de(login: str) -> str:
    """E-mail interno correspondente ao nome de usuário digitado."""
    chave = login.strip().lower()
    if chave in USUARIOS_CONHECIDOS:
        return USUARIOS_CONHECIDOS[chave]
    # Quem digitar o e-mail completo também entra.
    return chave


def _cliente():
    """Cliente do Supabase, criado na hora e nunca compartilhado.

    Um cliente guardado em cache global carregaria o token de quem logou
    antes — é exatamente o vazamento entre sessões que não pode existir.
    """
    try:
        from supabase import create_client
    except ModuleNotFoundError as erro:  # pragma: no cover - dependência externa
        raise AuthError(
            "A biblioteca do Supabase não está instalada. "
            "Rode: pip install supabase"
        ) from erro

    url = settings.get("SUPABASE_URL")
    chave = settings.get("SUPABASE_ANON_KEY")
    if not url or not chave:
        raise AuthError(
            "Faltam SUPABASE_URL e SUPABASE_ANON_KEY na configuração do aplicativo."
        )
    return create_client(url, chave)


def entrar(login: str, senha: str) -> Usuario:
    """Autentica no Supabase e devolve quem entrou.

    A senha vai direto para o Supabase e não é guardada em lugar nenhum:
    nem em variável de sessão, nem em tabela, nem em log.
    """
    if not login.strip() or not senha:
        raise AuthError("Informe usuário e senha.")

    email = email_de(login)
    cliente = _cliente()
    try:
        resposta = cliente.auth.sign_in_with_password(
            {"email": email, "password": senha}
        )
    except Exception as erro:  # a biblioteca lança tipos variados
        raise AuthError("Usuário ou senha incorretos.") from erro

    usuario = getattr(resposta, "user", None)
    if usuario is None or not getattr(usuario, "id", None):
        raise AuthError("Usuário ou senha incorretos.")

    return Usuario(
        id=uuid.UUID(str(usuario.id)),
        email=email,
        login=login.strip().lower(),
    )


def amarrar_sessao(session: Session, usuario: Usuario | None) -> None:
    """Declara ao banco quem está falando nesta transação.

    Chamada uma vez por ``session_scope()``. Sem ela, ``auth.uid()`` devolve
    ``NULL`` e as políticas recusam tudo — que é o comportamento desejado
    para um acesso sem dono.

    No SQLite (testes locais) não há RLS nem ``auth.uid()``; o isolamento
    dos testes vem do filtro por ``user_id``, e os testes de RLS de verdade
    rodam contra PostgreSQL.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    if usuario is None:
        session.execute(text("SELECT set_config('request.jwt.claims', '', true)"))
        return
    session.execute(text("SET LOCAL ROLE authenticated"))
    session.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": usuario.claims},
    )


def usuario_do_banco(session: Session) -> uuid.UUID | None:
    """O que o banco entende como usuário atual. Usado em testes e auditoria."""
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return None
    achado = session.execute(text("SELECT auth.uid()")).scalar()
    return uuid.UUID(str(achado)) if achado else None


# --------------------------------------------------------------------------
# Usuário corrente do processo
# --------------------------------------------------------------------------
#: Quem está logado *nesta* execução do script. Uma ``ContextVar`` não é
#: compartilhada entre execuções concorrentes do Streamlit, então a sessão
#: de um usuário não enxerga o usuário de outra.
_ATUAL: ContextVar[Usuario | None] = ContextVar("usuario_atual", default=None)


def definir_atual(usuario: Usuario | None) -> None:
    """Marca quem está usando o aplicativo nesta execução."""
    _ATUAL.set(usuario)


def atual() -> Usuario | None:
    """Quem está logado, ou ``None`` fora de uma sessão autenticada."""
    return _ATUAL.get()


def dono() -> uuid.UUID:
    """Id do usuário logado; erro se não houver.

    É o que os repositórios usam para carimbar e filtrar cada linha. Falhar
    alto aqui é de propósito: uma consulta sem dono seria uma consulta sem
    isolamento.
    """
    usuario = _ATUAL.get()
    if usuario is None:
        raise AuthError("Nenhum usuário autenticado nesta sessão.")
    return usuario.id
