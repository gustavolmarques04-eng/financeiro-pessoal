"""Primeiro acesso: o plano nasce inteiro ou não nasce."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import categories as cat
from core import onboarding_service as onboarding
from core import profile_service
from core.categories import CategoryError
from core.models import Category
from core.utils import to_cents

from .conftest import SETEMBRO


def _plano(*fatias: tuple[str, int], **extras) -> onboarding.PlanoInicial:
    """Plano com as fatias informadas, em pontos-base."""
    return onboarding.PlanoInicial(
        categorias=[
            onboarding.CategoriaDesejada(nome=nome, percent_bp=bp)
            for nome, bp in fatias
        ],
        **extras,
    )


# --------------------------------------------------------------------------
# Os 100%
# --------------------------------------------------------------------------
def test_nao_conclui_com_soma_diferente_de_cem(session_vazia: Session) -> None:
    """93% ou 107% não viram banco de dados."""
    for fatias in (
        (("Reserva", 3000), ("Livre", 6300)),
        (("Reserva", 5000), ("Livre", 6000)),
    ):
        with pytest.raises(CategoryError, match="100%"):
            onboarding.criar_plano_inicial(
                session_vazia, _plano(*fatias), a_partir_de=SETEMBRO
            )


def test_plano_furado_nao_grava_nem_a_primeira_categoria(
    session_vazia: Session,
) -> None:
    """Sem atomicidade, o banco ficaria com um plano somando 30%.

    É o cenário que a validação antes da escrita existe para impedir.
    """
    antes = session_vazia.scalars(select(Category)).all()

    with pytest.raises(CategoryError):
        onboarding.criar_plano_inicial(
            session_vazia,
            _plano(("Reserva", 3000), ("Livre", 3000)),
            a_partir_de=SETEMBRO,
        )

    depois = session_vazia.scalars(select(Category)).all()
    assert len(depois) == len(antes), "nenhuma categoria pode ter sobrado"


def test_plano_que_fecha_cem_grava_tudo(session_vazia: Session) -> None:
    """Com 100% exatos, todas as categorias nascem juntas."""
    criadas = onboarding.criar_plano_inicial(
        session_vazia,
        _plano(("Reserva", 3000), ("Faculdade", 2000), ("Livre", 5000)),
        a_partir_de=SETEMBRO,
    )

    assert set(criadas) == {"Reserva", "Faculdade", "Livre"}
    vistas = cat.resolve_active(session_vazia, SETEMBRO)
    assert {v.name for v in vistas} == {"Reserva", "Faculdade", "Livre"}
    cat.validar_total(vistas)


# --------------------------------------------------------------------------
# Conteúdo das categorias
# --------------------------------------------------------------------------
def test_meta_e_opcional(session_vazia: Session) -> None:
    """Nenhuma categoria é obrigada a ter meta."""
    plano = onboarding.PlanoInicial(
        categorias=[
            onboarding.CategoriaDesejada(
                nome="Reserva", percent_bp=5000, meta_cents=to_cents(6000)
            ),
            onboarding.CategoriaDesejada(nome="Livre", percent_bp=5000),
        ]
    )
    onboarding.criar_plano_inicial(session_vazia, plano, a_partir_de=SETEMBRO)

    vistas = {v.name: v for v in cat.resolve_active(session_vazia, SETEMBRO)}
    assert vistas["Reserva"].target_amount_cents == to_cents(6000)
    assert vistas["Livre"].target_amount_cents is None


def test_saldo_inicial_vira_saldo_de_verdade(session_vazia: Session) -> None:
    """"Já tenho R$ 2.000 aqui" precisa aparecer como saldo."""
    from core import budget_service as budget

    plano = onboarding.PlanoInicial(
        categorias=[
            onboarding.CategoriaDesejada(
                nome="Reserva",
                percent_bp=10000,
                saldo_inicial_cents=to_cents(2000),
            )
        ]
    )
    onboarding.criar_plano_inicial(session_vazia, plano, a_partir_de=SETEMBRO)

    reserva = cat.resolve_active(session_vazia, SETEMBRO)[0]
    assert budget.saldo_categoria(session_vazia, reserva, SETEMBRO) == to_cents(2000)


def test_patrimonio_respeita_a_escolha_de_cada_categoria(
    session_vazia: Session,
) -> None:
    """Só entra no patrimônio o que o usuário disse que entra."""
    from core import budget_service as budget
    from core.period import Period

    plano = onboarding.PlanoInicial(
        categorias=[
            onboarding.CategoriaDesejada(
                nome="Reserva",
                percent_bp=5000,
                saldo_inicial_cents=to_cents(1000),
                conta_no_patrimonio=True,
            ),
            onboarding.CategoriaDesejada(
                nome="Livre",
                percent_bp=5000,
                saldo_inicial_cents=to_cents(300),
                conta_no_patrimonio=False,
            ),
        ]
    )
    onboarding.criar_plano_inicial(session_vazia, plano, a_partir_de=SETEMBRO)

    patrimonio = budget.get_patrimonio(session_vazia, Period.of_month(SETEMBRO))
    assert patrimonio.total_cents == to_cents(1000), "só a reserva"
    assert patrimonio.guardado_cents == to_cents(1300), "mas o dinheiro todo existe"


# --------------------------------------------------------------------------
# Nomes e duplicatas
# --------------------------------------------------------------------------
def test_recusa_nomes_repetidos(session_vazia: Session) -> None:
    """Duas "Viagem" deixariam o usuário sem saber qual é qual."""
    with pytest.raises(CategoryError, match="duas categorias"):
        onboarding.criar_plano_inicial(
            session_vazia,
            _plano(("Viagem", 5000), ("viagem", 5000)),
            a_partir_de=SETEMBRO,
        )


def test_recusa_categoria_sem_nome(session_vazia: Session) -> None:
    with pytest.raises(CategoryError, match="nome"):
        onboarding.criar_plano_inicial(
            session_vazia, _plano(("   ", 10000)), a_partir_de=SETEMBRO
        )


# --------------------------------------------------------------------------
# Perfil
# --------------------------------------------------------------------------
def test_investimentos_e_reserva_ficam_guardados_por_id(
    session_vazia: Session,
) -> None:
    """Guardar por id é o que permite renomear sem quebrar nada."""
    criadas = onboarding.criar_plano_inicial(
        session_vazia,
        _plano(
            ("Ações", 6000),
            ("Emergência", 4000),
            investimentos="Ações",
            reserva="Emergência",
        ),
        a_partir_de=SETEMBRO,
    )

    preferencias = profile_service.obter(session_vazia)
    assert preferencias.investment_category_id == criadas["Ações"]
    assert preferencias.reserve_category_id == criadas["Emergência"]

    # Renomear não pode desfazer a escolha.
    cat.upsert_version(session_vazia, criadas["Ações"], SETEMBRO, name="Investimentos")
    assert profile_service.obter(session_vazia).investment_category_id == criadas["Ações"]


def test_escolher_categoria_que_nao_existe_e_recusado(session_vazia: Session) -> None:
    with pytest.raises(CategoryError, match="investimentos"):
        onboarding.criar_plano_inicial(
            session_vazia,
            _plano(("Reserva", 10000), investimentos="Tesouro Direto"),
            a_partir_de=SETEMBRO,
        )


def test_onboarding_so_conclui_depois_de_gravar(session_vazia: Session) -> None:
    """A marca de concluído não pode preceder o plano."""
    assert profile_service.precisa_de_onboarding(session_vazia) is True

    with pytest.raises(CategoryError):
        onboarding.criar_plano_inicial(
            session_vazia, _plano(("Reserva", 9000)), a_partir_de=SETEMBRO
        )
    assert profile_service.precisa_de_onboarding(session_vazia) is True, (
        "um plano recusado não conclui o primeiro acesso"
    )

    onboarding.criar_plano_inicial(
        session_vazia, _plano(("Reserva", 10000)), a_partir_de=SETEMBRO
    )
    assert profile_service.precisa_de_onboarding(session_vazia) is False


def test_cada_usuario_monta_o_proprio_plano(session_vazia: Session) -> None:
    """Categorias totalmente diferentes, sem interferência."""
    criadas = onboarding.criar_plano_inicial(
        session_vazia,
        _plano(("Investimentos", 6000), ("Viagem", 1500), ("Livre", 2500)),
        a_partir_de=SETEMBRO,
    )
    assert len(criadas) == 3
    cat.validar_total(cat.resolve_active(session_vazia, SETEMBRO))
