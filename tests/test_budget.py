"""Testes das regras de distribuição, metas e separações."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import repositories as repo
from core.models import CategoryBehavior, IncomeType
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, mes

BASE_SETEMBRO = to_cents(2952.21)

ESPERADO_SETEMBRO = {
    "independencia": to_cents(1446.58),
    "reserva": to_cents(501.88),
    "viagem": to_cents(413.31),
    "compras": to_cents(265.70),
    "namorada": to_cents(206.65),
    "amigos": to_cents(88.57),
    "outro": 0,
    "livre": to_cents(29.52),
}


def _por_slug(session: Session, planejado: dict[int, int], month: date) -> dict[str, int]:
    """Traduz o plano de ids para slugs, para asserções legíveis."""
    slugs = {v.id: v.slug for v in cat.resolve_all(session, month)}
    return {slugs[cid]: valor for cid, valor in planejado.items() if valor or True}


# --------------------------------------------------------------------------
# 1 e 2 — rateio
# --------------------------------------------------------------------------
def test_percentuais_somam_cem_por_cento(session: Session) -> None:
    """Os percentuais padrão somam exatamente 100%."""
    vistas = cat.resolve_all(session, SETEMBRO)
    assert cat.total_bp(vistas) == budget.TOTAL_BP
    cat.validar_total(vistas)


def test_distribuicao_nao_perde_nem_inventa_centavos(session: Session) -> None:
    """A soma das fatias é sempre igual à base, para várias bases."""
    vistas = cat.resolve_all(session, SETEMBRO)
    for base in [0, 1, 7, 99, 100_00, 295_221, 333_333, 1_000_001]:
        fatias = budget.distribuir(base, vistas)
        assert sum(fatias.values()) == base, f"falhou para base {base}"


def test_distribuicao_da_base_de_setembro(session: Session) -> None:
    """R$ 2.952,21 produz exatamente os valores esperados."""
    vistas = cat.resolve_all(session, SETEMBRO)
    resultado = _por_slug(session, budget.distribuir(BASE_SETEMBRO, vistas), SETEMBRO)

    for slug, esperado in ESPERADO_SETEMBRO.items():
        assert resultado[slug] == esperado, slug
    assert sum(resultado.values()) == BASE_SETEMBRO


def test_plano_do_mes_usa_base_e_nao_recebido(session: Session, receita) -> None:
    """Saldo inicial fora do orçamento não conta como renda nem entra no rateio."""
    receita(1775.94, tipo=IncomeType.SALARIO, descricao="Primeiro salário")
    receita(700.00, tipo=IncomeType.VA_VR, descricao="VA/VR")
    receita(476.27, tipo=IncomeType.SALDO_INICIAL, descricao="Saldo anterior Rico")

    plano = budget.get_month_plan(session, SETEMBRO)

    assert plano.recebido_cents == to_cents(2475.94)
    assert plano.base_cents == BASE_SETEMBRO
    assert _por_slug(session, plano.planejado, SETEMBRO) == ESPERADO_SETEMBRO
    assert plano.total_planejado_cents == plano.base_cents


# --------------------------------------------------------------------------
# 3 — regra da meta (sem citar "Reserva" pelo nome)
# --------------------------------------------------------------------------
def test_sobra_da_meta_vai_para_a_categoria_de_destino(session: Session) -> None:
    """Faltando R$ 100 para a meta, os outros R$ 200 vão para o destino."""
    vistas = cat.resolve_all(session, SETEMBRO)
    meta = next(v for v in vistas if v.target_amount_cents is not None)
    destino = meta.overflow_target_category_id

    planejado = {destino: to_cents(1000), meta.id: to_cents(300)}
    ajustado, sobra = budget.aplicar_regras_de_meta(
        planejado, vistas, {meta.id: to_cents(5900)}
    )

    assert ajustado[meta.id] == to_cents(100)
    assert ajustado[destino] == to_cents(1200)
    assert sobra == to_cents(200)
    assert sum(ajustado.values()) == sum(planejado.values())


def test_meta_completa_manda_tudo_para_o_destino(session: Session) -> None:
    """Com a meta atingida, a categoria não recebe mais nada."""
    vistas = cat.resolve_all(session, SETEMBRO)
    meta = next(v for v in vistas if v.target_amount_cents is not None)
    destino = meta.overflow_target_category_id

    planejado = {destino: to_cents(1000), meta.id: to_cents(300)}
    ajustado, sobra = budget.aplicar_regras_de_meta(
        planejado, vistas, {meta.id: to_cents(6000)}
    )

    assert ajustado[meta.id] == 0
    assert ajustado[destino] == to_cents(1300)
    assert sobra == to_cents(300)


def test_regra_da_meta_no_plano_completo(session: Session, receita, cats) -> None:
    """A regra vale ponta a ponta, a partir do fechamento informado."""
    receita(2000.00)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(5900),
        investimentos_cents=0,
    )

    plano = budget.get_month_plan(session, SETEMBRO)

    assert plano.planejado[cats["reserva"]] == to_cents(100)
    assert plano.planejado[cats["independencia"]] == to_cents(980) + to_cents(240)
    assert plano.sobra_meta_cents == to_cents(240)
    assert plano.total_planejado_cents == plano.base_cents


# --------------------------------------------------------------------------
# 4 — checkbox e nova renda
# --------------------------------------------------------------------------
def test_nova_receita_deixa_separacao_pendente_sem_perder_valor(
    session: Session, receita, cats
) -> None:
    """Confirmar, receber mais e ver a categoria voltar a pendente."""
    receita(3000.00)
    plano = budget.get_month_plan(session, SETEMBRO)
    planejado_inicial = plano.planejado[cats["independencia"]]

    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    linha = budget.get_month_plan(session, SETEMBRO).separacao(cats["independencia"])
    assert linha.separado_cents == planejado_inicial
    assert linha.feito is True
    assert linha.falta_cents == 0

    receita(500.00, descricao="serviço extra")
    linha = budget.get_month_plan(session, SETEMBRO).separacao(cats["independencia"])

    assert linha.separado_cents == planejado_inicial, "o valor separado não pode sumir"
    assert linha.planejado_cents > planejado_inicial
    assert linha.feito is False
    assert linha.status == "Parcial"
    assert linha.falta_cents == linha.planejado_cents - planejado_inicial

    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    linha = budget.get_month_plan(session, SETEMBRO).separacao(cats["independencia"])
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
def test_configuracao_de_outubro_nao_altera_setembro(
    session: Session, receita, cats
) -> None:
    """Mudar os percentuais a partir de outubro preserva o plano de setembro."""
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)

    setembro_antes = dict(budget.get_month_plan(session, SETEMBRO).planejado)

    repo.upsert_settings_version(
        session,
        effective_month=OUTUBRO,
        percentuais_bp={cats["independencia"]: 5500, cats["reserva"]: 1100},
    )
    repo.bump_revisions_from(session, OUTUBRO)

    setembro_depois = budget.get_month_plan(session, SETEMBRO).planejado
    outubro = budget.get_month_plan(session, OUTUBRO).planejado

    assert setembro_depois == setembro_antes
    assert setembro_depois[cats["independencia"]] == to_cents(490)
    assert outubro[cats["independencia"]] == to_cents(550)


def test_configuracao_nova_invalida_confirmacao_do_mes(
    session: Session, receita, cats
) -> None:
    """Se o plano do mês muda por configuração, a confirmação deixa de valer."""
    receita(1000.00, mes=OUTUBRO)
    budget.confirmar_separacao(session, OUTUBRO, cats["independencia"])
    assert budget.get_month_plan(session, OUTUBRO).separacao(cats["independencia"]).feito

    repo.upsert_settings_version(
        session,
        effective_month=OUTUBRO,
        percentuais_bp={cats["independencia"]: 5500, cats["reserva"]: 1100},
    )
    repo.bump_revisions_from(session, OUTUBRO)

    linha = budget.get_month_plan(session, OUTUBRO).separacao(cats["independencia"])
    assert linha.feito is False
    assert linha.separado_cents == to_cents(490), "o que já foi separado continua registrado"


def test_versao_do_mes_pega_a_configuracao_vigente(session: Session, cats) -> None:
    """Um mês usa a versão mais recente que começou até ele."""
    repo.upsert_settings_version(
        session, effective_month=OUTUBRO, meta_reserva_cents=to_cents(8000)
    )

    def meta_em(mes_alvo: date) -> int:
        vista = next(
            v for v in cat.resolve_all(session, mes_alvo) if v.id == cats["reserva"]
        )
        return vista.target_amount_cents or 0

    assert meta_em(SETEMBRO) == to_cents(6000)
    assert meta_em(OUTUBRO) == to_cents(8000)
    assert meta_em(date(2027, 3, 1)) == to_cents(8000)


def test_meta_usa_target_amount_e_nao_o_nome(session: Session, receita, cats) -> None:
    """Renomear a categoria-meta não muda nada; mudar o alvo muda."""
    receita(2000.00)
    cat.upsert_version(session, cats["reserva"], SETEMBRO, name="Colchão")
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(5900),
        investimentos_cents=0,    )

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.planejado[cats["reserva"]] == to_cents(100)
    assert plano.sobra_meta_cents == to_cents(240)


def test_overflow_usa_id_e_nao_nome(session: Session, receita, cats) -> None:
    """A sobra segue o id declarado, mesmo com os nomes trocados."""
    receita(2000.00)
    cat.upsert_version(session, cats["independencia"], SETEMBRO, name="Aposentadoria")
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(6000),
        investimentos_cents=0,    )

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.planejado[cats["reserva"]] == 0
    assert plano.planejado[cats["independencia"]] == to_cents(980) + to_cents(340)


def test_resumo_de_separacoes_bate_com_o_plano(session: Session, receita, cats) -> None:
    """Home e página Separações leem o mesmo resumo."""
    receita(2952.21)
    resumo = budget.resumo_separacoes(session, SETEMBRO)
    plano = budget.get_month_plan(session, SETEMBRO)

    assert resumo.total == len([s for s in plano.separacoes if s.planejado_cents > 0])
    assert resumo.pendentes == resumo.total
    assert resumo.falta_cents == plano.total_falta_separar_cents

    for vista in cat.resolve_active(session, SETEMBRO):
        budget.confirmar_separacao(session, SETEMBRO, vista.id)

    resumo = budget.resumo_separacoes(session, SETEMBRO)
    assert resumo.tudo_feito is True
    assert resumo.falta_cents == 0
