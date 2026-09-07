"""Testes das regras de distribuição, reserva e separações."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import repositories as repo
from core.models import Category, IncomeType
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO

BASE_SETEMBRO = to_cents(2952.21)

ESPERADO_SETEMBRO = {
    Category.INDEPENDENCIA: to_cents(1446.58),
    Category.RESERVA: to_cents(501.88),
    Category.VIAGEM: to_cents(413.31),
    Category.COMPRAS: to_cents(265.70),
    Category.NAMORADA: to_cents(206.65),
    Category.AMIGOS: to_cents(88.57),
    Category.LIVRE: to_cents(29.52),
}

PESOS_PADRAO = {
    Category.INDEPENDENCIA: 4900,
    Category.RESERVA: 1700,
    Category.VIAGEM: 1400,
    Category.COMPRAS: 900,
    Category.NAMORADA: 700,
    Category.AMIGOS: 300,
    Category.LIVRE: 100,
}


# --------------------------------------------------------------------------
# 1 e 2 — rateio
# --------------------------------------------------------------------------
def test_percentuais_somam_cem_por_cento() -> None:
    """Os percentuais padrão somam exatamente 100%."""
    assert sum(PESOS_PADRAO.values()) == budget.TOTAL_BP


def test_distribuicao_nao_perde_nem_inventa_centavos() -> None:
    """A soma das fatias é sempre igual à base, para várias bases."""
    for base in [0, 1, 7, 99, 100_00, 295_221, 333_333, 1_000_001]:
        fatias = budget.distribuir(base, PESOS_PADRAO)
        assert sum(fatias.values()) == base, f"falhou para base {base}"


def test_distribuicao_da_base_de_setembro() -> None:
    """R$ 2.952,21 produz exatamente os valores esperados."""
    resultado = budget.distribuir(BASE_SETEMBRO, PESOS_PADRAO)
    assert resultado == ESPERADO_SETEMBRO
    assert sum(resultado.values()) == BASE_SETEMBRO


def test_plano_do_mes_usa_base_e_nao_recebido(session: Session, receita) -> None:
    """Saldo inicial entra na base do rateio mas não conta como renda."""
    receita(1775.94, tipo=IncomeType.SALARIO, descricao="Primeiro salário")
    receita(700.00, tipo=IncomeType.VA_VR, descricao="VA/VR")
    receita(476.27, tipo=IncomeType.SALDO_INICIAL, descricao="Saldo anterior Rico")

    plano = budget.get_month_plan(session, SETEMBRO)

    assert plano.recebido_cents == to_cents(2475.94)
    assert plano.base_cents == BASE_SETEMBRO
    assert plano.planejado == ESPERADO_SETEMBRO
    assert plano.total_planejado_cents == plano.base_cents


# --------------------------------------------------------------------------
# 3 — regra da reserva
# --------------------------------------------------------------------------
def test_sobra_da_reserva_vai_para_independencia() -> None:
    """Faltando R$ 100 para a meta, os outros R$ 200 vão para Independência."""
    planejado = {
        Category.INDEPENDENCIA: to_cents(1000),
        Category.RESERVA: to_cents(300),
    }
    ajustado, sobra = budget.aplicar_regra_reserva(
        planejado, reserva_atual_cents=to_cents(5900), meta_cents=to_cents(6000)
    )

    assert ajustado[Category.RESERVA] == to_cents(100)
    assert ajustado[Category.INDEPENDENCIA] == to_cents(1200)
    assert sobra == to_cents(200)
    assert sum(ajustado.values()) == sum(planejado.values())


def test_reserva_completa_manda_tudo_para_independencia() -> None:
    """Com a meta atingida, a Reserva não recebe mais nada."""
    planejado = {
        Category.INDEPENDENCIA: to_cents(1000),
        Category.RESERVA: to_cents(300),
    }
    ajustado, sobra = budget.aplicar_regra_reserva(
        planejado, reserva_atual_cents=to_cents(6000), meta_cents=to_cents(6000)
    )

    assert ajustado[Category.RESERVA] == 0
    assert ajustado[Category.INDEPENDENCIA] == to_cents(1300)
    assert sobra == to_cents(300)


def test_regra_da_reserva_no_plano_completo(session: Session, receita) -> None:
    """A regra vale ponta a ponta, a partir do fechamento informado."""
    receita(2000.00)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(5900),
        investimentos_cents=0,
        dividendos_cents=0,
    )

    plano = budget.get_month_plan(session, SETEMBRO)

    assert plano.planejado[Category.RESERVA] == to_cents(100)
    assert plano.planejado[Category.INDEPENDENCIA] == to_cents(980) + to_cents(240)
    assert plano.sobra_reserva_cents == to_cents(240)
    assert plano.total_planejado_cents == plano.base_cents


# --------------------------------------------------------------------------
# 4 — checkbox e nova renda
# --------------------------------------------------------------------------
def test_nova_receita_deixa_separacao_pendente_sem_perder_valor(
    session: Session, receita
) -> None:
    """Confirmar, receber mais e ver a categoria voltar a pendente."""
    receita(3000.00)
    plano = budget.get_month_plan(session, SETEMBRO)
    planejado_inicial = plano.planejado[Category.INDEPENDENCIA]

    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    linha = budget.get_month_plan(session, SETEMBRO).separacao(Category.INDEPENDENCIA)
    assert linha.separado_cents == planejado_inicial
    assert linha.feito is True
    assert linha.falta_cents == 0

    receita(500.00, descricao="serviço extra")
    linha = budget.get_month_plan(session, SETEMBRO).separacao(Category.INDEPENDENCIA)

    assert linha.separado_cents == planejado_inicial, "o valor separado não pode sumir"
    assert linha.planejado_cents > planejado_inicial
    assert linha.feito is False
    assert linha.status == "Parcial"
    assert linha.falta_cents == linha.planejado_cents - planejado_inicial

    budget.confirmar_separacao(session, SETEMBRO, Category.INDEPENDENCIA)
    linha = budget.get_month_plan(session, SETEMBRO).separacao(Category.INDEPENDENCIA)
    assert linha.feito is True
    assert linha.falta_cents == 0


def test_receita_incrementa_revisao_do_mes(session: Session, receita) -> None:
    """Cada alteração de receita gera uma nova revisão do plano."""
    inicial = repo.get_revision(session, SETEMBRO)
    r = receita(100.00)
    depois_de_criar = repo.get_revision(session, SETEMBRO)
    assert depois_de_criar > inicial

    repo.delete_income(session, r.id)
    assert repo.get_revision(session, SETEMBRO) > depois_de_criar


def test_excluir_receita_recalcula_plano(session: Session, receita) -> None:
    """Excluir a receita zera o plano do mês."""
    r = receita(1000.00)
    assert budget.get_month_plan(session, SETEMBRO).base_cents == to_cents(1000)

    repo.delete_income(session, r.id)
    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.base_cents == 0
    assert plano.total_planejado_cents == 0


# --------------------------------------------------------------------------
# 7 — configuração versionada
# --------------------------------------------------------------------------
def test_configuracao_de_outubro_nao_altera_setembro(session: Session, receita) -> None:
    """Mudar os percentuais a partir de outubro preserva o plano de setembro."""
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)

    setembro_antes = budget.get_month_plan(session, SETEMBRO).planejado

    novos = dict(PESOS_PADRAO)
    novos[Category.INDEPENDENCIA] = 5500
    novos[Category.RESERVA] = 1100
    repo.upsert_settings_version(
        session,
        effective_month=OUTUBRO,
        meta_reserva_cents=to_cents(6000),
        percentuais_bp=novos,
    )
    repo.bump_revisions_from(session, OUTUBRO)

    setembro_depois = budget.get_month_plan(session, SETEMBRO).planejado
    outubro = budget.get_month_plan(session, OUTUBRO).planejado

    assert setembro_depois == setembro_antes
    assert setembro_depois[Category.INDEPENDENCIA] == to_cents(490)
    assert outubro[Category.INDEPENDENCIA] == to_cents(550)


def test_configuracao_nova_invalida_confirmacao_do_mes(session: Session, receita) -> None:
    """Se o plano do mês muda por configuração, a confirmação deixa de valer."""
    receita(1000.00, mes=OUTUBRO)
    budget.confirmar_separacao(session, OUTUBRO, Category.INDEPENDENCIA)
    assert budget.get_month_plan(session, OUTUBRO).separacao(Category.INDEPENDENCIA).feito

    novos = dict(PESOS_PADRAO)
    novos[Category.INDEPENDENCIA] = 5500
    novos[Category.RESERVA] = 1100
    repo.upsert_settings_version(
        session,
        effective_month=OUTUBRO,
        meta_reserva_cents=to_cents(6000),
        percentuais_bp=novos,
    )
    repo.bump_revisions_from(session, OUTUBRO)

    linha = budget.get_month_plan(session, OUTUBRO).separacao(Category.INDEPENDENCIA)
    assert linha.feito is False
    assert linha.separado_cents == to_cents(490), "o que já foi separado continua registrado"


def test_settings_do_mes_pega_versao_vigente(session: Session) -> None:
    """Um mês usa a versão mais recente que começou até ele."""
    novos = dict(PESOS_PADRAO)
    repo.upsert_settings_version(
        session,
        effective_month=date(2026, 10, 1),
        meta_reserva_cents=to_cents(8000),
        percentuais_bp=novos,
    )

    assert repo.get_settings_for_month(session, SETEMBRO).meta_reserva_cents == to_cents(6000)
    assert repo.get_settings_for_month(session, OUTUBRO).meta_reserva_cents == to_cents(8000)
    assert repo.get_settings_for_month(
        session, date(2027, 3, 1)
    ).meta_reserva_cents == to_cents(8000)
