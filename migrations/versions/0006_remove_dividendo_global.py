"""Remove o campo de dividendo global do fechamento.

Com os dividendos pertencendo a cada categoria (migração 0005), a coluna
``monthly_closings.dividendos_cents`` deixou de ser lida por qualquer
código. Uma coluna assim é pior que inútil: convida alguém a gravar nela e
a se perguntar por que o número não aparece em lugar nenhum.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    colunas = {c["name"] for c in inspetor.get_columns("monthly_closings")}
    if "dividendos_cents" not in colunas:
        return

    with op.batch_alter_table("monthly_closings") as batch:
        batch.drop_column("dividendos_cents")


def downgrade() -> None:
    with op.batch_alter_table("monthly_closings") as batch:
        batch.add_column(
            sa.Column(
                "dividendos_cents",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
