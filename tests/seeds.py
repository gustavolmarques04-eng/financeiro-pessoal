"""Categorias de exemplo usadas pelos testes.

Estes nomes ("Reserva", "Viagem", "Livre"...) vivem aqui, e **não** no
código do aplicativo, porque no aplicativo eles não existem: cada usuário
cria as categorias dele no primeiro acesso, e nenhuma regra de negócio
reconhece nome nenhum.

Os testes precisam de um cenário estável para conferir números, e é só
para isso que esta lista serve. Um teste de arquitetura garante que
nenhum destes nomes volte para ``core/``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    Category,
    CategoryBehavior,
    CategoryVersion,
    ClosingField,
    OpeningBalance,
)
from core.repositories import set_opening_balance


#: Categorias criadas na primeira execução. Os *slugs* são estáveis; os
#: nomes são apenas o rótulo inicial e podem ser trocados pelo usuário.
SEED_CATEGORIES: tuple[dict[str, object], ...] = (
    {
        "slug": "independencia",
        "name": "Independência financeira",
        "emoji": "📈",
        "behavior": CategoryBehavior.ALLOCATION_LONG_TERM,
        "percent_bp": 4900,
        "display_order": 1,
        "counts_as_investment_capital": True,
        "include_in_net_worth": True,
        "balance_from_closing": ClosingField.INVESTIMENTOS,
    },
    {
        "slug": "reserva",
        "name": "Reserva de emergência",
        "emoji": "🛟",
        "behavior": CategoryBehavior.ALLOCATION_GOAL,
        "percent_bp": 1700,
        "display_order": 2,
        "target_amount_cents": 600_000,
        "overflow_target_slug": "independencia",
        "include_in_net_worth": True,
        "balance_from_closing": ClosingField.RESERVA,
    },
    {
        "slug": "viagem",
        "name": "Viagem",
        "emoji": "✈️",
        "behavior": CategoryBehavior.ACCUMULATING_ENVELOPE,
        "percent_bp": 1400,
        "display_order": 3,
        "include_in_net_worth": True,
    },
    {
        "slug": "compras",
        "name": "Compras pessoais",
        "emoji": "🛍️",
        "behavior": CategoryBehavior.ACCUMULATING_ENVELOPE,
        "percent_bp": 900,
        "display_order": 4,
        "include_in_net_worth": True,
        "opening_balance_cents": 554,
    },
    {
        "slug": "namorada",
        "name": "Namorada",
        "emoji": "💕",
        "behavior": CategoryBehavior.MONTHLY_SPENDING,
        "percent_bp": 700,
        "display_order": 5,
    },
    {
        "slug": "amigos",
        "name": "Amigos",
        "emoji": "🍻",
        "behavior": CategoryBehavior.MONTHLY_SPENDING,
        "percent_bp": 300,
        "display_order": 6,
    },
    {
        "slug": "livre",
        "name": "Livre",
        "emoji": "🎲",
        "behavior": CategoryBehavior.MONTHLY_SPENDING,
        "percent_bp": 100,
        "display_order": 7,
    },
    {
        # Destino dos gastos que não pertencem a nenhum orçamento.
        # Não recebe percentual, então fica fora da conta dos 100%.
        "slug": "outro",
        "name": "Outro",
        "emoji": "📦",
        "behavior": CategoryBehavior.TRACKING_ONLY,
        "percent_bp": 0,
        "display_order": 99,
    },
)

#: Mês em que as categorias-semente passam a valer (bem antes de qualquer dado).
SEED_EFFECTIVE_MONTH = date(2000, 1, 1)

#: Categoria usada para gastos que não se encaixam em nenhuma outra.
FALLBACK_CATEGORY_SLUG = "outro"


def seed_categories(session: Session) -> None:
    """Cria as categorias de exemplo, se ainda não existirem."""
    if session.scalar(select(func.count()).select_from(Category)):
        return

    criadas: dict[str, Category] = {}
    for dados in SEED_CATEGORIES:
        categoria = Category(slug=str(dados["slug"]))
        session.add(categoria)
        criadas[categoria.slug] = categoria
    session.flush()

    for dados in SEED_CATEGORIES:
        slug_overflow = dados.get("overflow_target_slug")
        session.add(
            CategoryVersion(
                category_id=criadas[str(dados["slug"])].id,
                effective_month=SEED_EFFECTIVE_MONTH,
                name=str(dados["name"]),
                emoji=dados.get("emoji"),  # type: ignore[arg-type]
                behavior=dados["behavior"],  # type: ignore[arg-type]
                percent_bp=int(dados["percent_bp"]),  # type: ignore[arg-type]
                display_order=int(dados["display_order"]),  # type: ignore[arg-type]
                active=True,
                target_amount_cents=dados.get("target_amount_cents"),  # type: ignore[arg-type]
                overflow_target_category_id=(
                    criadas[str(slug_overflow)].id if slug_overflow else None
                ),
                counts_as_investment_capital=bool(
                    dados.get("counts_as_investment_capital", False)
                ),
                include_in_net_worth=bool(dados.get("include_in_net_worth", False)),
                accumulates_balance=bool(
                    dados.get(
                        "accumulates_balance",
                        dados["behavior"].acumula_por_padrao,  # type: ignore[union-attr]
                    )
                ),
                balance_from_closing=dados.get("balance_from_closing"),  # type: ignore[arg-type]
            )
        )
        saldo = int(dados.get("opening_balance_cents", 0))  # type: ignore[arg-type]
        if saldo:
            session.add(
                OpeningBalance(
                    category_id=criadas[str(dados["slug"])].id,
                    amount_cents=saldo,
                    note="Saldo anterior ao uso do aplicativo",
                )
            )
    session.flush()


# --------------------------------------------------------------------------
# Resolução por mês
# --------------------------------------------------------------------------
