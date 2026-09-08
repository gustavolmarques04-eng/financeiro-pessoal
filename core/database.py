"""Conexão com o banco e criação do esquema.

Todo o acesso ao banco passa por aqui. Trocar SQLite por PostgreSQL/Supabase
é só apontar ``DATABASE_URL`` para a nova URL — nenhum outro arquivo muda.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import settings
from .categories import seed_categories
from .models import Base

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "financeiro.db"

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def database_url() -> str:
    """URL do banco: ``DATABASE_URL`` configurada ou o SQLite local.

    Na nuvem a URL aponta para o PostgreSQL e vem dos segredos do Streamlit;
    no seu computador, sem configurar nada, cai no arquivo local.
    """
    url = settings.database_url()
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
    elif resolved.startswith("postgresql"):
        # Bancos em nuvem derrubam conexões ociosas, e um app que fica
        # aberto na aba do celular fica ocioso o tempo todo: testar a
        # conexão antes de usar evita o erro na primeira ação do dia.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
        # Em pooler de transação (PgBouncer) as prepared statements do
        # psycopg não sobrevivem entre requisições. Desligá-las faz a mesma
        # URL funcionar nos dois modos de pooling do Supabase.
        kwargs["connect_args"] = {"prepare_threshold": None}

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
    """Garante as categorias iniciais na primeira execução."""
    seed_categories(session)


def sqlite_file() -> Path | None:
    """Arquivo SQLite em uso, ou ``None`` quando o banco não é SQLite."""
    url = database_url()
    if not url.startswith("sqlite"):
        return None
    caminho = url.split("///", 1)[-1]
    return Path(caminho) if caminho else DEFAULT_DB_PATH


def _backup_antes_da_migracao() -> Path | None:
    """Copia o banco SQLite antes de aplicar migrações pendentes."""
    origem = sqlite_file()
    if origem is None or not origem.exists():
        return None
    destino_dir = DATA_DIR / "backups"
    destino_dir.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = destino_dir / f"pre_migracao_{carimbo}.db"
    shutil.copy2(origem, destino)
    return destino


def url_para_alembic(url: str) -> str:
    """Prepara a URL para o ``configparser`` usado pelo Alembic.

    O configparser interpreta ``%`` como início de interpolação. Uma senha
    percent-encoded (``%40`` para ``@``) faria a leitura da configuração
    explodir, derrubando o app logo no arranque — dobrar o sinal resolve.
    """
    return url.replace("%", "%%")


def run_migrations(engine: Engine | None = None) -> None:
    """Leva o banco até a última migração do Alembic.

    Faz um backup automático antes, quando há algo a aplicar. Nunca apaga e
    recria: o esquema é transformado no lugar.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    eng = engine or get_engine()
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url_para_alembic(database_url()))
    config.attributes["connection"] = None

    with eng.connect() as conexao:
        atual = MigrationContext.configure(conexao).get_current_revision()
    topo = ScriptDirectory.from_config(config).get_current_head()

    if atual == topo:
        return
    _backup_antes_da_migracao()
    command.upgrade(config, "head")


def init_db(engine: Engine | None = None) -> Engine:
    """Aplica as migrações pendentes e semeia os dados padrão."""
    eng = engine or get_engine()
    run_migrations(eng)
    Base.metadata.create_all(eng, checkfirst=True)
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
