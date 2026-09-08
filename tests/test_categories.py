"""Testes das categorias configuráveis e do seu versionamento."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import repositories as repo
from core.models import CategoryBehavior
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, mes

JANEIRO_27 = date(2027, 1, 1)


# --------------------------------------------------------------------------
# 7 e 8 — criar categoria afeta o futuro, não o passado
# --------------------------------------------------------------------------
def test_adicionar_categoria_atualiza_o_plano_futuro(
    session: Session, receita, cats
) -> None:
    """A nova categoria passa a receber percentual a partir do mês escolhido."""
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)

    nova = cat.criar_categoria(
        session,
        name="Estudos",
        behavior=CategoryBehavior.MONTHLY_SPENDING,
        percent_bp=500,
        effective_month=OUTUBRO,
    )
    # Tira exatamente 500 bps de quem tinha, para o plano fechar 100%.
    cat.upsert_version(session, cats["livre"], OUTUBRO, percent_bp=0)  # -100
    cat.upsert_version(session, cats["amigos"], OUTUBRO, percent_bp=0)  # -300
    cat.upsert_version(session, cats["namorada"], OUTUBRO, percent_bp=600)  # -100
    repo.bump_revisions_from(session, OUTUBRO)

    cat.validar_total(cat.resolve_all(session, OUTUBRO))
    outubro = budget.get_month_plan(session, OUTUBRO)
    assert outubro.planejado[nova.id] == to_cents(50)
    assert outubro.total_planejado_cents == outubro.base_cents


def test_adicionar_categoria_nao_altera_o_passado(
    session: Session, receita, cats
) -> None:
    """Setembro continua com o plano que tinha antes da nova categoria."""
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)
    setembro_antes = dict(budget.get_month_plan(session, SETEMBRO).planejado)

    nova = cat.criar_categoria(
        session,
        name="Estudos",
        behavior=CategoryBehavior.MONTHLY_SPENDING,
        percent_bp=500,
        effective_month=OUTUBRO,
    )
    repo.bump_revisions_from(session, OUTUBRO)

    setembro_depois = budget.get_month_plan(session, SETEMBRO).planejado
    assert setembro_depois == setembro_antes
    assert nova.id not in setembro_depois
    assert nova.id in budget.get_month_plan(session, OUTUBRO).planejado


def test_nenhum_mes_anterior_muda_por_configuracao_futura(
    session: Session, receita, cats
) -> None:
    """Mexer em 2027 não pode tocar em nada de 2026."""
    receita(2952.21, mes=SETEMBRO)
    receita(2952.21, mes=OUTUBRO)
    antes = {
        m: dict(budget.get_month_plan(session, m).planejado)
        for m in (SETEMBRO, OUTUBRO)
    }

    cat.upsert_version(session, cats["independencia"], JANEIRO_27, percent_bp=6000)
    cat.upsert_version(session, cats["reserva"], JANEIRO_27, percent_bp=600)
    repo.bump_revisions_from(session, JANEIRO_27)

    for m in (SETEMBRO, OUTUBRO):
        assert budget.get_month_plan(session, m).planejado == antes[m]


# --------------------------------------------------------------------------
# 9 — desativar preserva histórico
# --------------------------------------------------------------------------
def test_desativar_categoria_nao_apaga_historico(
    session: Session, receita, gasto, cats
) -> None:
    """A categoria some do plano novo mas os lançamentos antigos continuam."""
    receita(2952.21, mes=SETEMBRO)
    gasto(100.00, cats["namorada"], mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    cat.desativar_categoria(session, cats["namorada"], OUTUBRO)
    cat.upsert_version(session, cats["livre"], OUTUBRO, percent_bp=800)
    repo.bump_revisions_from(session, OUTUBRO)

    setembro = budget.get_month_plan(session, SETEMBRO)
    assert any(g.categoria.slug == "namorada" for g in setembro.gastos)
    assert setembro.planejado[cats["namorada"]] == to_cents(206.65)
    assert repo.sum_expenses(session, mes(SETEMBRO), category_id=cats["namorada"]) == (
        to_cents(100)
    )

    outubro = budget.get_month_plan(session, OUTUBRO)
    assert not any(g.categoria.slug == "namorada" for g in outubro.gastos)
    assert cat.get_view(session, cats["namorada"], OUTUBRO).active is False


def test_categoria_desativada_some_do_rateio_mas_o_total_fecha(
    session: Session, receita, cats
) -> None:
    """Depois de redistribuir o percentual, o plano continua batendo 100%."""
    receita(1000.00, mes=OUTUBRO)
    cat.desativar_categoria(session, cats["livre"], OUTUBRO)
    cat.upsert_version(session, cats["amigos"], OUTUBRO, percent_bp=400)

    vistas = cat.resolve_all(session, OUTUBRO)
    cat.validar_total(vistas)

    plano = budget.get_month_plan(session, OUTUBRO)
    assert plano.total_planejado_cents == plano.base_cents


# --------------------------------------------------------------------------
# 10 e 20 — soma tem de ser 10000 bps
# --------------------------------------------------------------------------
def test_total_diferente_de_cem_por_cento_nao_passa(session: Session, cats) -> None:
    """Salvar com soma diferente de 10000 bps é bloqueado."""
    cat.upsert_version(session, cats["livre"], OUTUBRO, percent_bp=300)
    vistas = cat.resolve_all(session, OUTUBRO)

    assert cat.total_bp(vistas) == 10_200
    with pytest.raises(cat.CategoryError) as erro:
        cat.validar_total(vistas)
    assert "102.00%" in str(erro.value)
    assert "Excesso" in str(erro.value)


def test_mensagem_indica_quanto_falta(session: Session, cats) -> None:
    """A mensagem diz o quanto falta, não só que está errado."""
    cat.upsert_version(session, cats["livre"], OUTUBRO, percent_bp=0)
    vistas = cat.resolve_all(session, OUTUBRO)

    with pytest.raises(cat.CategoryError) as erro:
        cat.validar_total(vistas)
    assert "99.00%" in str(erro.value)
    assert "Faltam" in str(erro.value)


def test_categoria_de_acompanhamento_fica_fora_dos_cem(session: Session) -> None:
    """``TRACKING_ONLY`` não participa do rateio nem da validação."""
    vistas = cat.resolve_all(session, SETEMBRO)
    outro = next(v for v in vistas if v.slug == "outro")

    assert outro.receives_percent is False
    assert outro.percent_bp == 0
    cat.validar_total(vistas)


# --------------------------------------------------------------------------
# 11 — invalidar apenas os meses afetados
# --------------------------------------------------------------------------
def test_mudar_percentual_invalida_so_os_meses_afetados(
    session: Session, receita, cats
) -> None:
    """Confirmação de setembro sobrevive a uma mudança que vale de outubro."""
    receita(2952.21, mes=SETEMBRO)
    receita(2952.21, mes=OUTUBRO)
    for m in (SETEMBRO, OUTUBRO):
        budget.confirmar_separacao(session, m, cats["independencia"])

    revisao_setembro = repo.get_revision(session, SETEMBRO)

    cat.upsert_version(session, cats["independencia"], OUTUBRO, percent_bp=5500)
    cat.upsert_version(session, cats["reserva"], OUTUBRO, percent_bp=1100)
    afetados = repo.bump_revisions_from(session, OUTUBRO)

    assert SETEMBRO not in afetados
    assert OUTUBRO in afetados
    assert repo.get_revision(session, SETEMBRO) == revisao_setembro

    assert budget.get_month_plan(session, SETEMBRO).separacao(
        cats["independencia"]
    ).feito is True
    assert budget.get_month_plan(session, OUTUBRO).separacao(
        cats["independencia"]
    ).feito is False


# --------------------------------------------------------------------------
# 12 e 13 — categorias válidas na data do gasto
# --------------------------------------------------------------------------
def test_gasto_usa_categorias_validas_na_data(session: Session, cats) -> None:
    """O formulário de setembro oferece o que existia em setembro."""
    cat.criar_categoria(
        session,
        name="Pet",
        behavior=CategoryBehavior.MONTHLY_SPENDING,
        percent_bp=0,
        effective_month=OUTUBRO,
    )

    slugs_setembro = {v.slug for v in cat.resolve_active(session, SETEMBRO)}
    slugs_outubro = {v.slug for v in cat.resolve_active(session, OUTUBRO)}

    assert "pet" not in slugs_setembro
    assert "pet" in slugs_outubro


def test_categoria_historica_continua_disponivel_ao_editar_gasto_antigo(
    session: Session, gasto, cats
) -> None:
    """Editar um gasto de 2026 ainda encontra a categoria desativada em 2027."""
    compra = gasto(100.00, cats["namorada"], mes=SETEMBRO)
    cat.desativar_categoria(session, cats["namorada"], JANEIRO_27)

    disponiveis_2027 = {v.slug for v in cat.resolve_active(session, JANEIRO_27)}
    assert "namorada" not in disponiveis_2027

    # Na data da compra a categoria existia e continua resolvível.
    historicas = {v.slug for v in cat.resolve_all(session, compra.purchase_date)}
    assert "namorada" in historicas
    vista = cat.get_view(session, cats["namorada"], compra.purchase_date)
    assert vista is not None and vista.active is True

    # E continua acessível mesmo consultando pela ótica de 2027.
    todas_2027 = {v.id for v in cat.resolve_all(session, JANEIRO_27)}
    assert cats["namorada"] in todas_2027


# --------------------------------------------------------------------------
# 14 — mudança estrutural bloqueada
# --------------------------------------------------------------------------
def test_impedir_mudanca_de_comportamento_com_historico(
    session: Session, gasto, cats
) -> None:
    """Com histórico, trocar orçamento mensal por envelope é bloqueado."""
    gasto(100.00, cats["namorada"], mes=SETEMBRO)

    permitido, motivo = cat.pode_trocar_comportamento(
        session, cats["namorada"], CategoryBehavior.ACCUMULATING_ENVELOPE
    )

    assert permitido is False
    assert "histórico" in motivo.lower()
    assert "desative" in motivo.lower()


def test_permitir_mudanca_em_categoria_nunca_usada(session: Session) -> None:
    """Sem histórico, o comportamento pode ser corrigido à vontade."""
    nova = cat.criar_categoria(
        session,
        name="Cursos",
        behavior=CategoryBehavior.MONTHLY_SPENDING,
        percent_bp=0,
        effective_month=SETEMBRO,
    )

    permitido, motivo = cat.pode_trocar_comportamento(
        session, nova.id, CategoryBehavior.ACCUMULATING_ENVELOPE
    )
    assert permitido is True
    assert motivo == ""


def test_separacao_confirmada_tambem_conta_como_historico(
    session: Session, receita, cats
) -> None:
    """Separar dinheiro já cria histórico suficiente para bloquear."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    permitido, _ = cat.pode_trocar_comportamento(
        session, cats["viagem"], CategoryBehavior.MONTHLY_SPENDING
    )
    assert permitido is False


