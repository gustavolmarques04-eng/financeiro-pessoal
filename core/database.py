"""Conexão com o banco e criação do esquema.

Todo o acesso ao banco passa por aqui. Trocar SQLite por PostgreSQL/Supabase
é só apontar ``DATABASE_URL`` para a nova URL — nenhum outro arquivo muda.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Category, OpeningBalance, SettingsVersion

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "financeiro.db"

#: Percentuais aplicados na primeira execução (somam 100%).
DEFAULT_PERCENTUAIS_BP: dict[Category, int] = {
    Category.INDEPENDENCIA: 4900,
    Category.RESERVA: 1700,
    Category.VIAGEM: 1400,
    Category.COMPRAS: 900,
    Category.NAMORADA: 700,
    Category.AMIGOS: 300,
    Category.LIVRE: 100,
}
DEFAULT_META_RESERVA_CENTS = 600_000
DEFAULT_EFFECTIVE_MONTH = date(2000, 1, 1)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def database_url() -> str:
    """URL do banco: ``DATABASE_URL`` do ambiente ou o SQLite local."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


def _configure_sqlite(engine: Engine) -> None:
    """Liga chaves estrangeiras no SQLite (desligadas por padrão)."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Devolve o engine do processo, criando-o na primeira chamada."""
    global _engine, _SessionFactory
    if _engine is not None and url is None:
        return _engine

    resolved = url or database_url()
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if resolved.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(resolved, **kwargs)  # type: ignore[arg-type]
    if resolved.startswith("sqlite"):
        _configure_sqlite(engine)

    if url is None:
        _engine = engine
        _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_session_factory() -> sessionmaker[Session]:
    """Fábrica de sessões ligada ao engine padrão."""
    global _SessionFactory
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sessão transacional: commita no fim ou desfaz tudo em caso de erro.

    Usado por qualquer operação que toque em mais de uma tabela — é o que
    impede gravar metade de uma compra parcelada.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def seed_defaults(session: Session) -> None:
    """Garante configuração inicial e saldos de envelope zerados."""
    existe = session.scalar(select(SettingsVersion).limit(1))
    if existe is None:
        session.add(
            SettingsVersion(
                effective_month=DEFAULT_EFFECTIVE_MONTH,
                meta_reserva_cents=DEFAULT_META_RESERVA_CENTS,
                pct_independencia_bp=DEFAULT_PERCENTUAIS_BP[Category.INDEPENDENCIA],
                pct_reserva_bp=DEFAULT_PERCENTUAIS_BP[Category.RESERVA],
                pct_viagem_bp=DEFAULT_PERCENTUAIS_BP[Category.VIAGEM],
                pct_compras_bp=DEFAULT_PERCENTUAIS_BP[Category.COMPRAS],
                pct_namorada_bp=DEFAULT_PERCENTUAIS_BP[Category.NAMORADA],
                pct_amigos_bp=DEFAULT_PERCENTUAIS_BP[Category.AMIGOS],
                pct_livre_bp=DEFAULT_PERCENTUAIS_BP[Category.LIVRE],
            )
        )

    for categoria in (Category.VIAGEM, Category.COMPRAS):
        if session.get(OpeningBalance, categoria) is None:
            session.add(OpeningBalance(category=categoria, amount_cents=0))


def init_db(engine: Engine | None = None) -> Engine:
    """Cria as tabelas (se faltarem) e semeia os dados padrão."""
    eng = engine or get_engine()
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    with factory() as session:
        seed_defaults(session)
        session.commit()
    return eng


def reset_engine() -> None:
    """Descarta o engine em cache. Usado pelos testes e pela restauração."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
