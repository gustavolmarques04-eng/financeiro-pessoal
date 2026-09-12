"""Estorno, conferência de saldo e desativação com dinheiro dentro.

Três situações em que seria fácil o dinheiro sumir sem explicação — e que
por isso deixam rastro em vez de sobrescrever números.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import investment_service as investimentos
from core import profile_service
from core import repositories as repo
from core.models import AdjustmentKind
from core.period import Period
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, mes


def _saldo(session: Session, slug: str, quando=SETEMBRO) -> int:
    vista = next(v for v in cat.resolve_all(session, quando) if v.slug == slug)
    return budget.saldo_categoria(session, vista, quando)


# --------------------------------------------------------------------------
# Estorno
# --------------------------------------------------------------------------
def test_estorno_devolve_o_dinheiro_e_deixa_as_duas_linhas(
    session: Session, receita, cats
) -> None:
    """Corrigir é estornar: o original continua no histórico."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    viagem_antes = _saldo(session, "viagem")
    compras_antes = _saldo(session, "compras")

    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["compras"],
        valor_cents=to_cents(100),
        note="engano",
    )
    original = repo.list_transferencias(session, mes(SETEMBRO))[0]

    budget.estornar_transferencia(session, original.id)

    assert _saldo(session, "viagem") == viagem_antes
    assert _saldo(session, "compras") == compras_antes

    historico = repo.list_transferencias(session, mes(SETEMBRO))
    assert len(historico) == 2, "o original não é apagado"
    estorno = next(t for t in historico if t.reversal_of_id is not None)
    assert estorno.reversal_of_id == original.id
    assert estorno.from_category_id == original.to_category_id
    assert estorno.to_category_id == original.from_category_id


def test_nao_da_para_estornar_duas_vezes(session: Session, receita, cats) -> None:
    """Senão o dinheiro voltaria em dobro."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["compras"],
        valor_cents=to_cents(50),
    )
    original = repo.list_transferencias(session, mes(SETEMBRO))[0]
    budget.estornar_transferencia(session, original.id)

    with pytest.raises(budget.TransferenciaInvalida, match="já foi estornada"):
        budget.estornar_transferencia(session, original.id)


def test_estorno_de_estorno_e_recusado(session: Session, receita, cats) -> None:
    """Estornar um estorno seria só uma transferência disfarçada."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["compras"],
        valor_cents=to_cents(50),
    )
    original = repo.list_transferencias(session, mes(SETEMBRO))[0]
    budget.estornar_transferencia(session, original.id)
    estorno = next(
        t for t in repo.list_transferencias(session, mes(SETEMBRO))
        if t.reversal_of_id is not None
    )

    with pytest.raises(budget.TransferenciaInvalida, match="já é um estorno"):
        budget.estornar_transferencia(session, estorno.id)


def test_transferencia_nao_cria_nem_destroi_dinheiro(
    session: Session, receita, cats
) -> None:
    """A invariante central: o total guardado é o mesmo antes e depois."""
    receita(2952.21, mes=SETEMBRO)
    for vista in cat.resolve_active(session, SETEMBRO):
        budget.confirmar_separacao(session, SETEMBRO, vista.id)

    antes = budget.get_patrimonio(session, mes(SETEMBRO)).guardado_cents

    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["livre"],
        valor_cents=to_cents(77),
    )
    assert budget.get_patrimonio(session, mes(SETEMBRO)).guardado_cents == antes

    original = repo.list_transferencias(session, mes(SETEMBRO))[0]
    budget.estornar_transferencia(session, original.id)
    assert budget.get_patrimonio(session, mes(SETEMBRO)).guardado_cents == antes


