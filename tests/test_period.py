"""Testes da visão anual e do período estruturado."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import investment_service as investimentos
from core import repositories as repo
from core.period import Period, PeriodMode
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, ano, mes

DEZEMBRO = date(2026, 12, 1)


# --------------------------------------------------------------------------
# Período
# --------------------------------------------------------------------------
def test_periodo_mensal_cobre_um_mes() -> None:
    """O modo mensal tem exatamente um mês."""
    periodo = Period.of_month(SETEMBRO)
    assert periodo.mode is PeriodMode.MONTH
    assert periodo.months == [SETEMBRO]
    assert periodo.label == "setembro/2026"


def test_periodo_anual_cobre_doze_meses() -> None:
    """O modo anual tem os doze meses do ano."""
    periodo = Period.of_year(2026)
    assert len(periodo.months) == 12
    assert periodo.start == date(2026, 1, 1)
    assert periodo.end == date(2026, 12, 1)
    assert periodo.label == "2026"


def test_navegacao_preserva_o_modo() -> None:
    """Avançar e voltar anda de mês em mês ou de ano em ano."""
    assert Period.of_month(date(2026, 12, 1)).shift(1).month == date(2027, 1, 1)
    assert Period.of_month(date(2026, 1, 1)).shift(-1).month == date(2025, 12, 1)
    assert Period.of_year(2026).shift(1).year == 2027
    assert Period.of_year(2026).shift(-1).year == 2025


def test_troca_de_modo_mantem_a_ancora() -> None:
    """Ir de mensal para anual mantém o ano em foco."""
    assert Period.of_month(SETEMBRO).as_year().year == 2026
    assert Period.of_year(2026).as_month().month == date(2026, 1, 1)


# --------------------------------------------------------------------------
# 1 e 2 — agregação anual
# --------------------------------------------------------------------------
def test_visao_anual_soma_receitas(session: Session, receita) -> None:
    """Receita anual é a soma dos meses do ano."""
    receita(1000.00, mes=SETEMBRO)
    receita(2000.00, mes=OUTUBRO)
    receita(500.00, mes=date(2027, 1, 1))

    assert repo.sum_incomes(session, ano(2026), only_renda=True) == to_cents(3000)
    assert repo.sum_incomes(session, ano(2027), only_renda=True) == to_cents(500)
    assert repo.sum_incomes(session, mes(SETEMBRO), only_renda=True) == to_cents(1000)


def test_visao_anual_soma_gastos_e_parcelas(session: Session, gasto, cats) -> None:
    """Gasto anual soma as parcelas que caem naquele ano.

    Uma compra de 6x começando em setembro deixa 4 parcelas em 2026 e 2 em
    2027 — cada ano vê só o que lhe pertence.
    """
    gasto(600.00, cats["compras"], mes=SETEMBRO, parcelas=6)

    assert repo.sum_expenses(session, ano(2026)) == to_cents(400)
    assert repo.sum_expenses(session, ano(2027)) == to_cents(200)
    assert repo.sum_expenses(session, mes(SETEMBRO)) == to_cents(100)


def test_visao_anual_soma_dividendos(session: Session) -> None:
    """Dividendos anuais somam os fechamentos do ano."""
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=0, investimentos_cents=0,
        dividendos_cents=to_cents(80),
    )
    repo.upsert_closing(
        session, OUTUBRO, reserva_cents=0, investimentos_cents=0,
        dividendos_cents=to_cents(120),
    )

    assert investimentos.dividendos_do_periodo(session, ano(2026)) == to_cents(200)
    assert investimentos.dividendos_do_periodo(session, mes(SETEMBRO)) == to_cents(80)


# --------------------------------------------------------------------------
# 3 — patrimônio anual não soma meses
# --------------------------------------------------------------------------
def test_patrimonio_anual_usa_o_ultimo_fechamento_do_ano(
    session: Session, cats
) -> None:
    """Patrimônio do ano é uma foto, nunca a soma das fotos mensais."""
    repo.set_opening_balance(session, cats["compras"], 0)
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(1000),
        investimentos_cents=0, dividendos_cents=0,
    )
    repo.upsert_closing(
        session, OUTUBRO, reserva_cents=to_cents(1500),
        investimentos_cents=0, dividendos_cents=0,
    )

    anual = budget.get_patrimonio(session, ano(2026))

    assert anual.total_cents == to_cents(1500), "não pode somar 1000 + 1500"
    assert anual.posicao == OUTUBRO, "a posição é o mês do fechamento usado"


def test_patrimonio_anual_sem_dezembro_usa_o_mes_mais_recente(
    session: Session, cats
) -> None:
    """Sem fechamento em dezembro, vale a última posição informada do ano."""
    repo.set_opening_balance(session, cats["compras"], 0)
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(1774.88),
        investimentos_cents=0, dividendos_cents=0,
    )

    anual = budget.get_patrimonio(session, ano(2026))
    assert anual.posicao == SETEMBRO
    assert anual.total_cents == to_cents(1774.88)
    assert anual.informado is True


def test_patrimonio_anual_ignora_fechamento_de_outro_ano(
    session: Session, cats
) -> None:
    """O ano só enxerga fechamentos dele."""
    repo.set_opening_balance(session, cats["compras"], 0)
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(1000),
        investimentos_cents=0, dividendos_cents=0,
    )

    assert budget.get_patrimonio(session, ano(2025)).informado is False
    assert budget.get_patrimonio(session, ano(2026)).informado is True


def test_resumo_do_periodo_agrega_tudo(session: Session, receita, gasto, cats) -> None:
    """O resumo usado pelos cards responde nos dois modos."""
    repo.set_opening_balance(session, cats["compras"], 0)
    receita(1000.00, mes=SETEMBRO)
    receita(2000.00, mes=OUTUBRO)
    gasto(100.00, cats["namorada"], mes=SETEMBRO)
    repo.upsert_closing(
        session, OUTUBRO, reserva_cents=to_cents(500),
        investimentos_cents=0, dividendos_cents=to_cents(30),
    )

    anual = budget.get_resumo_periodo(session, ano(2026))
    assert anual.recebido_cents == to_cents(3000)
    assert anual.gasto_cents == to_cents(100)
    assert anual.dividendos_cents == to_cents(30)
    assert anual.patrimonio.total_cents == to_cents(500)

    mensal = budget.get_resumo_periodo(session, mes(SETEMBRO))
    assert mensal.recebido_cents == to_cents(1000)
    assert mensal.gasto_cents == to_cents(100)


def test_series_mensais_cobrem_o_ano_inteiro(session: Session, receita, gasto, cats) -> None:
    """As séries dos gráficos anuais têm doze pontos, com zeros nos meses vazios."""
    receita(1000.00, mes=SETEMBRO)
    gasto(100.00, cats["namorada"], mes=OUTUBRO)

    receitas = repo.incomes_by_month(session, ano(2026))
    gastos = repo.expenses_by_month(session, ano(2026))

    assert len(receitas) == 12 and len(gastos) == 12
    assert receitas[SETEMBRO] == to_cents(1000)
    assert receitas[date(2026, 1, 1)] == 0
    assert gastos[OUTUBRO] == to_cents(100)
    assert sum(receitas.values()) == to_cents(1000)


# --------------------------------------------------------------------------
# 19 — o anual é analítico
# --------------------------------------------------------------------------
def test_separacoes_anuais_sao_resumo_por_mes(session: Session, receita, cats) -> None:
    """No anual há um resumo por mês; confirmação continua mensal."""
    receita(2952.21, mes=SETEMBRO)
    receita(2952.21, mes=OUTUBRO)
    for slug in ("independencia", "reserva", "viagem", "compras"):
        budget.confirmar_separacao(session, SETEMBRO, cats[slug])

    resumos = [
        budget.resumo_separacoes(session, m) for m in ano(2026).months
    ]
    setembro = next(r for r in resumos if r.month == SETEMBRO)
    outubro = next(r for r in resumos if r.month == OUTUBRO)

    assert len(resumos) == 12
    assert setembro.tudo_feito is True
    assert outubro.tudo_feito is False
    assert outubro.pendentes == 4
    # Cada resumo é de um mês: não existe confirmação "do ano".
    assert all(r.month in ano(2026).months for r in resumos)


def test_mes_de_referencia_no_modo_anual(session: Session, receita, gasto, cats) -> None:
    """Blocos mensais no modo anual usam o mês mais recente com dados.

    Sem isso, o "quanto posso gastar" cairia em janeiro e apareceria zerado
    mesmo havendo orçamento em setembro.
    """
    assert budget.mes_de_referencia(session, mes(SETEMBRO)) == SETEMBRO

    receita(1000.00, mes=SETEMBRO)
    gasto(50.00, cats["namorada"], mes=OUTUBRO)
    assert budget.mes_de_referencia(session, ano(2026)) == OUTUBRO

    # Um ano sem nenhum dado cai em dezembro, não em janeiro.
    assert budget.mes_de_referencia(session, ano(2024)) == date(2024, 12, 1)


def test_orcamento_mensal_no_modo_anual_nao_fica_zerado(
    session: Session, receita, cats
) -> None:
    """O bloco de orçamento mostra números reais mesmo em visão anual."""
    receita(2952.21, mes=SETEMBRO)

    referencia = budget.mes_de_referencia(session, ano(2026))
    plano = budget.get_month_plan(session, referencia)
    namorada = next(g for g in plano.gastos if g.categoria.slug == "namorada")

    assert referencia == SETEMBRO
    assert namorada.orcamento_cents == to_cents(206.65)
