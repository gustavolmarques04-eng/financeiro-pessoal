"""Backup e restauração dos dados.

Duas saídas: cópia do arquivo SQLite (fiel, para restaurar) e exportação em
JSON (legível, para inspecionar ou migrar). A restauração sempre cria antes
uma cópia do banco atual, para que um arquivo errado não apague nada.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .database import DATA_DIR, DEFAULT_DB_PATH, database_url, init_db, reset_engine
from .models import (
    AllocationState,
    Category,
    CategoryVersion,
    Expense,
    ExpenseInstallment,
    Income,
    MonthlyClosing,
    MonthRevision,
    OpeningBalance,
)
from .database import session_scope

BACKUP_DIR = DATA_DIR / "backups"

#: Tabelas exportadas no JSON, na ordem em que devem ser lidas.
TABELAS_EXPORT = (
    ("categories", Category),
    ("category_versions", CategoryVersion),
    ("incomes", Income),
    ("expenses", Expense),
    ("expense_installments", ExpenseInstallment),
    ("allocation_states", AllocationState),
    ("monthly_closings", MonthlyClosing),
    ("opening_balances", OpeningBalance),
    ("month_revisions", MonthRevision),
)


def is_sqlite() -> bool:
    """Se o banco em uso é SQLite (só nele faz sentido copiar o arquivo)."""
    return database_url().startswith("sqlite")


def sqlite_path() -> Path:
    """Caminho do arquivo SQLite em uso."""
    url = database_url()
    if not url.startswith("sqlite"):
        raise RuntimeError("O banco atual não é SQLite.")
    caminho = url.split("///", 1)[-1]
    return Path(caminho) if caminho else DEFAULT_DB_PATH


def _serializar(valor: Any) -> Any:
    """Converte tipos do banco para algo que o JSON aceite."""
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if hasattr(valor, "value"):
        return valor.value
    return valor


def exportar_json() -> str:
    """Exporta todo o banco como JSON indentado."""
    payload: dict[str, Any] = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "schema": 1,
        "tables": {},
    }
    with session_scope() as session:
        for nome, modelo in TABELAS_EXPORT:
            linhas = []
            for obj in session.scalars(select(modelo)):
                linhas.append(
                    {
                        coluna.name: _serializar(getattr(obj, coluna.name))
                        for coluna in modelo.__table__.columns
                    }
                )
            payload["tables"][nome] = linhas
    return json.dumps(payload, ensure_ascii=False, indent=2)


def exportar_sqlite_bytes() -> bytes:
    """Bytes do arquivo SQLite atual, para download."""
    caminho = sqlite_path()
    if not caminho.exists():
        raise FileNotFoundError(f"Banco não encontrado em {caminho}")
    return caminho.read_bytes()


def criar_backup_local() -> Path:
    """Copia o banco atual para ``data/backups`` com data e hora no nome."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    origem = sqlite_path()
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = BACKUP_DIR / f"financeiro_{carimbo}.db"
    shutil.copy2(origem, destino)
    return destino


def restaurar_sqlite(conteudo: bytes) -> Path:
    """Substitui o banco atual pelo arquivo enviado.

    Antes de sobrescrever, guarda uma cópia do banco atual em
    ``data/backups`` e valida que o arquivo recebido é mesmo um SQLite com
    as tabelas esperadas.
    """
    if not conteudo.startswith(b"SQLite format 3\x00"):
        raise ValueError("O arquivo enviado não é um banco SQLite válido.")

    destino = sqlite_path()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    temporario = BACKUP_DIR / "_restore_candidato.db"
    temporario.write_bytes(conteudo)

    try:
        _validar_banco(temporario)
    except Exception:
        temporario.unlink(missing_ok=True)
        raise

    if destino.exists():
        criar_backup_local()

    reset_engine()
    shutil.move(str(temporario), str(destino))
    init_db(forcar=True)
    return destino


def _validar_banco(caminho: Path) -> None:
    """Confere que o arquivo tem as tabelas essenciais antes de restaurar."""
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{caminho.as_posix()}")
    try:
        tabelas = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    obrigatorias = {
        "incomes",
        "expenses",
        "expense_installments",
        "categories",
        "category_versions",
    }
    faltando = obrigatorias - tabelas
    if faltando:
        raise ValueError(f"Backup incompleto. Faltam as tabelas: {', '.join(sorted(faltando))}")
