"""Copia os dados do SQLite local para o banco da nuvem (PostgreSQL).

Roda uma vez, na hora de publicar. Lê o banco local, cria o esquema no
destino pelas migrações do Alembic e copia tabela por tabela, na ordem das
dependências, dentro de uma única transação: ou vai tudo, ou não vai nada.

Uso::

    python migrar_para_nuvem.py --destino "postgresql+psycopg://..."
    python migrar_para_nuvem.py --destino "..." --sim        # sem perguntar
    python migrar_para_nuvem.py --destino "..." --conferir   # só compara

O banco de origem não é alterado em momento nenhum.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import Session

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.database import DEFAULT_DB_PATH  # noqa: E402
from core.models import (  # noqa: E402
    AllocationState,
    Category,
    CategoryVersion,
    Expense,
    ExpenseInstallment,
    ImportLog,
    Income,
    MonthlyClosing,
    MonthRevision,
    OpeningBalance,
)

#: Ordem de cópia: pais antes dos filhos, por causa das chaves estrangeiras.
TABELAS = (
    Category,
    CategoryVersion,
    Income,
    Expense,
    ExpenseInstallment,
    AllocationState,
    MonthlyClosing,
    OpeningBalance,
    MonthRevision,
    ImportLog,
)


def _preparar_console() -> None:
    """Garante acentos legíveis mesmo em console cp1252."""
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            reconfigurar(encoding="utf-8", errors="replace")


def contar(engine, modelo) -> int:
    """Quantas linhas a tabela tem."""
    with Session(engine) as sessao:
        return int(sessao.scalar(select(func.count()).select_from(modelo)) or 0)


def resumo(engine, titulo: str) -> dict[str, int]:
    """Imprime e devolve a contagem de linhas por tabela."""
    print(f"\n{titulo}")
    contagens = {}
    for modelo in TABELAS:
        total = contar(engine, modelo)
        contagens[modelo.__tablename__] = total
        print(f"  {modelo.__tablename__:24} {total:>6}")
    return contagens


def criar_esquema(destino_url: str) -> None:
    """Cria as tabelas no destino usando as migrações do Alembic."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.set_main_option("sqlalchemy.url", destino_url)
    command.upgrade(config, "head")


def copiar(origem_url: str, destino_url: str) -> dict[str, int]:
    """Copia todas as tabelas da origem para o destino."""
    origem = create_engine(origem_url)
    destino = create_engine(destino_url)

    copiadas: dict[str, int] = {}
    try:
        with Session(origem) as leitura, destino.begin() as escrita:
            for modelo in TABELAS:
                colunas = [c.name for c in modelo.__table__.columns]
                linhas = [
                    {coluna: getattr(obj, coluna) for coluna in colunas}
                    for obj in leitura.scalars(select(modelo))
                ]
                if linhas:
                    escrita.execute(insert(modelo.__table__), linhas)
                copiadas[modelo.__tablename__] = len(linhas)
                print(f"  {modelo.__tablename__:24} {len(linhas):>6} copiada(s)")
        return copiadas
    finally:
        origem.dispose()
        destino.dispose()


def limpar_destino(destino_url: str) -> None:
    """Apaga o conteúdo das tabelas do destino, respeitando as dependências."""
    destino = create_engine(destino_url)
    try:
        with destino.begin() as conexao:
            for modelo in reversed(TABELAS):
                conexao.execute(modelo.__table__.delete())
    finally:
        destino.dispose()


def main() -> int:
    """Ponto de entrada do script."""
    _preparar_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", required=True, help="URL do banco de destino")
    parser.add_argument(
        "--origem",
        default=f"sqlite:///{DEFAULT_DB_PATH.as_posix()}",
        help="URL do banco de origem (padrão: o SQLite local)",
    )
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação")
    parser.add_argument(
        "--conferir",
        action="store_true",
        help="apenas compara as contagens, sem copiar nada",
    )
    args = parser.parse_args()

    origem = create_engine(args.origem)
    try:
        contagens_origem = resumo(origem, f"ORIGEM  {args.origem}")
    finally:
        origem.dispose()

    print(f"\nDESTINO {args.destino.split('@')[-1]}")
    print("  preparando o esquema…")
    criar_esquema(args.destino)

    destino = create_engine(args.destino)
    try:
        contagens_destino = resumo(destino, "DESTINO (antes)")
    finally:
        destino.dispose()

    if args.conferir:
        iguais = contagens_origem == contagens_destino
        print("\nContagens iguais." if iguais else "\nContagens DIFERENTES.")
        return 0 if iguais else 1

    ocupado = {t: n for t, n in contagens_destino.items() if n}
    # As categorias-semente são criadas pelo próprio Alembic; elas não contam
    # como "dados do usuário", mas precisam sair antes da cópia.
    if ocupado:
        print(f"\nO destino já tem linhas em: {', '.join(ocupado)}")
        if not args.sim:
            resposta = input("Apagar o conteúdo do destino e copiar? [s/N]: ")
            if not resposta.strip().lower().startswith("s"):
                print("Cancelado. Nada foi alterado.")
                return 0
        limpar_destino(args.destino)
    elif not args.sim:
        resposta = input("\nCopiar os dados para o destino? [s/N]: ")
        if not resposta.strip().lower().startswith("s"):
            print("Cancelado. Nada foi alterado.")
            return 0

    print("\nCOPIANDO")
    copiadas = copiar(args.origem, args.destino)

    destino = create_engine(args.destino)
    try:
        contagens_finais = resumo(destino, "DESTINO (depois)")
    finally:
        destino.dispose()

    if contagens_finais == contagens_origem:
        print("\nOK: destino idêntico à origem. O banco local não foi alterado.")
        return 0

    print("\nATENÇÃO: as contagens não bateram.")
    for tabela in contagens_origem:
        se, ate = contagens_origem[tabela], contagens_finais.get(tabela, 0)
        if se != ate:
            print(f"  {tabela}: origem {se} · destino {ate} · copiadas {copiadas.get(tabela)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
