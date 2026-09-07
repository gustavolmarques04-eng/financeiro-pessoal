"""Importa uma vez os dados da planilha ``Plano_Financeiro_Pessoal_v2.xlsx``.

O script lê a planilha, mostra tudo que encontrou, aponta divergências e só
grava depois de confirmação. Rodando de novo, ele avisa que a importação já
foi feita e não duplica nada.

Uso::

    python import_excel.py                 # interativo
    python import_excel.py --sim           # sem perguntar
    python import_excel.py --fonte informado
    python import_excel.py --refazer       # apaga a marca e importa de novo
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
from sqlalchemy import select

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core import budget_service as budget  # noqa: E402
from core import repositories as repo  # noqa: E402
from core.database import init_db, session_scope  # noqa: E402
from core.models import (  # noqa: E402
    Category,
    ImportLog,
    IncomeType,
    PaymentMethod,
)
from core.utils import format_brl, month_label, month_start, to_cents  # noqa: E402

PLANILHA_PADRAO = RAIZ.parent / "Plano_Financeiro_Pessoal_v2.xlsx"
MARCA = "Plano_Financeiro_Pessoal_v2.xlsx"

#: Estado descrito pelo usuário, usado para comparar com o arquivo.
BASE_INFORMADA_CENTS = to_cents(2952.21)
SALDO_COMPRAS_INFORMADO_CENTS = to_cents(5.54)

CATEGORIAS_CONFIG = (
    Category.INDEPENDENCIA,
    Category.RESERVA,
    Category.VIAGEM,
    Category.COMPRAS,
    Category.NAMORADA,
    Category.AMIGOS,
    Category.LIVRE,
)

MAPA_TIPO = {
    "salário": IncomeType.SALARIO,
    "salario": IncomeType.SALARIO,
    "va/vr": IncomeType.VA_VR,
    "renda extra": IncomeType.RENDA_EXTRA,
    "saldo inicial": IncomeType.SALDO_INICIAL,
    "outro": IncomeType.OUTRO,
}

MAPA_CATEGORIA = {c.value.lower(): c for c in Category}
MAPA_CATEGORIA["reserva"] = Category.RESERVA
MAPA_CATEGORIA["independência"] = Category.INDEPENDENCIA

MAPA_MEIO = {m.value.lower(): m for m in PaymentMethod}


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------
@dataclass
class ReceitaLida:
    """Uma linha da aba Receitas."""

    data: date
    descricao: str
    tipo: IncomeType
    valor_cents: int
    no_orcamento: bool
    obs: str | None


@dataclass
class GastoLido:
    """Uma linha da aba Gastos."""

    data: date
    descricao: str
    categoria: Category
    valor_cents: int
    meio: PaymentMethod
    obs: str | None


@dataclass
class Leitura:
    """Tudo que foi encontrado na planilha."""

    mes: date
    meta_reserva_cents: int
    percentuais_bp: dict[Category, int]
    receitas: list[ReceitaLida] = field(default_factory=list)
    gastos: list[GastoLido] = field(default_factory=list)
    separacoes: dict[Category, int] = field(default_factory=dict)
    reserva_cents: int = 0
    investimentos_cents: int = 0
    dividendos_cents: int = 0

    @property
    def recebido_cents(self) -> int:
        """Soma das receitas que não são saldo inicial."""
        return sum(r.valor_cents for r in self.receitas if r.tipo is not IncomeType.SALDO_INICIAL)

    @property
    def base_cents(self) -> int:
        """Soma das receitas marcadas como participantes do orçamento."""
        return sum(r.valor_cents for r in self.receitas if r.no_orcamento)


def _cents(valor: object) -> int:
    """Converte uma célula numérica em centavos, tolerando vazio."""
    if valor in (None, ""):
        return 0
    return to_cents(Decimal(str(valor)))


def _data(valor: object, padrao: date) -> date:
    """Converte uma célula de data, caindo no padrão quando vazia."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return padrao


