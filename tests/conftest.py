"""Fixtures dos testes: banco temporário e atalhos de criação de dados."""

from __future__ import annotations

import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core import auth  # noqa: E402
from core import categories as cat  # noqa: E402
from core import repositories as repo  # noqa: E402
from .seeds import seed_categories  # noqa: E402
from core.models import Base, IncomeType, PaymentMethod  # noqa: E402
from core.period import Period  # noqa: E402
from core.utils import to_cents  # noqa: E402

SETEMBRO = date(2026, 9, 1)
OUTUBRO = date(2026, 10, 1)
NOVEMBRO = date(2026, 11, 1)


@pytest.fixture()
def usuario() -> auth.Usuario:
    """Dono descartável dos dados do teste.

    Um id novo a cada teste: nenhum teste enxerga o que outro gravou, e
    nenhum encosta nas contas reais.
    """
    return auth.Usuario(id=uuid.uuid4(), email="teste@financeiro.local", login="teste")


@pytest.fixture()
def outro_usuario() -> auth.Usuario:
    """Segundo dono, para os testes de isolamento."""
    return auth.Usuario(id=uuid.uuid4(), email="outro@financeiro.local", login="outro")


@pytest.fixture()
def session(tmp_path: Path, usuario: auth.Usuario) -> Session:
    """Sessão ligada a um SQLite temporário, já com as categorias iniciais."""
    auth.definir_atual(usuario)
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
    seed_categories(sessao)
    sessao.commit()

    try:
        yield sessao
    finally:
        sessao.close()
        engine.dispose()
        auth.definir_atual(None)


@pytest.fixture()
def session_vazia(tmp_path: Path, usuario: auth.Usuario) -> Session:
    """Sessão sem categoria nenhuma, como um usuário recém-criado.

    O aplicativo não semeia mais categorias: elas nascem no primeiro
    acesso. Esta fixture reproduz esse estado.
    """
    auth.definir_atual(usuario)
    caminho = tmp_path / "vazio.db"
    engine = create_engine(f"sqlite:///{caminho.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    sessao = fabrica()
    try:
        yield sessao
    finally:
        sessao.close()
        engine.dispose()
        auth.definir_atual(None)


@pytest.fixture()
def cats(session: Session) -> dict[str, int]:
    """Mapa ``slug -> category_id`` das categorias iniciais.

    Os testes usam o *slug* (identidade estável) e nunca o nome, do mesmo
    jeito que o código de produção.
    """
    return {v.slug: v.id for v in cat.resolve_all(session, SETEMBRO)}


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
    """Cria um gasto rapidamente. Valor em reais, categoria por id."""

    def _criar(
        valor: float,
        category_id: int,
        *,
        mes: date = SETEMBRO,
        parcelas: int = 1,
        primeira: date | None = None,
        descricao: str = "compra",
    ):
        return repo.create_expense(
            session,
            purchase_date=mes,
            description=descricao,
            category_id=category_id,
            total_cents=to_cents(valor),
            payment_method=PaymentMethod.PIX_DEBITO,
            installments_count=parcelas,
            first_installment_month=primeira or mes,
        )

    return _criar


@pytest.fixture()
def saldo(session: Session):
    """Saldo de uma categoria (pelo slug) no fim de um mês."""

    def _saldo(slug: str, mes: date = SETEMBRO) -> int:
        from core import budget_service as budget

        vista = next(v for v in cat.resolve_all(session, mes) if v.slug == slug)
        return budget.saldo_categoria(session, vista, mes)

    return _saldo


def mes(valor: date) -> Period:
    """Atalho: período mensal."""
    return Period.of_month(valor)


def ano(valor: int) -> Period:
    """Atalho: período anual."""
    return Period.of_year(valor)
