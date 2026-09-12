"""Apaga os dados financeiros das contas, deixando os dois zerados.

O que é apagado: receitas, gastos, parcelas, categorias, versões,
separações, transferências, ajustes, fechamentos, saldos iniciais e o
estado de onboarding.

O que **não** é tocado: o esquema, as migrações, o código e as contas do
Supabase Auth. Ninguém perde o login.

Uso::

    python scripts/reset_test_users.py

O script mostra o que vai apagar e exige que você digite a palavra de
confirmação. Não tem modo silencioso de propósito, e não é usado por
nenhum teste automatizado — os testes rodam em banco descartável.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine, text  # noqa: E402

from core import settings  # noqa: E402

CONFIRMACAO = "APAGAR"

#: Filhos antes dos pais, para nenhuma chave estrangeira reclamar.
ORDEM = (
    "balance_adjustments",
    "category_transfers",
    "expense_installments",
    "expenses",
    "allocation_states",
    "opening_balances",
    "monthly_closings",
    "month_revisions",
    "incomes",
    "import_log",
    "category_versions",
    "categories",
)


def _contagens(conexao) -> dict[str, int]:
    return {
        tabela: conexao.execute(
            text(f'SELECT count(*) FROM "{tabela}"')  # noqa: S608 - nome interno
        ).scalar_one()
        for tabela in ORDEM
    }


def main() -> int:
    url = settings.database_url()
    if not url:
        raise SystemExit("Sem DATABASE_URL configurada.")

    engine = create_engine(url, connect_args={"prepare_threshold": None})
    try:
        with engine.connect() as conexao:
            antes = _contagens(conexao)
            destino = url.split("@")[-1]

        print(f"BANCO   {destino}")
        print("\nSerá apagado:")
        total = 0
        for tabela, quantas in antes.items():
            if quantas:
                print(f"  {tabela:<24} {quantas:>5}")
                total += quantas
        if not total:
            print("  (nada — já está zerado)")
            return 0

        print(f"\n  total de linhas: {total}")
        print("\nAs contas do Supabase Auth e o esquema são preservados.")
        resposta = input(f'Digite "{CONFIRMACAO}" para confirmar: ')
        if resposta.strip() != CONFIRMACAO:
            print("Cancelado. Nada foi alterado.")
            return 1

        # Tudo ou nada: um erro no meio não pode deixar metade apagada.
        with engine.begin() as conexao:
            for tabela in ORDEM:
                conexao.execute(text(f'DELETE FROM "{tabela}"'))  # noqa: S608
            conexao.execute(
                text(
                    """
                    UPDATE profiles
                       SET onboarding_completed = false,
                           investment_category_id = NULL,
                           reserve_category_id = NULL,
                           investment_cost_basis_cents = NULL,
                           updated_at = now()
                    """
                )
            )

        with engine.connect() as conexao:
            depois = _contagens(conexao)
        sobrou = {t: n for t, n in depois.items() if n}
        if sobrou:
            print(f"\nATENÇÃO: sobraram linhas em {sobrou}")
            return 1

        print("\nZerado. Os dois usuários vão recomeçar pelo primeiro acesso.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