def ler_planilha(caminho: Path) -> Leitura:
    """Lê a planilha simplificada de 4 abas e devolve o que encontrou."""
    wb = openpyxl.load_workbook(caminho, data_only=True)
    dash, cfg = wb["Dashboard"], wb["Config"]

    mes = month_start(_data(dash["A4"].value, date.today()))

    percentuais = {}
    for indice, categoria in enumerate(CATEGORIAS_CONFIG):
        bruto = cfg.cell(6 + indice, 2).value or 0
        percentuais[categoria] = int(round(float(bruto) * 10_000))

    leitura = Leitura(
        mes=mes,
        meta_reserva_cents=_cents(cfg["B3"].value),
        percentuais_bp=percentuais,
        reserva_cents=_cents(dash["B20"].value),
        investimentos_cents=_cents(dash["B21"].value),
        dividendos_cents=_cents(dash["B22"].value),
    )

    receitas = wb["Receitas"]
    for linha in range(2, receitas.max_row + 1):
        descricao = receitas.cell(linha, 2).value
        valor = receitas.cell(linha, 4).value
        if not descricao or valor in (None, ""):
            continue
        tipo_txt = str(receitas.cell(linha, 3).value or "Outro").strip().lower()
        orcamento_txt = str(receitas.cell(linha, 5).value or "Sim").strip().lower()
        leitura.receitas.append(
            ReceitaLida(
                data=_data(receitas.cell(linha, 1).value, mes),
                descricao=str(descricao).strip(),
                tipo=MAPA_TIPO.get(tipo_txt, IncomeType.OUTRO),
                valor_cents=_cents(valor),
                no_orcamento=not orcamento_txt.startswith("n"),
                obs=(str(receitas.cell(linha, 6).value).strip()
                     if receitas.cell(linha, 6).value else None),
            )
        )

    gastos = wb["Gastos"]
    for linha in range(2, gastos.max_row + 1):
        descricao = gastos.cell(linha, 2).value
        valor = gastos.cell(linha, 4).value
        if not descricao or valor in (None, ""):
            continue
        cat_txt = str(gastos.cell(linha, 3).value or "Outro").strip().lower()
        meio_txt = str(gastos.cell(linha, 5).value or "Outro").strip().lower()
        leitura.gastos.append(
            GastoLido(
                data=_data(gastos.cell(linha, 1).value, mes),
                descricao=str(descricao).strip(),
                categoria=MAPA_CATEGORIA.get(cat_txt, Category.OUTRO),
                valor_cents=_cents(valor),
                meio=MAPA_MEIO.get(meio_txt, PaymentMethod.OUTRO),
                obs=(str(gastos.cell(linha, 6).value).strip()
                     if gastos.cell(linha, 6).value else None),
            )
        )

    # Separações confirmadas no dashboard (linhas 8 a 11, coluna "Feito?").
    for indice, categoria in enumerate(
        (Category.INDEPENDENCIA, Category.RESERVA, Category.VIAGEM, Category.COMPRAS)
    ):
        linha = 8 + indice
        marcado = "feito" in str(dash.cell(linha, 4).value or "").lower()
        if marcado:
            leitura.separacoes[categoria] = _cents(dash.cell(linha, 3).value)

    return leitura


