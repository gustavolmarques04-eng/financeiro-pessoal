"""Dois usuários independentes, com o isolamento garantido pelo banco.

O que muda:

* toda tabela financeira ganha ``user_id`` e passa a ter chaves
  estrangeiras **compostas** — ``(category_id, user_id)`` em vez de só
  ``category_id``. Sem isso o banco aceitaria um gasto de um usuário
  apontando para a categoria de outro;
* ``transfers`` dá lugar a ``category_transfers``, com data própria e
  vínculo de estorno;
* nascem ``profiles`` (preferências e onboarding), ``balance_adjustments``
  (a diferença entre o saldo calculado e o real conferido) e as políticas
  de RLS;
* nasce a role ``app_user``: é com ela que o aplicativo se conecta em
  produção. Ela **não** é dona das tabelas e **não** tem ``BYPASSRLS``, de
  modo que uma consulta que esqueça de dizer quem é o usuário não devolve
  nada em vez de devolver tudo.

Os dados financeiros anteriores são apagados, com autorização explícita:
os dois usuários começam zerados. O esquema, as migrações e as contas do
Supabase Auth são preservados.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

#: Toda tabela que guarda dinheiro de alguém. Nenhuma pode ficar de fora:
#: uma tabela esquecida é uma tabela sem isolamento.
TABELAS_DO_USUARIO = (
    "categories",
    "category_versions",
    "incomes",
    "expenses",
    "expense_installments",
    "month_revisions",
    "allocation_states",
    "monthly_closings",
    "opening_balances",
    "category_transfers",
    "balance_adjustments",
    "profiles",
    "import_log",
)

#: Ordem de remoção: filhos antes dos pais.
ORDEM_DE_LIMPEZA = (
    "transfers",
    "expense_installments",
    "expenses",
    "allocation_states",
    "opening_balances",
    "monthly_closings",
    "month_revisions",
    "incomes",
    "import_log",
    "category_versions",
    "categories",
)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    _limpar_dados_financeiros()
    _recriar_tabelas()
    if _postgres():
        _criar_role_do_aplicativo()
        _ligar_rls()


def _limpar_dados_financeiros() -> None:
    """Esvazia as tabelas antigas antes de mudar o formato.

    Sem um ``user_id`` para atribuir, não haveria como converter as linhas
    existentes sem inventar um dono. Como a troca para dois usuários foi
    autorizada com os dados zerados, apagar é mais honesto que adivinhar.
    """
    inspetor = sa.inspect(op.get_bind())
    existentes = set(inspetor.get_table_names())
    for tabela in ORDEM_DE_LIMPEZA:
        if tabela in existentes:
            op.execute(sa.text(f'DELETE FROM "{tabela}"'))


def _recriar_tabelas() -> None:
    """Recria o esquema no formato multiusuário.

    Com as tabelas vazias, derrubar e recriar sai mais simples — e mais
    seguro — do que uma sequência de ``ALTER TABLE`` que teria de inventar
    valores para colunas ``NOT NULL`` e reconstruir cada chave composta.
    """
    from core.models import Base

    ligacao = op.get_bind()
    inspetor = sa.inspect(ligacao)
    existentes = set(inspetor.get_table_names())

    for tabela in ORDEM_DE_LIMPEZA:
        if tabela in existentes:
            op.drop_table(tabela)

    financeiras = [
        tabela
        for nome, tabela in Base.metadata.tables.items()
        if nome in TABELAS_DO_USUARIO
    ]
    Base.metadata.create_all(ligacao, tables=financeiras, checkfirst=True)


def _criar_role_do_aplicativo() -> None:
    """Cria a role sem privilégios que o aplicativo usa em produção.

    A senha vem de ``app.app_user_password`` quando definida; se não
    estiver, a role é criada sem login e o administrador define a senha
    depois. Nunca há senha escrita no código.
    """
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                    CREATE ROLE app_user NOLOGIN NOBYPASSRLS NOSUPERUSER
                        NOCREATEDB NOCREATEROLE;
                END IF;
            END $$;
            """
        )
    )
    # Poder assumir 'authenticated' é o que faz auth.uid() valer; as
    # políticas são escritas para essa role.
    op.execute(sa.text("GRANT authenticated TO app_user"))
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO app_user, authenticated"))
    for tabela in TABELAS_DO_USUARIO:
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" '
                "TO app_user, authenticated"
            )
        )
    op.execute(
        sa.text(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
            "TO app_user, authenticated"
        )
    )


def _ligar_rls() -> None:
    """Liga RLS e escreve as políticas de cada tabela.

    ``FORCE`` é indispensável: sem ele o **dono** da tabela continua
    enxergando tudo, e o dono aqui é justamente quem roda as migrações.
    """
    for tabela in TABELAS_DO_USUARIO:
        op.execute(sa.text(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY'))
        for acao, clausula in (
            ("SELECT", "USING (user_id = auth.uid())"),
            ("INSERT", "WITH CHECK (user_id = auth.uid())"),
            # UPDATE precisa das duas: USING escolhe as linhas que podem ser
            # alteradas, WITH CHECK impede que a alteração troque o dono.
            (
                "UPDATE",
                "USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())",
            ),
            ("DELETE", "USING (user_id = auth.uid())"),
        ):
            nome = f"{tabela}_{acao.lower()}_do_dono"
            op.execute(sa.text(f'DROP POLICY IF EXISTS "{nome}" ON "{tabela}"'))
            op.execute(
                sa.text(
                    f'CREATE POLICY "{nome}" ON "{tabela}" '
                    f"FOR {acao} TO authenticated, app_user {clausula}"
                )
            )


def downgrade() -> None:
    """Volta ao esquema de usuário único, sem os dados."""
    if _postgres():
        for tabela in TABELAS_DO_USUARIO:
            op.execute(sa.text(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY'))
    for tabela in (
        "profiles",
        "balance_adjustments",
        "category_transfers",
    ):
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{tabela}"'))
