"""Saldo que atravessa os meses e transferências entre categorias.

O que muda:

* ``category_versions`` ganha ``accumulates_balance``: até aqui, "acumular"
  era deduzido do comportamento, e por isso uma categoria de consumo como
  "Livre" não tinha como guardar a sobra sem passar a exigir separação.
  Agora as duas coisas são independentes;
* nasce ``transfers``, o registro de dinheiro que muda de categoria — seja
  porque um gasto estourou o disponível e foi coberto por outra, seja
  porque a sobra de um mês foi enviada para outro lugar.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

#: Comportamentos que já acumulavam saldo antes desta migração.
JA_ACUMULAVAM = (
    "ALLOCATION_LONG_TERM",
    "ALLOCATION_GOAL",
    "ACCUMULATING_ENVELOPE",
)
#: Comportamento de consumo mensal: passa a acumular, que é o objetivo da
#: mudança. Só ``TRACKING_ONLY`` segue sem saldo próprio.
CONSUMO = "MONTHLY_SPENDING"


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    colunas = {c["name"] for c in inspetor.get_columns("category_versions")}
    # Num banco criado já com o modelo atual, a coluna nasce junto com a
    # tabela: tentar adicioná-la de novo faria o batch do SQLite reordenar
    # colunas e cair em dependência circular.
    if "accumulates_balance" in colunas:
        _criar_transferencias_se_faltar(inspetor)
        return

    with op.batch_alter_table("category_versions") as batch:
        batch.add_column(
            sa.Column(
                "accumulates_balance",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    versoes = sa.table(
        "category_versions",
        sa.column("behavior", sa.String),
        sa.column("accumulates_balance", sa.Boolean),
    )
    op.execute(
        versoes.update()
        .where(versoes.c.behavior.in_((*JA_ACUMULAVAM, CONSUMO)))
        .values(accumulates_balance=sa.true())
    )

    if "transfers" in inspetor.get_table_names():
        return

    op.create_table(
        "transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False, index=True),
        sa.Column(
            "from_category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "to_category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        # Quando a transferência nasceu para cobrir um gasto, apagar o gasto
        # tem de desfazer a cobertura junto.
        sa.Column(
            "expense_id",
            sa.Integer(),
            sa.ForeignKey("expenses.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_transferencia_positiva"),
        sa.CheckConstraint(
            "from_category_id <> to_category_id", name="ck_transferencia_entre_diferentes"
        ),
    )


def _criar_transferencias_se_faltar(inspetor) -> None:
    """Garante a tabela de transferências num banco que já tinha a coluna."""
    if "transfers" in inspetor.get_table_names():
        return
    if "category_transfers" in inspetor.get_table_names():
        return  # já está no formato da 0003


def downgrade() -> None:
    op.drop_table("transfers")
    with op.batch_alter_table("category_versions") as batch:
        batch.drop_column("accumulates_balance")