# --------------------------------------------------------------------------
# Renomear e versionar
# --------------------------------------------------------------------------
def test_renomear_categoria_preserva_o_nome_historico(
    session: Session, receita, cats
) -> None:
    """Setembro continua mostrando "Namorada"; 2027 mostra o nome novo."""
    receita(1000.00, mes=SETEMBRO)
    cat.upsert_version(
        session, cats["namorada"], JANEIRO_27, name="Relacionamento", percent_bp=800
    )
    cat.upsert_version(session, cats["livre"], JANEIRO_27, percent_bp=0)

    setembro = cat.get_view(session, cats["namorada"], SETEMBRO)
    futuro = cat.get_view(session, cats["namorada"], JANEIRO_27)

    assert setembro.name == "Namorada" and setembro.percent_bp == 700
    assert futuro.name == "Relacionamento" and futuro.percent_bp == 800
    assert setembro.id == futuro.id, "a identidade da categoria não muda"


def test_slug_permanece_estavel_apos_renomear(session: Session, cats) -> None:
    """O identificador estável não acompanha o nome."""
    cat.upsert_version(session, cats["namorada"], JANEIRO_27, name="Relacionamento")
    assert cat.get_view(session, cats["namorada"], JANEIRO_27).slug == "namorada"


def test_criar_categoria_gera_slug_unico(session: Session) -> None:
    """Nomes repetidos não colidem no identificador."""
    a = cat.criar_categoria(
        session, name="Pet", behavior=CategoryBehavior.MONTHLY_SPENDING,
        percent_bp=0, effective_month=SETEMBRO,
    )
    b = cat.criar_categoria(
        session, name="Pet", behavior=CategoryBehavior.MONTHLY_SPENDING,
        percent_bp=0, effective_month=SETEMBRO,
    )
    assert a.slug == "pet"
    assert b.slug == "pet_2"
    assert a.id != b.id
