"""Apagar uma categoria não pode zerar o dono do perfil.

As chaves do perfil que apontam para categorias — "meus investimentos" e
"minha reserva" — são compostas com ``user_id``, para que ninguém possa
apontar para a categoria de outra pessoa. Mas com ``ON DELETE SET NULL``
o PostgreSQL zera **todas** as colunas da chave, e ``user_id`` é
``NOT NULL``: apagar qualquer categoria derrubava a operação inteira.

Apareceu ao zerar os dados de teste, que é exatamente quando categorias
são apagadas em massa.

A correção usa a forma do PostgreSQL 15+, que permite dizer **quais**
colunas devem ser zeradas::

    ON DELETE SET NULL (investment_category_id)

Assim a referência some e o dono continua no lugar.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CHAVES = (
    ("fk_perfil_investimentos_do_usuario", "investment_category_id"),
    ("fk_perfil_reserva_do_usuario", "reserve_category_id"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # No SQLite as chaves compostas não disparam SET NULL parcial; o
        # cenário simplesmente não acontece.
        return

    for nome, coluna in CHAVES:
        op.execute(sa.text(f'ALTER TABLE profiles DROP CONSTRAINT IF EXISTS "{nome}"'))
        op.execute(
            sa.text(
                f"""
                ALTER TABLE profiles
                ADD CONSTRAINT "{nome}"
                FOREIGN KEY ({coluna}, user_id)
                REFERENCES categories (id, user_id)
                ON DELETE SET NULL ({coluna})
                """
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for nome, coluna in CHAVES:
        op.execute(sa.text(f'ALTER TABLE profiles DROP CONSTRAINT IF EXISTS "{nome}"'))
        op.execute(
            sa.text(
                f"""
                ALTER TABLE profiles
                ADD CONSTRAINT "{nome}"
                FOREIGN KEY ({coluna}, user_id)
                REFERENCES categories (id, user_id)
                ON DELETE SET NULL
                """
            )
        )
