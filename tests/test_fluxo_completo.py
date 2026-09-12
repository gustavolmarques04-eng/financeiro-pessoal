"""Simulação ponta a ponta do fluxo descrito na especificação.

RECEITA → PLANO → SEPARAÇÃO → GASTOS → FECHAMENTO → PATRIMÔNIO → HISTÓRICO,
conferindo em cada passo que Home e Separações leem os mesmos números e que
nada do passado se mexe.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import investment_service as investimentos
from core import repositories as repo
from core.models import IncomeType
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, ano, mes

SLUGS_SEPARACAO = ("independencia", "reserva", "viagem", "compras")


def test_fluxo_da_especificacao(session: Session, receita, gasto, cats) -> None:
    """Percorre os oito passos do roteiro de consistência."""
    # ------------------------------------------------------------------
    # 1. Setembro: base de R$ 2.952,21
    # ------------------------------------------------------------------
    repo.set_opening_balance(session, cats["compras"], to_cents(5.54))
    receita(1775.94, descricao="Primeiro salário")
    receita(700.00, descricao="VA/VR", tipo=IncomeType.VA_VR)
    receita(476.27, descricao="Saldo anterior Rico", tipo=IncomeType.SALDO_INICIAL)

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.recebido_cents == to_cents(2475.94)
    assert plano.base_cents == to_cents(2952.21)
    assert plano.planejado[cats["independencia"]] == to_cents(1446.58)

    # ------------------------------------------------------------------
    # 2. Confirmar todas as separações
    # ------------------------------------------------------------------
    # Toda categoria ativa entra na separação agora, e não só as de poupança.
    for vista in cat.resolve_active(session, SETEMBRO):
        budget.confirmar_separacao(session, SETEMBRO, vista.id)

    resumo = budget.resumo_separacoes(session, SETEMBRO)
    assert resumo.tudo_feito is True and resumo.falta_cents == 0

    # ------------------------------------------------------------------
    # 3. Jantar em Namorada
    # ------------------------------------------------------------------
    plano = budget.get_month_plan(session, SETEMBRO)
    antes = next(
        linha for linha in plano.separacoes if linha.categoria.slug == "namorada"
    ).saldo_cents

    gasto(150.00, cats["namorada"], mes=SETEMBRO, descricao="Jantar")

    plano = budget.get_month_plan(session, SETEMBRO)
    namorada = next(
        linha for linha in plano.separacoes if linha.categoria.slug == "namorada"
    )
    assert namorada.saldo_cents == antes - to_cents(150)

    # ------------------------------------------------------------------
    # 4. Compra parcelada em Compras (3x de R$ 100)
    # ------------------------------------------------------------------
    gasto(300.00, cats["compras"], mes=SETEMBRO, parcelas=3, descricao="Tênis")
    assert repo.sum_expenses(session, mes(SETEMBRO), category_id=cats["compras"]) == (
        to_cents(100)
    )
    assert repo.future_installments(session, SETEMBRO) == [
        (OUTUBRO, to_cents(100)),
        (date(2026, 11, 1), to_cents(100)),
    ]
    # O envelope usa o que foi separado, não o planejado.
    saldo_compras = budget.saldo_categoria(
        session, cat.get_view(session, cats["compras"], SETEMBRO), SETEMBRO
    )
    assert saldo_compras == to_cents(5.54) + to_cents(265.70) - to_cents(100)

    # ------------------------------------------------------------------
    # 5. Entra renda extra de R$ 500
    # ------------------------------------------------------------------
    separado_antes = {
        slug: budget.get_month_plan(session, SETEMBRO)
        .separacao(cats[slug])
        .separado_cents
        for slug in SLUGS_SEPARACAO
    }

    receita(500.00, descricao="Serviço extra", tipo=IncomeType.RENDA_EXTRA)
    plano = budget.get_month_plan(session, SETEMBRO)

    assert plano.recebido_cents == to_cents(2975.94)
    assert plano.base_cents == to_cents(3452.21)
    assert plano.total_planejado_cents == plano.base_cents

    for slug in SLUGS_SEPARACAO:
        linha = plano.separacao(cats[slug])
        assert linha.separado_cents == separado_antes[slug], "o separado não some"
        assert linha.planejado_cents > separado_antes[slug]
        assert linha.feito is False
        assert linha.falta_cents == linha.planejado_cents - separado_antes[slug]

    # Independência: 49% de 3.452,21 = 1.691,58; já separados 1.446,58.
    assert plano.separacao(cats["independencia"]).falta_cents == to_cents(245.00)

    # O planejado das demais categorias também sobe.
    namorada = next(
        linha for linha in plano.separacoes if linha.categoria.slug == "namorada"
    )
    assert namorada.planejado_cents == to_cents(241.65)

    # Home e Separações leem o mesmo resumo.
    resumo = budget.resumo_separacoes(session, SETEMBRO)
    assert resumo.pendentes == resumo.total, "a renda nova deixou todas pendentes"
    assert resumo.falta_cents == plano.total_falta_separar_cents

    # ------------------------------------------------------------------
    # 6. Alterar a configuração a partir de outubro
    # ------------------------------------------------------------------
    setembro_antes = dict(budget.get_month_plan(session, SETEMBRO).planejado)
    revisao_setembro = repo.get_revision(session, SETEMBRO)

    receita(3000.00, mes=OUTUBRO, descricao="Salário outubro")
    cat.upsert_version(session, cats["independencia"], OUTUBRO, percent_bp=5500)
    cat.upsert_version(session, cats["reserva"], OUTUBRO, percent_bp=1100)
    afetados = repo.bump_revisions_from(session, OUTUBRO)

    assert SETEMBRO not in afetados
    assert repo.get_revision(session, SETEMBRO) == revisao_setembro
    assert budget.get_month_plan(session, SETEMBRO).planejado == setembro_antes

    outubro = budget.get_month_plan(session, OUTUBRO)
    assert outubro.planejado[cats["independencia"]] == to_cents(1650)
    cat.validar_total(cat.resolve_all(session, OUTUBRO))

    # ------------------------------------------------------------------
    # 7. Fechar setembro
    # ------------------------------------------------------------------
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=to_cents(1500),
        dividendos_cents=to_cents(40),
    )

    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    assert patrimonio.valor_de("reserva") == to_cents(1774.88)
    assert patrimonio.valor_de("independencia") == to_cents(1500)
    assert patrimonio.total_cents == (
        to_cents(1774.88)
        + to_cents(1500)
        + patrimonio.valor_de("viagem")
        + patrimonio.valor_de("compras")
    )

    resumo_inv = investimentos.get_resumo(session, mes(SETEMBRO))
    assert resumo_inv.capital_destinado_cents == to_cents(1446.58)
    assert resumo_inv.resultado_cents == to_cents(1500) - to_cents(1446.58)

    # ------------------------------------------------------------------
    # 8. Visão anual de 2026
    # ------------------------------------------------------------------
    anual = budget.get_resumo_periodo(session, ano(2026))

    assert anual.recebido_cents == to_cents(2975.94) + to_cents(3000)
    # Jantar (150) + as três parcelas do tênis, todas dentro de 2026 (300).
    assert anual.gasto_cents == to_cents(150) + to_cents(300)
    assert anual.dividendos_cents == to_cents(40)
    assert anual.patrimonio.posicao == SETEMBRO, "único fechamento do ano"
    assert anual.patrimonio.total_cents == budget.get_patrimonio(
        session, mes(SETEMBRO)
    ).total_cents

    receitas_por_mes = repo.incomes_by_month(session, ano(2026))
    assert receitas_por_mes[SETEMBRO] == to_cents(2975.94)
    assert receitas_por_mes[OUTUBRO] == to_cents(3000)
    assert sum(receitas_por_mes.values()) == anual.recebido_cents

    gastos_por_mes = repo.expenses_by_month(session, ano(2026))
    assert gastos_por_mes[SETEMBRO] == to_cents(250)
    assert gastos_por_mes[OUTUBRO] == to_cents(100)


def test_excluir_gasto_parcelado_limpa_todos_os_meses(
    session: Session, gasto, cats
) -> None:
    """Excluir a compra tira as parcelas de todos os meses e do ano."""
    compra = gasto(300.00, cats["compras"], mes=SETEMBRO, parcelas=3)
    assert repo.sum_expenses(session, ano(2026)) == to_cents(300)

    repo.delete_expense(session, compra.id)

    assert repo.sum_expenses(session, ano(2026)) == 0
    assert repo.future_installments(session, SETEMBRO) == []


def test_editar_receita_recalcula_os_dois_meses(session: Session, receita, cats) -> None:
    """Mover uma receita de mês invalida o plano da origem e do destino."""
    entrada = receita(1000.00, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])

    repo.update_income(
        session,
        entrada.id,
        on=OUTUBRO,
        description="movida",
        type_=IncomeType.SALARIO,
        amount_cents=to_cents(1000),
        counts_in_budget=True,
    )

    setembro = budget.get_month_plan(session, SETEMBRO)
    outubro = budget.get_month_plan(session, OUTUBRO)

    assert setembro.base_cents == 0
    assert outubro.base_cents == to_cents(1000)
    # O valor já separado em setembro continua registrado, mas sem plano
    # ele deixa de estar "feito".
    linha = setembro.separacao(cats["independencia"])
    assert linha.separado_cents == to_cents(490)
    assert linha.planejado_cents == 0