# --------------------------------------------------------------------------
# Apresentação
# --------------------------------------------------------------------------
def mostrar(leitura: Leitura) -> bool:
    """Imprime o que foi lido e devolve ``True`` se há divergência na base."""
    print("=" * 68)
    print(f"PLANILHA LIDA — mês {month_label(leitura.mes)}")
    print("=" * 68)

    print("\nRECEITAS")
    for r in leitura.receitas:
        marca = "no rateio" if r.no_orcamento else "FORA do rateio"
        print(f"  {r.data:%d/%m/%Y}  {r.descricao:<26} {r.tipo.value:<14} "
              f"{format_brl(r.valor_cents):>13}  ({marca})")
    print(f"  {'Recebido no mês':<40} {format_brl(leitura.recebido_cents):>13}")
    print(f"  {'Base de distribuição':<40} {format_brl(leitura.base_cents):>13}")

    print("\nGASTOS")
    if not leitura.gastos:
        print("  (nenhum)")
    for g in leitura.gastos:
        print(f"  {g.data:%d/%m/%Y}  {g.descricao:<26} {g.categoria.value:<20} "
              f"{format_brl(g.valor_cents):>13}")

    print("\nSEPARAÇÕES MARCADAS COMO FEITAS")
    if not leitura.separacoes:
        print("  (nenhuma)")
    for categoria, valor in leitura.separacoes.items():
        print(f"  {categoria.value:<28} {format_brl(valor):>13}")

    print("\nFECHAMENTO")
    print(f"  {'Reserva de emergência':<28} {format_brl(leitura.reserva_cents):>13}")
    print(f"  {'Investimentos':<28} {format_brl(leitura.investimentos_cents):>13}")
    print(f"  {'Dividendos':<28} {format_brl(leitura.dividendos_cents):>13}")

    print("\nCONFIGURAÇÃO")
    print(f"  {'Meta da reserva':<28} {format_brl(leitura.meta_reserva_cents):>13}")
    total_bp = sum(leitura.percentuais_bp.values())
    for categoria, bp in leitura.percentuais_bp.items():
        print(f"  {categoria.value:<28} {bp / 100:>12.2f}%")
    print(f"  {'TOTAL':<28} {total_bp / 100:>12.2f}%")

    divergente = leitura.base_cents != BASE_INFORMADA_CENTS
    if divergente:
        print("\n" + "!" * 68)
        print("DIVERGÊNCIA entre a planilha e os valores que você informou")
        print("!" * 68)
        print(f"  Base de distribuição na planilha : {format_brl(leitura.base_cents)}")
        print(f"  Base que você informou           : {format_brl(BASE_INFORMADA_CENTS)}")
        print(f"  Diferença                        : "
              f"{format_brl(leitura.base_cents - BASE_INFORMADA_CENTS)}")
        print()
        print("  Causa: na planilha, 'Saldo anterior Nubank' (R$ 5,54) está marcado")
        print("  como entrando no rateio. No seu enunciado, esses R$ 5,54 ficam de")
        print("  fora do rateio e viram saldo inicial do envelope Compras pessoais.")
        print()
        print("  Escolha a fonte:")
        print("    [arquivo]   usa a planilha como está (base "
              f"{format_brl(leitura.base_cents)})")
        print("    [informado] usa o que você descreveu (base "
              f"{format_brl(BASE_INFORMADA_CENTS)}, envelope Compras R$ 5,54)")
    return divergente


def aplicar_fonte_informada(leitura: Leitura) -> Leitura:
    """Ajusta a leitura para o estado descrito pelo usuário.

    Tira o saldo do Nubank do rateio e recalcula as separações a partir da
    base resultante, para que os valores fiquem coerentes entre si.
    """
    for receita in leitura.receitas:
        if "nubank" in receita.descricao.lower():
            receita.no_orcamento = False

    if leitura.separacoes:
        plano = budget.distribuir(leitura.base_cents, leitura.percentuais_bp)
        for categoria in list(leitura.separacoes):
            leitura.separacoes[categoria] = plano.get(categoria, 0)
    return leitura


# --------------------------------------------------------------------------
# Gravação
# --------------------------------------------------------------------------
def ja_importado() -> bool:
    """Se a planilha já foi importada alguma vez."""
    with session_scope() as session:
        return session.scalar(select(ImportLog).where(ImportLog.source == MARCA)) is not None


def limpar_marca() -> None:
    """Remove a marca de importação (para reimportar de propósito)."""
    with session_scope() as session:
        registro = session.scalar(select(ImportLog).where(ImportLog.source == MARCA))
        if registro:
            session.delete(registro)


