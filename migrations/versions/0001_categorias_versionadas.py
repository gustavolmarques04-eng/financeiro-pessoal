"""Categorias versionadas substituindo os percentuais fixos.

O que muda:

* nascem ``categories`` (identidade estável) e ``category_versions``
  (nome, emoji, percentual, comportamento e metas a partir de um mês);
* ``expenses``, ``allocation_states`` e ``opening_balances`` passam a
  apontar para ``categories`` por id, em vez de guardar o nome;
* ``settings_versions`` deixa de existir: cada linha vira um conjunto de
  versões de categoria com o mesmo ``effective_month``, preservando o
  histórico do plano.

Roda nos dois cenários: banco novo (cria tudo) e banco antigo (converte os
dados existentes sem apagar nada).

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SEED_MONTH = date(2000, 1, 1)

#: Mapa do enum antigo (guardado por nome) para o slug estável.
SLUG_POR_NOME_ANTIGO = {
    "INDEPENDENCIA": "independencia",
    "Independência financeira": "independencia",
    "RESERVA": "reserva",
    "Reserva de emergência": "reserva",
    "Reserva": "reserva",
    "VIAGEM": "viagem",
    "Viagem": "viagem",
    "COMPRAS": "compras",
    "Compras pessoais": "compras",
    "NAMORADA": "namorada",
    "Namorada": "namorada",
    "AMIGOS": "amigos",
    "Amigos": "amigos",
    "LIVRE": "livre",
    "Livre": "livre",
    "OUTRO": "outro",
    "Outro": "outro",
}

#: Propriedades das categorias-semente. A coluna ``percent_col`` diz de onde
#: vem o percentual quando existe um ``settings_versions`` antigo.
SEEDS = [
    {
        "slug": "independencia",
        "name": "Independência financeira",
        "emoji": "📈",
        "behavior": "ALLOCATION_LONG_TERM",
        "percent_bp": 4900,
        "percent_col": "pct_independencia_bp",
        "display_order": 1,
        "counts_as_investment_capital": True,
        "include_in_net_worth": True,
        "balance_from_closing": "INVESTIMENTOS",
    },
    {
        "slug": "reserva",
        "name": "Reserva de emergência",
        "emoji": "🛟",
        "behavior": "ALLOCATION_GOAL",
        "percent_bp": 1700,
        "percent_col": "pct_reserva_bp",
        "display_order": 2,
        "target_amount_cents": 600_000,
        "overflow_target_slug": "independencia",
        "include_in_net_worth": True,
        "balance_from_closing": "RESERVA",
    },
    {
        "slug": "viagem",
        "name": "Viagem",
        "emoji": "✈️",
        "behavior": "ACCUMULATING_ENVELOPE",
        "percent_bp": 1400,
        "percent_col": "pct_viagem_bp",
        "display_order": 3,
        "include_in_net_worth": True,
    },
    {
        "slug": "compras",
        "name": "Compras pessoais",
        "emoji": "🛍️",
        "behavior": "ACCUMULATING_ENVELOPE",
        "percent_bp": 900,
        "percent_col": "pct_compras_bp",
        "display_order": 4,
        "include_in_net_worth": True,
    },
    {
        "slug": "namorada",
        "name": "Namorada",
        "emoji": "💕",
        "behavior": "MONTHLY_SPENDING",
        "percent_bp": 700,
        "percent_col": "pct_namorada_bp",
        "display_order": 5,
    },
    {
        "slug": "amigos",
        "name": "Amigos",
        "emoji": "🍻",
        "behavior": "MONTHLY_SPENDING",
        "percent_bp": 300,
        "percent_col": "pct_amigos_bp",
        "display_order": 6,
    },
    {
        "slug": "livre",
        "name": "Livre",
        "emoji": "🎲",
        "behavior": "MONTHLY_SPENDING",
        "percent_bp": 100,
        "percent_col": "pct_livre_bp",
        "display_order": 7,
    },
    {
        "slug": "outro",
        "name": "Outro",
        "emoji": "📦",
        "behavior": "TRACKING_ONLY",
        "percent_bp": 0,
        "percent_col": None,
        "display_order": 99,
    },
]


def _tabelas(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _colunas(conn, tabela: str) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(tabela)}


# --------------------------------------------------------------------------
# Criação das tabelas
# --------------------------------------------------------------------------
def _criar_categorias(conn) -> None:
    """Cria ``categories`` e ``category_versions`` se ainda não existirem."""
    tabelas = _tabelas(conn)
    if "categories" not in tabelas:
        op.create_table(
            "categories",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("slug", sa.String(60), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_categories_slug", "categories", ["slug"])

    if "category_versions" not in tabelas:
        op.create_table(
            "category_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "category_id",
                sa.Integer(),
                sa.ForeignKey("categories.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("effective_month", sa.Date(), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("emoji", sa.String(8), nullable=True),
            sa.Column(
                "behavior",
                sa.Enum(
                    "ALLOCATION_LONG_TERM",
                    "ALLOCATION_GOAL",
                    "ACCUMULATING_ENVELOPE",
                    "MONTHLY_SPENDING",
                    "TRACKING_ONLY",
                    name="categorybehavior",
                ),
                nullable=False,
            ),
            sa.Column("percent_bp", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("target_amount_cents", sa.Integer(), nullable=True),
            sa.Column(
                "overflow_target_category_id",
                sa.Integer(),
                sa.ForeignKey("categories.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "counts_as_investment_capital",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "include_in_net_worth",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "balance_from_closing",
                sa.Enum("RESERVA", "INVESTIMENTOS", name="closingfield"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("category_id", "effective_month", name="uq_versao_por_mes"),
            sa.CheckConstraint("percent_bp >= 0", name="ck_percentual_nao_negativo"),
            sa.CheckConstraint(
                "target_amount_cents IS NULL OR target_amount_cents >= 0",
                name="ck_meta_nao_negativa",
            ),
        )
        op.create_index(
            "ix_category_versions_category_id", "category_versions", ["category_id"]
        )
        op.create_index(
            "ix_category_versions_effective_month", "category_versions", ["effective_month"]
        )


def _criar_restantes(conn) -> None:
    """Cria as demais tabelas do modelo quando o banco está vazio."""
    from core.models import Base

    Base.metadata.create_all(conn, checkfirst=True)


# --------------------------------------------------------------------------
# Semeadura e conversão
# --------------------------------------------------------------------------
def _semear_categorias(conn) -> dict[str, int]:
    """Insere as categorias-semente e devolve ``slug -> id``."""
    ids: dict[str, int] = {
        slug: cid
        for slug, cid in conn.execute(sa.text("SELECT slug, id FROM categories")).all()
    }
    agora = datetime.now(timezone.utc)

    for seed in SEEDS:
        slug = str(seed["slug"])
        if slug in ids:
            continue
        conn.execute(
            sa.text("INSERT INTO categories (slug, created_at) VALUES (:s, :c)"),
            {"s": slug, "c": agora},
        )
        ids[slug] = conn.execute(
            sa.text("SELECT id FROM categories WHERE slug = :s"), {"s": slug}
        ).scalar_one()
    return ids


def _versoes_do_plano(conn, ids: dict[str, int]) -> None:
    """Converte cada ``settings_versions`` antigo em versões de categoria.

    Sem histórico antigo, cria uma única versão-semente. Com histórico, cada
    ``effective_month`` vira um conjunto de versões — o plano de cada mês
    passado continua exatamente como era.
    """
    tabelas = _tabelas(conn)
    legado: list[dict] = []
    if "settings_versions" in tabelas:
        colunas = _colunas(conn, "settings_versions")
        campos = ["effective_month", "meta_reserva_cents"] + [
            str(s["percent_col"]) for s in SEEDS if s["percent_col"] in colunas
        ]
        linhas = conn.execute(
            sa.text(f"SELECT {', '.join(campos)} FROM settings_versions")
        ).mappings().all()
        legado = [dict(linha) for linha in linhas]

    if not legado:
        legado = [{"effective_month": SEED_MONTH, "meta_reserva_cents": 600_000}]

    agora = datetime.now(timezone.utc)
    for linha in legado:
        mes = linha["effective_month"]
        if isinstance(mes, str):
            mes = date.fromisoformat(mes[:10])
        meta = linha.get("meta_reserva_cents") or 600_000

        for seed in SEEDS:
            slug = str(seed["slug"])
            coluna = seed["percent_col"]
            percentual = (
                int(linha[coluna])
                if coluna and coluna in linha and linha[coluna] is not None
                else int(seed["percent_bp"])
            )
            ja_existe = conn.execute(
                sa.text(
                    "SELECT 1 FROM category_versions "
                    "WHERE category_id = :c AND effective_month = :m"
                ),
                {"c": ids[slug], "m": mes},
            ).first()
            if ja_existe:
                continue

            overflow = seed.get("overflow_target_slug")
            conn.execute(
                sa.text(
                    "INSERT INTO category_versions ("
                    " category_id, effective_month, name, emoji, behavior, percent_bp,"
                    " display_order, active, target_amount_cents,"
                    " overflow_target_category_id, counts_as_investment_capital,"
                    " include_in_net_worth, balance_from_closing, created_at"
                    ") VALUES ("
                    " :cid, :mes, :nome, :emoji, :beh, :pct, :ordem, 1, :meta,"
                    " :overflow, :capital, :patrimonio, :closing, :criado)"
                ),
                {
                    "cid": ids[slug],
                    "mes": mes,
                    "nome": seed["name"],
                    "emoji": seed["emoji"],
                    "beh": seed["behavior"],
                    "pct": percentual,
                    "ordem": seed["display_order"],
                    "meta": meta if seed.get("target_amount_cents") else None,
                    "overflow": ids[str(overflow)] if overflow else None,
                    "capital": 1 if seed.get("counts_as_investment_capital") else 0,
                    "patrimonio": 1 if seed.get("include_in_net_worth") else 0,
                    "closing": seed.get("balance_from_closing"),
                    "criado": agora,
                },
            )


def _converter_coluna_categoria(conn, tabela: str, ids: dict[str, int]) -> None:
    """Troca a coluna textual ``category`` por ``category_id``."""
    if tabela not in _tabelas(conn):
        return
    colunas = _colunas(conn, tabela)
    if "category" not in colunas:
        return

    if "category_id" not in colunas:
        op.add_column(tabela, sa.Column("category_id", sa.Integer(), nullable=True))

    fallback = ids["outro"]
    for nome_antigo, slug in SLUG_POR_NOME_ANTIGO.items():
        conn.execute(
            sa.text(f"UPDATE {tabela} SET category_id = :cid WHERE category = :nome"),
            {"cid": ids.get(slug, fallback), "nome": nome_antigo},
        )
    conn.execute(
        sa.text(f"UPDATE {tabela} SET category_id = :cid WHERE category_id IS NULL"),
        {"cid": fallback},
    )

    # O índice antigo aponta para a coluna que vai sair. Sem removê-lo, o
    # SQLite tenta recriá-lo ao reconstruir a tabela e quebra.
    indices = {i["name"] for i in sa.inspect(conn).get_indexes(tabela)}
    for nome in (f"ix_{tabela}_category", f"ix_{tabela}_category_id"):
        if nome in indices:
            op.drop_index(nome, table_name=tabela)

    with op.batch_alter_table(tabela) as batch:
        batch.drop_column("category")

    op.create_index(f"ix_{tabela}_category_id", tabela, ["category_id"])


def _converter_opening_balances(conn, ids: dict[str, int]) -> None:
    """Recria ``opening_balances`` com chave por ``category_id``."""
    if "opening_balances" not in _tabelas(conn):
        return
    colunas = _colunas(conn, "opening_balances")
    if "category" not in colunas:
        return

    antigos = conn.execute(
        sa.text("SELECT category, amount_cents, note FROM opening_balances")
    ).all()

    op.drop_table("opening_balances")
    op.create_table(
        "opening_balances",
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(300), nullable=True),
    )

    for nome, valor, nota in antigos:
        slug = SLUG_POR_NOME_ANTIGO.get(str(nome))
        if slug is None or slug not in ids:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO opening_balances (category_id, amount_cents, note) "
                "VALUES (:c, :v, :n)"
            ),
            {"c": ids[slug], "v": int(valor or 0), "n": nota},
        )


# --------------------------------------------------------------------------
# upgrade / downgrade
# --------------------------------------------------------------------------
def upgrade() -> None:
    """Aplica o modelo de categorias versionadas."""
    conn = op.get_bind()
    banco_novo = "incomes" not in _tabelas(conn)

    if banco_novo:
        _criar_restantes(conn)
        ids = _semear_categorias(conn)
        _versoes_do_plano(conn, ids)
        return

    _criar_categorias(conn)
    ids = _semear_categorias(conn)
    _versoes_do_plano(conn, ids)
    _converter_coluna_categoria(conn, "expenses", ids)
    _converter_coluna_categoria(conn, "allocation_states", ids)
    _converter_opening_balances(conn, ids)

    if "settings_versions" in _tabelas(conn):
        op.drop_table("settings_versions")

    if "import_log" not in _tabelas(conn):
        _criar_restantes(conn)


def downgrade() -> None:
    """Não há volta automática: restaure o backup criado antes da migração."""
    raise NotImplementedError(
        "Reverter esta migração perderia o versionamento de categorias. "
        "Restaure o backup gerado automaticamente em data/backups."
    )
