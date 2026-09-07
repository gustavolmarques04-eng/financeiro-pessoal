"""Fixtures dos testes: banco temporário e atalhos de criação de dados."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.database import seed_defaults  # noqa: E402
from core.models import Base, Category, IncomeType, PaymentMethod  # noqa: E402
from core import repositories as repo  # noqa: E402

SETEMBRO = date(2026, 9, 1)
OUTUBRO = date(2026, 10, 1)
NOVEMBRO = date(2026, 11, 1)


@pytest.fixture()
def session(tmp_path: Path) -> Session:
    """Sessão ligada a um SQLite temporário, já com a configuração padrão."""
    caminho = tmp_path / "teste.db"
    engine = create_engine(f"sqlite:///{caminho.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    sessao = fabrica()
    seed_defaults(sessao)
    sessao.commit()

    try:
        yield sessao
    finally:
        sessao.close()
        engine.dispose()


@pytest.fixture()
def receita(session: Session):
    """Cria uma receita rapidamente. Valor em reais."""

    def _criar(
        valor: float,
        *,
        mes: date = SETEMBRO,
        tipo: IncomeType = IncomeType.SALARIO,
        no_orcamento: bool = True,
        descricao: str = "entrada",
    ):
        from core.utils import to_cents

        return repo.create_income(
            session,
            on=mes,
            description=descricao,
            type_=tipo,
            amount_cents=to_cents(valor),
            counts_in_budget=no_orcamento,
        )

    return _criar


@pytest.fixture()
def gasto(session: Session):
    """Cria um gasto rapidamente. Valor em reais."""

    def _criar(
        valor: float,
        categoria: Category,
        *,
        mes: date = SETEMBRO,
        parcelas: int = 1,
        primeira: date | None = None,
        descricao: str = "compra",
    ):
        from core.utils import to_cents

        return repo.create_expense(
            session,
            purchase_date=mes,
            description=descricao,
            category=categoria,
            total_cents=to_cents(valor),
            payment_method=PaymentMethod.PIX_DEBITO,
            installments_count=parcelas,
            first_installment_month=primeira or mes,
        )

    return _criar
