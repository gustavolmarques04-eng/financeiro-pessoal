"""Dividendo passa a pertencer à categoria que o gerou.

Até aqui os dividendos eram um número solto no fechamento do mês: o app
sabia que tinham entrado R$ 40, mas não de onde. Quem investe em mais de
uma frente não consegue responder qual carteira rendeu, e reinvestir vira
adivinhação.

Agora cada categoria declara se recebe dividendos
(``receives_dividends``), e o valor informado no fechamento entra **nela**
— aumentando o saldo, porque dividendo reinvestido é dinheiro que passou a
existir ali.

O registro reaproveita ``balance_adjustments``, com o motivo
``DIVIDENDO``: é a mesma ideia de "dinheiro que entrou sem ter sido
separado", e mantém a regra de que todo centavo tem uma linha que o
explica.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    colunas = {c["name"] for c in inspetor.get_columns("category_versions")}
    if "receives_dividends" in colunas:
        return

    with op.batch_alter_table("category_versions") as batch:
        batch.add_column(
            sa.Column(
                "receives_dividends",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("category_versions") as batch:
        batch.drop_column("receives_dividends")
