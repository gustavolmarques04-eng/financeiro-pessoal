"""Testes de gastos, parcelamento e envelopes acumulativos."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import repositories as repo
from core.models import ExpenseInstallment
from core.utils import split_installments, to_cents

from .conftest import NOVEMBRO, OUTUBRO, SETEMBRO, mes


# --------------------------------------------------------------------------
# 5 — parcelamento
# --------------------------------------------------------------------------
def test_parcelamento_de_cem_reais_em_tres() -> None:
    """R$ 100,00 em 3x vira 33,33 + 33,33 + 33,34."""
    parcelas = split_installments(to_cents(100), 3)
    assert parcelas == [3333, 3333, 3334]
    assert sum(parcelas) == to_cents(100)


def test_parcelamento_nunca_perde_centavos() -> None:
    """Para qualquer valor e quantidade, a soma fecha exatamente."""
    for total in [1, 7, 100, 999, 10_000, 60_000, 123_457]:
        for n in range(1, 13):
            parcelas = split_installments(total, n)
            assert sum(parcelas) == total
            assert len(parcelas) == n
            assert max(parcelas) - min(parcelas) <= 1


def test_compra_parcelada_espalha_pelos_meses(session: Session, gasto, cats) -> None:
    """R$ 600 em 6x a partir de setembro gera R$ 100 por mês."""
    compra = gasto(600.00, cats["compras"], mes=SETEMBRO, parcelas=6)

    meses = [(p.month, p.amount_cents) for p in compra.installments]
    assert meses == [
        (date(2026, 9, 1), 10_000),
        (date(2026, 10, 1), 10_000),
        (date(2026, 11, 1), 10_000),
        (date(2026, 12, 1), 10_000),
        (date(2027, 1, 1), 10_000),
        (date(2027, 2, 1), 10_000),
    ]
    assert repo.sum_expenses(session, mes(SETEMBRO)) == to_cents(100)
    assert repo.sum_expenses(session, mes(date(2027, 2, 1))) == to_cents(100)


def test_gasto_a_vista_cai_no_mes_da_compra(session: Session, gasto, cats) -> None:
    """Sem parcelamento, o gasto inteiro pertence ao mês da compra."""
    gasto(250.00, cats["namorada"], mes=SETEMBRO)
    assert repo.sum_expenses(session, mes(SETEMBRO)) == to_cents(250)
    assert repo.sum_expenses(session, mes(OUTUBRO)) == 0


def test_editar_compra_parcelada_recalcula_parcelas(
    session: Session, gasto, cats
) -> None:
    """Alterar valor e quantidade regenera as parcelas do zero."""
    compra = gasto(600.00, cats["compras"], mes=SETEMBRO, parcelas=6)

    repo.update_expense(
        session,
        compra.id,
        purchase_date=SETEMBRO,
        description="revisado",
        category_id=cats["compras"],
        total_cents=to_cents(100),
        payment_method=compra.payment_method,
        installments_count=3,
        first_installment_month=SETEMBRO,
    )

    atualizado = repo.get_expense(session, compra.id)
    assert [p.amount_cents for p in atualizado.installments] == [3333, 3333, 3334]
    assert repo.sum_expenses(session, mes(date(2027, 2, 1))) == 0


# --------------------------------------------------------------------------
# 6 — exclusão em cascata
# --------------------------------------------------------------------------
def test_excluir_compra_remove_todas_as_parcelas(
    session: Session, gasto, cats
) -> None:
    """Apagar a compra não deixa parcelas órfãs."""
    compra = gasto(600.00, cats["viagem"], mes=SETEMBRO, parcelas=6)
    assert session.scalars(select(ExpenseInstallment)).all()

    repo.delete_expense(session, compra.id)

    assert session.scalars(select(ExpenseInstallment)).all() == []
    assert repo.sum_expenses(session, mes(SETEMBRO)) == 0
    assert repo.sum_expenses(session, mes(OUTUBRO)) == 0


# --------------------------------------------------------------------------
# 8 — gastos afetam a categoria certa
# --------------------------------------------------------------------------
def test_gasto_reduz_apenas_a_sua_categoria(
    session: Session, receita, gasto, cats
) -> None:
    """Gasto em Namorada consome só o orçamento de Namorada."""
    receita(3000.00)
    gasto(150.00, cats["namorada"], mes=SETEMBRO)

    plano = budget.get_month_plan(session, SETEMBRO)
    namorada = next(g for g in plano.gastos if g.categoria.slug == "namorada")
    amigos = next(g for g in plano.gastos if g.categoria.slug == "amigos")

    assert namorada.gasto_cents == to_cents(150)
    assert namorada.disponivel_cents == namorada.orcamento_cents - to_cents(150)
    assert amigos.gasto_cents == 0
    assert amigos.disponivel_cents == amigos.orcamento_cents


def test_estouro_de_orcamento_fica_negativo(
    session: Session, receita, gasto, cats
) -> None:
    """Gastar mais que o orçamento deixa o disponível negativo."""
    receita(1000.00)
    gasto(200.00, cats["amigos"], mes=SETEMBRO)

    linha = next(
        g for g in budget.get_month_plan(session, SETEMBRO).gastos
        if g.categoria.slug == "amigos"
    )
    assert linha.orcamento_cents == to_cents(30)
    assert linha.disponivel_cents == to_cents(-170)
    assert linha.estourou is True


def test_categorias_mensais_levam_a_sobra_para_o_mes_seguinte(
    session: Session, receita, gasto, cats
) -> None:
    """O que não foi gasto continua sendo seu no mês seguinte.

    O dinheiro do orçamento de consumo fica na conta corrente: virar o mês
    não o faz desaparecer.
    """
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)
    gasto(50.00, cats["namorada"], mes=SETEMBRO)

    setembro = next(
        g for g in budget.get_month_plan(session, SETEMBRO).gastos
        if g.categoria.slug == "namorada"
    )
    outubro = next(
        g for g in budget.get_month_plan(session, OUTUBRO).gastos
        if g.categoria.slug == "namorada"
    )

    assert setembro.gasto_cents == to_cents(50)
    assert setembro.vem_de_antes_cents == 0, "não havia mês anterior"
    sobrou = setembro.disponivel_cents

    assert outubro.gasto_cents == 0
    assert outubro.vem_de_antes_cents == sobrou
    assert outubro.disponivel_cents == outubro.orcamento_cents + sobrou


def test_estouro_vira_saldo_negativo_no_mes_seguinte(
    session: Session, receita, gasto, cats
) -> None:
    """Gastar além do orçado deixa dívida, e ela também atravessa o mês."""
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)

    setembro_antes = next(
        g for g in budget.get_month_plan(session, SETEMBRO).gastos
        if g.categoria.slug == "namorada"
    )
    gasto(
        float(setembro_antes.orcamento_cents) / 100 + 30.0,
        cats["namorada"],
        mes=SETEMBRO,
    )

    outubro = next(
        g for g in budget.get_month_plan(session, OUTUBRO).gastos
        if g.categoria.slug == "namorada"
    )

    assert outubro.vem_de_antes_cents == to_cents(-30)
    assert outubro.disponivel_cents == outubro.orcamento_cents - to_cents(30)


# --------------------------------------------------------------------------
# 9 e 10 — envelopes acumulativos
# --------------------------------------------------------------------------
def test_envelope_de_compras_acumula_entre_meses(
    session: Session, receita, gasto, cats, saldo
) -> None:
    """Separou em setembro e outubro, gastou depois: o saldo acompanha."""
    repo.set_opening_balance(session, cats["compras"], to_cents(5.54))

    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])
    assert saldo("compras", SETEMBRO) == to_cents(5.54) + to_cents(265.70)

    receita(3000.00, mes=OUTUBRO)
    budget.confirmar_separacao(session, OUTUBRO, cats["compras"])
    saldo_outubro = saldo("compras", OUTUBRO)
    assert saldo_outubro == to_cents(5.54) + to_cents(265.70) + to_cents(270)

    gasto(500.00, cats["compras"], mes=OUTUBRO)
    assert saldo("compras", OUTUBRO) == saldo_outubro - to_cents(500)
    assert saldo("compras", SETEMBRO) == to_cents(5.54) + to_cents(265.70)


def test_envelope_usa_separado_e_nao_planejado(
    session: Session, receita, cats, saldo
) -> None:
    """Sem confirmar a separação, o envelope não cresce."""
    repo.set_opening_balance(session, cats["compras"], 0)
    receita(2952.21, mes=SETEMBRO)
    assert saldo("compras", SETEMBRO) == 0

    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])
    assert saldo("compras", SETEMBRO) == to_cents(265.70)


def test_envelope_de_viagem_acumula(
    session: Session, receita, gasto, cats, saldo
) -> None:
    """Viagem também não reinicia todo mês."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    receita(2952.21, mes=OUTUBRO)
    budget.confirmar_separacao(session, OUTUBRO, cats["viagem"])

    assert saldo("viagem", OUTUBRO) == to_cents(413.31) * 2

    gasto(1000.00, cats["viagem"], mes=NOVEMBRO)
    assert saldo("viagem", NOVEMBRO) == to_cents(413.31) * 2 - to_cents(1000)


def test_parcelas_futuras_listam_compromissos(session: Session, gasto, cats) -> None:
    """Parcelas depois do mês selecionado aparecem agrupadas por mês."""
    gasto(300.00, cats["compras"], mes=SETEMBRO, parcelas=3)

    futuras = repo.future_installments(session, SETEMBRO)
    assert futuras == [(OUTUBRO, to_cents(100)), (NOVEMBRO, to_cents(100))]


def test_envelopes_vem_do_comportamento_e_nao_do_nome(
    session: Session, receita, cats
) -> None:
    """A lista de envelopes sai do comportamento declarado."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])

    linhas = budget.envelopes(session, mes(SETEMBRO))
    slugs = {linha.categoria.slug for linha in linhas}

    assert slugs == {"viagem", "compras"}
    for linha in linhas:
        assert linha.categoria.accumulates
        assert not linha.categoria.is_monthly_budget, (
            "orçamento de consumo acumula, mas se mostra na seção do mês"
        )