def importar(leitura: Leitura, *, saldo_compras_cents: int) -> None:
    """Grava tudo em uma única transação."""
    with session_scope() as session:
        repo.upsert_settings_version(
            session,
            effective_month=leitura.mes,
            meta_reserva_cents=leitura.meta_reserva_cents,
            percentuais_bp=leitura.percentuais_bp,
        )

        for r in leitura.receitas:
            repo.create_income(
                session,
                on=r.data,
                description=r.descricao,
                type_=r.tipo,
                amount_cents=r.valor_cents,
                counts_in_budget=r.no_orcamento,
                note=r.obs,
            )

        for g in leitura.gastos:
            repo.create_expense(
                session,
                purchase_date=g.data,
                description=g.descricao,
                category=g.categoria,
                total_cents=g.valor_cents,
                payment_method=g.meio,
                note=g.obs,
            )

        revisao = repo.get_revision(session, leitura.mes)
        for categoria, valor in leitura.separacoes.items():
            repo.set_allocation(
                session, leitura.mes, categoria, separated_cents=valor, revision=revisao
            )

        if any(
            (leitura.reserva_cents, leitura.investimentos_cents, leitura.dividendos_cents)
        ):
            repo.upsert_closing(
                session,
                leitura.mes,
                reserva_cents=leitura.reserva_cents,
                investimentos_cents=leitura.investimentos_cents,
                dividendos_cents=leitura.dividendos_cents,
                note="Importado da planilha",
            )

        repo.set_opening_balance(
            session,
            Category.COMPRAS,
            saldo_compras_cents,
            note="Saldo trazido da planilha",
        )

        session.add(
            ImportLog(
                source=MARCA,
                detail=(
                    f"mês {month_label(leitura.mes)}; "
                    f"{len(leitura.receitas)} receitas; {len(leitura.gastos)} gastos; "
                    f"base {format_brl(leitura.base_cents)}"
                ),
            )
        )


def _preparar_console() -> None:
    """Garante que acentos e símbolos apareçam mesmo em console cp1252."""
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            reconfigurar(encoding="utf-8", errors="replace")


def main() -> int:
    """Ponto de entrada do script."""
    _preparar_console()
    parser = argparse.ArgumentParser(description="Importa a planilha para o banco.")
    parser.add_argument("--arquivo", type=Path, default=PLANILHA_PADRAO)
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação")
    parser.add_argument(
        "--fonte",
        choices=("arquivo", "informado"),
        default=None,
        help="qual versão usar quando houver divergência",
    )
    parser.add_argument("--refazer", action="store_true", help="permite importar de novo")
    args = parser.parse_args()

    if not args.arquivo.exists():
        print(f"Planilha não encontrada: {args.arquivo}")
        return 1

    init_db()

    if args.refazer:
        limpar_marca()
    if ja_importado():
        print("Esta planilha já foi importada. Nada foi alterado.")
        print("Use --refazer se quiser importar de novo (pode duplicar dados).")
        return 0

    leitura = ler_planilha(args.arquivo)
    divergente = mostrar(leitura)

    fonte = args.fonte
    if divergente and fonte is None:
        if args.sim:
            fonte = "arquivo"
            print("\n--sim sem --fonte: usando a planilha como está.")
        else:
            resposta = input("\nFonte [arquivo/informado] (padrão arquivo): ").strip().lower()
            fonte = "informado" if resposta.startswith("i") else "arquivo"

    saldo_compras = 0
    if fonte == "informado":
        leitura = aplicar_fonte_informada(leitura)
        saldo_compras = SALDO_COMPRAS_INFORMADO_CENTS
        print("\nAjustado para o estado informado:")
        print(f"  Base de distribuição   : {format_brl(leitura.base_cents)}")
        print(f"  Envelope Compras inicia: {format_brl(saldo_compras)}")
        for categoria, valor in leitura.separacoes.items():
            print(f"  Separado {categoria.value:<26}: {format_brl(valor)}")

    if not args.sim:
        confirmacao = input("\nGravar estes dados no banco? [s/N]: ").strip().lower()
        if not confirmacao.startswith("s"):
            print("Importação cancelada. Nada foi gravado.")
            return 0

    importar(leitura, saldo_compras_cents=saldo_compras)
    print("\n✔ Importação concluída.")

    with session_scope() as session:
        plano = budget.get_month_plan(session, leitura.mes)
        print(f"\nResumo de {month_label(plano.month)}:")
        print(f"  Recebido no mês        : {format_brl(plano.recebido_cents)}")
        print(f"  Base de distribuição   : {format_brl(plano.base_cents)}")
        for linha in plano.separacoes:
            print(f"  {linha.categoria.value:<28} planejado "
                  f"{format_brl(linha.planejado_cents):>13} · separado "
                  f"{format_brl(linha.separado_cents):>13} · {linha.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
