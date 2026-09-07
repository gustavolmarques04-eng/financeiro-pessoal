"""Testes de patrimônio, investimentos e consistência do fluxo completo."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core.models import Category
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO


# --------------------------------------------------------------------------
# 11 — patrimônio não duplica
# --------------------------------------------------------------------------
def test_patrimonio_soma_as_quatro_parcelas(session: Session, receita) -> None:
    """Patrimônio = reserva + investimentos + envelopes, sem repetir nada."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, Category.VIAGEM)
    budget.confirmar_separacao(session, SETEMBRO, Category.COMPRAS)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=to_cents(1000),
        dividendos_cents=0,
    )

    patrimonio = budget.get_patrimonio(session, SETEMBRO)

    assert patrimonio.reserva_cents == to_cents(1774.88)
    assert patrimonio.investimentos_cents == to_cents(1000)
    assert patrimonio.viagem_cents == to_cents(413.31)
    assert patrimonio.compras_cents == to_cents(265.70)
    assert patrimonio.total_cents == (
        to_cents(1774.88) + to_cents(1000) + to_cents(413.31) + to_cents(265.70)
    )


def test_independencia_nao_entra_duas_vezes_no_patrimonio(
    session: Session, receita
) -> None:
    """Separar para Independência não infla o patrimônio por si só.

    O dinheiro investido é representado pelo valor informado no fechamento;
    somar também as separações contaria o mesmo dinheiro duas vezes.
    """
    receita(2952.21, mes=SETEMBRO)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1000),
        investimentos_cents=to_cents(1000),
        dividendos_cents=0,
    )
    antes = budget.get_patrimonio(session, SETEMBRO).total_cents

    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    depois = budget.get_patrimonio(session, SETEMBRO).total_cents

    assert depois == antes


def test_patrimonio_sem_fechamento_nao_inventa_valor(session: Session) -> None:
    """Sem fechamento informado, reserva e investimentos ficam zerados."""
    patrimonio = budget.get_patrimonio(session, SETEMBRO)
    assert patrimonio.informado is False
    assert patrimonio.total_cents == 0


# --------------------------------------------------------------------------
# Investimentos e dividendos
# --------------------------------------------------------------------------
def test_capital_destinado_soma_separacoes_de_independencia(
    session: Session, receita
) -> None:
    """Capital destinado acumula as separações de Independência entre meses."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    receita(2952.21, mes=OUTUBRO)
    budget.confirmar_separacao(session, OUTUBRO, Category.INDEPENDENCIA)

    assert investimentos.capital_destinado(session, SETEMBRO) == to_cents(1446.58)
    assert investimentos.capital_destinado(session, OUTUBRO) == to_cents(1446.58) * 2


def test_resultado_dos_investimentos_pode_ser_negativo(
    session: Session, receita
) -> None:
    """Valor atual abaixo do capital destinado gera resultado negativo."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=to_cents(1300),
        dividendos_cents=0,
    )

    resumo = investimentos.get_resumo(session, SETEMBRO)
    assert resumo.capital_destinado_cents == to_cents(1446.58)
    assert resumo.resultado_cents == to_cents(1300) - to_cents(1446.58)
    assert resumo.resultado_cents < 0


def test_dividendos_ficam_fora_do_resultado(session: Session, receita) -> None:
    """Dividendos são informativos e não mexem no resultado estimado."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=to_cents(1500),
        dividendos_cents=to_cents(80),
    )

    resumo = investimentos.get_resumo(session, SETEMBRO)
    assert resumo.resultado_cents == to_cents(1500) - to_cents(1446.58)
    assert investimentos.dividendos_do_mes(session, SETEMBRO) == to_cents(80)
    assert investimentos.dividendos_acumulados(session, SETEMBRO) == to_cents(80)


def test_fechamento_guarda_o_historico_de_cada_mes(session: Session) -> None:
    """Cada mês preserva o que foi informado nele."""
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(1774.88),
        investimentos_cents=0, dividendos_cents=0,
    )
    repo.upsert_closing(
        session, OUTUBRO, reserva_cents=to_cents(2500),
        investimentos_cents=0, dividendos_cents=0,
    )

    assert repo.get_closing(session, SETEMBRO).reserva_cents == to_cents(1774.88)
    assert repo.get_closing(session, OUTUBRO).reserva_cents == to_cents(2500)
    assert budget.get_patrimonio(session, SETEMBRO).reserva_cents == to_cents(1774.88)


# --------------------------------------------------------------------------
# Fluxo completo: receita → plano → separação → gasto → fechamento
# --------------------------------------------------------------------------
def test_fluxo_completo_permanece_consistente(
    session: Session, receita, gasto
) -> None:
    """Percorre o ciclo inteiro conferindo que tudo bate ponta a ponta."""
    repo.set_opening_balance(session, Category.COMPRAS, to_cents(5.54))

    receita(1775.94, descricao="Primeiro salário")
    receita(700.00, descricao="VA/VR")
    from core.models import IncomeType

    receita(476.27, tipo=IncomeType.SALDO_INICIAL, descricao="Saldo anterior Rico")

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.recebido_cents == to_cents(2475.94)
    assert plano.base_cents == to_cents(2952.21)
    assert plano.total_planejado_cents == plano.base_cents

    for categoria in (
        Category.INDEPENDENCIA,
        Category.RESERVA,
        Category.VIAGEM,
        Category.COMPRAS,
    ):
        budget.confirmar_separacao(session, SETEMBRO, categoria)

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.total_falta_separar_cents == 0
    assert all(linha.feito for linha in plano.separacoes)

    gasto(150.00, Category.NAMORADA, mes=SETEMBRO)
    plano = budget.get_month_plan(session, SETEMBRO)
    namorada = next(g for g in plano.gastos if g.categoria is Category.NAMORADA)
    assert namorada.disponivel_cents == to_cents(206.65) - to_cents(150)
    assert plano.gasto_cents == to_cents(150)

    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=to_cents(1446.58),
        dividendos_cents=0,
    )
    patrimonio = budget.get_patrimonio(session, SETEMBRO)
    assert patrimonio.total_cents == (
        to_cents(1774.88) + to_cents(1446.58) + to_cents(413.31) + to_cents(265.70 + 5.54)
    )

    resumo = investimentos.get_resumo(session, SETEMBRO)
    assert resumo.capital_destinado_cents == to_cents(1446.58)
    assert resumo.resultado_cents == 0


def test_investimento_sem_valor_informado_nao_mostra_prejuizo(
    session: Session, receita
) -> None:
    """Separar para Independência sem informar a carteira não vira perda.

    Antes desta regra o painel mostrava -100%, o que assustava sem motivo.
    """
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=0,
        dividendos_cents=0,
    )

    resumo = investimentos.get_resumo(session, SETEMBRO)
    assert resumo.informado is False
    assert resumo.capital_destinado_cents == to_cents(1446.58)
    assert resumo.resultado_cents == 0
    assert resumo.rentabilidade_pct == 0.0