def test_transferir_entre_tipos_muda_o_patrimonio_mas_nao_o_dinheiro(
    session: Session, receita, cats
) -> None:
    """Reclassificar dinheiro muda o patrimônio; não é perda."""
    receita(2952.21, mes=SETEMBRO)
    for vista in cat.resolve_active(session, SETEMBRO):
        budget.confirmar_separacao(session, SETEMBRO, vista.id)

    antes = budget.get_patrimonio(session, mes(SETEMBRO))
    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],  # conta no patrimônio
        destino_id=cats["livre"],  # não conta
        valor_cents=to_cents(100),
    )
    depois = budget.get_patrimonio(session, mes(SETEMBRO))

    assert depois.total_cents == antes.total_cents - to_cents(100)
    assert depois.guardado_cents == antes.guardado_cents, "o dinheiro continua lá"


# --------------------------------------------------------------------------
# Conferência de saldo
# --------------------------------------------------------------------------
def test_conferir_saldo_cria_ajuste_com_a_diferenca(
    session: Session, receita, cats
) -> None:
    """O saldo passa a bater, e o rendimento fica registrado como tal."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    calculado = _saldo(session, "viagem")
    real = calculado + to_cents(7.32)

    diferenca = budget.conferir_saldo(
        session,
        month=SETEMBRO,
        category_id=cats["viagem"],
        saldo_real_cents=real,
        motivo=AdjustmentKind.RENDIMENTO,
    )

    assert diferenca == to_cents(7.32)
    assert _saldo(session, "viagem") == real

    ajustes = repo.list_ajustes(session, mes(SETEMBRO))
    assert len(ajustes) == 1
    assert ajustes[0].amount_cents == to_cents(7.32)
    assert ajustes[0].kind is AdjustmentKind.RENDIMENTO


def test_conferir_saldo_que_ja_bate_nao_cria_nada(
    session: Session, receita, cats
) -> None:
    """Sem diferença, não há o que explicar."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    calculado = _saldo(session, "viagem")

    diferenca = budget.conferir_saldo(
        session,
        month=SETEMBRO,
        category_id=cats["viagem"],
        saldo_real_cents=calculado,
    )

    assert diferenca == 0
    assert repo.list_ajustes(session, mes(SETEMBRO)) == []


def test_ajuste_para_baixo_tambem_funciona(
    session: Session, receita, cats
) -> None:
    """Nem toda correção é para cima."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    calculado = _saldo(session, "viagem")

    budget.conferir_saldo(
        session,
        month=SETEMBRO,
        category_id=cats["viagem"],
        saldo_real_cents=calculado - to_cents(20),
        motivo=AdjustmentKind.CORRECAO,
    )

    assert _saldo(session, "viagem") == calculado - to_cents(20)


def test_ajuste_atravessa_o_mes(session: Session, receita, cats) -> None:
    """Corrigido em setembro, continua valendo em outubro."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.conferir_saldo(
        session,
        month=SETEMBRO,
        category_id=cats["viagem"],
        saldo_real_cents=_saldo(session, "viagem") + to_cents(10),
        motivo=AdjustmentKind.RENDIMENTO,
    )

    assert _saldo(session, "viagem", OUTUBRO) == _saldo(session, "viagem", SETEMBRO)


# --------------------------------------------------------------------------
# Desativar com saldo
# --------------------------------------------------------------------------
def test_desativar_com_saldo_move_o_dinheiro_antes(
    session: Session, receita, cats
) -> None:
    """Nada de saldo sumindo da tela sem ter sido gasto."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    saldo_viagem = _saldo(session, "viagem")
    compras_antes = _saldo(session, "compras")
    assert saldo_viagem > 0

    movido = budget.esvaziar_e_desativar(
        session, cats["viagem"], cats["compras"], SETEMBRO
    )

    assert movido == saldo_viagem
    assert _saldo(session, "viagem") == 0
    assert _saldo(session, "compras") == compras_antes + saldo_viagem
    assert cat.get_view(session, cats["viagem"], SETEMBRO).active is False


def test_desativar_com_divida_leva_a_divida_junto(
    session: Session, receita, gasto, cats
) -> None:
    """Saldo negativo também precisa de destino: some, mas não evapora."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])
    gasto(
        float(_saldo(session, "viagem") + to_cents(40)) / 100,
        cats["viagem"],
        mes=SETEMBRO,
    )
    assert _saldo(session, "viagem") == -to_cents(40)
    compras_antes = _saldo(session, "compras")

    budget.esvaziar_e_desativar(session, cats["viagem"], cats["compras"], SETEMBRO)

    assert _saldo(session, "viagem") == 0
    assert _saldo(session, "compras") == compras_antes - to_cents(40)


def test_saldo_impede_desativar_avisa_quanto_ha(
    session: Session, receita, cats
) -> None:
    """A tela precisa saber se pode desativar direto."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    assert budget.saldo_impede_desativar(session, cats["viagem"], SETEMBRO) > 0
    assert budget.saldo_impede_desativar(session, cats["amigos"], SETEMBRO) == 0


# --------------------------------------------------------------------------
# Investimentos sem dupla contagem
# --------------------------------------------------------------------------
def test_investimento_nao_e_contado_duas_vezes(
    session: Session, receita, cats
) -> None:
    """Com fechamento informado, ele substitui o saldo contábil.

    Somar os dois duplicaria o mesmo dinheiro no patrimônio.
    """
    profile_service.atualizar(session, investment_category_id=cats["independencia"])
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])

    valor, conferido = investimentos.valor_para_o_patrimonio(session, SETEMBRO)
    assert conferido is False, "sem fechamento, o valor é estimado"
    assert valor == _saldo(session, "independencia")

    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=to_cents(1600),
        dividendos_cents=0,
    )

    valor, conferido = investimentos.valor_para_o_patrimonio(session, SETEMBRO)
    assert conferido is True
    assert valor == to_cents(1600), "o informado manda, e não se soma ao saldo"


def test_sem_categoria_de_investimento_nao_ha_valor(
    session: Session, receita, cats
) -> None:
    """Sem escolha no perfil, o app não inventa um investimento."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])

    valor, conferido = investimentos.valor_para_o_patrimonio(session, SETEMBRO)
    assert (valor, conferido) == (0, False)


def test_patrimonio_usa_o_valor_informado_e_nao_soma_com_o_saldo(
    session_vazia: Session,
) -> None:
    """A dupla contagem que o §44 existe para impedir, ponta a ponta.

    Usa um plano criado no primeiro acesso: as categorias-semente antigas
    tinham o saldo amarrado ao fechamento, o que confundiria a medição.
    """
    from core import onboarding_service as onboarding
    from core.models import IncomeType

    criadas = onboarding.criar_plano_inicial(
        session_vazia,
        onboarding.PlanoInicial(
            categorias=[
                onboarding.CategoriaDesejada(
                    nome="Ações", percent_bp=10000, conta_no_patrimonio=True
                )
            ],
            investimentos="Ações",
        ),
        a_partir_de=SETEMBRO,
    )
    repo.create_income(
        session_vazia,
        on=SETEMBRO,
        description="Salário",
        type_=IncomeType.SALARIO,
        amount_cents=to_cents(1000),
    )
    budget.confirmar_separacao(session_vazia, SETEMBRO, criadas["Ações"])

    vista = cat.resolve_active(session_vazia, SETEMBRO)[0]
    saldo = budget.saldo_categoria(session_vazia, vista, SETEMBRO)
    assert saldo == to_cents(1000)

    antes = budget.get_patrimonio(session_vazia, mes(SETEMBRO)).total_cents
    assert antes == saldo

    repo.upsert_closing(
        session_vazia,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=saldo + to_cents(500),
        dividendos_cents=0,
    )
    depois = budget.get_patrimonio(session_vazia, mes(SETEMBRO))

    # Sobe o rendimento, e não o valor inteiro — que seria o sintoma da
    # dupla contagem.
    assert depois.total_cents == antes + to_cents(500)
