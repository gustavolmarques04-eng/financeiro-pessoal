"""Dinheiro mudando de categoria: sobra enviada e estouro coberto."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from core import budget_service as budget
from core import repositories as repo
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, mes


def _saldo(session: Session, slug: str, cats: dict[str, int], quando=SETEMBRO) -> int:
    vista = next(
        v for v in budget.cat.resolve_all(session, quando) if v.slug == slug
    )
    return budget.saldo_categoria(session, vista, quando)


# --------------------------------------------------------------------------
# Enviar a sobra
# --------------------------------------------------------------------------
def test_transferencia_move_o_saldo_entre_as_duas_categorias(
    session: Session, receita, cats
) -> None:
    """Sai de uma, entra na outra, e o total guardado não muda."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])

    viagem_antes = _saldo(session, "viagem", cats)
    compras_antes = _saldo(session, "compras", cats)
    total_antes = budget.get_patrimonio(session, mes(SETEMBRO)).guardado_cents

    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["compras"],
        valor_cents=to_cents(100),
    )

    assert _saldo(session, "viagem", cats) == viagem_antes - to_cents(100)
    assert _saldo(session, "compras", cats) == compras_antes + to_cents(100)
    assert budget.get_patrimonio(session, mes(SETEMBRO)).guardado_cents == total_antes


def test_transferencia_atravessa_o_mes_seguinte(
    session: Session, receita, cats
) -> None:
    """O efeito não é só do mês: o saldo segue mudado daí para a frente."""
    receita(2952.21, mes=SETEMBRO)
    receita(2952.21, mes=OUTUBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["compras"],
        valor_cents=to_cents(50),
    )

    em_outubro = _saldo(session, "compras", cats, OUTUBRO)
    sem_transferencia = budget.get_month_plan(session, OUTUBRO)
    assert em_outubro >= to_cents(50), (
        "o dinheiro transferido em setembro tem de continuar lá em outubro"
    )
    assert sem_transferencia.month == OUTUBRO


def test_transferencia_recusa_destino_igual_a_origem(
    session: Session, cats
) -> None:
    """Mover dinheiro para a própria categoria não é um movimento."""
    with pytest.raises(budget.TransferenciaInvalida):
        budget.transferir(
            session,
            month=SETEMBRO,
            origem_id=cats["viagem"],
            destino_id=cats["viagem"],
            valor_cents=to_cents(10),
        )


def test_transferencia_recusa_valor_nao_positivo(session: Session, cats) -> None:
    """Zero ou negativo não é transferência."""
    for valor in (0, -100):
        with pytest.raises(budget.TransferenciaInvalida):
            budget.transferir(
                session,
                month=SETEMBRO,
                origem_id=cats["viagem"],
                destino_id=cats["compras"],
                valor_cents=valor,
            )


# --------------------------------------------------------------------------
# Cobrir o estouro
# --------------------------------------------------------------------------
def test_cobertura_zera_o_negativo_da_categoria(
    session: Session, receita, gasto, cats
) -> None:
    """Coberto o estouro, a categoria volta ao zero em vez de ficar devendo."""
    receita(1000.00, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    orcado = budget.get_month_plan(session, SETEMBRO).planejado.get(cats["livre"], 0)
    excesso = to_cents(40)
    gasto(float(orcado + excesso) / 100, cats["livre"], mes=SETEMBRO)

    assert _saldo(session, "livre", cats) == -excesso

    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["livre"],
        valor_cents=excesso,
    )

    assert _saldo(session, "livre", cats) == 0


def test_fontes_para_cobrir_lista_so_quem_tem_saldo(
    session: Session, receita, cats
) -> None:
    """A pergunta 'de onde sai?' não oferece categoria sem dinheiro."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    fontes = budget.fontes_para_cobrir(
        session, SETEMBRO, excluindo=cats["livre"], minimo_cents=to_cents(100)
    )
    ids = {v.id for v, _ in fontes}

    assert cats["livre"] not in ids, "a própria categoria nunca se cobre"
    for _vista, saldo in fontes:
        assert saldo >= to_cents(100)
    assert fontes == sorted(fontes, key=lambda par: par[1], reverse=True), (
        "as de maior folga vêm primeiro"
    )


def test_estouro_sem_cobertura_segue_negativo_para_o_mes_seguinte(
    session: Session, receita, gasto, cats
) -> None:
    """Deixar negativo é resposta válida, e a dívida atravessa o mês."""
    receita(1000.00, mes=SETEMBRO)
    receita(1000.00, mes=OUTUBRO)

    orcado = budget.get_month_plan(session, SETEMBRO).planejado.get(cats["livre"], 0)
    gasto(float(orcado + to_cents(30)) / 100, cats["livre"], mes=SETEMBRO)

    outubro = next(
        linha
        for linha in budget.get_month_plan(session, OUTUBRO).gastos
        if linha.categoria.slug == "livre"
    )

    assert outubro.vem_de_antes_cents == -to_cents(30)


def test_apagar_o_gasto_desfaz_a_cobertura(
    session: Session, receita, gasto, cats
) -> None:
    """Sem isso, cobrir e depois apagar o gasto criaria dinheiro do nada."""
    receita(1000.00, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    orcado = budget.get_month_plan(session, SETEMBRO).planejado.get(cats["livre"], 0)
    excesso = to_cents(40)
    compra = gasto(float(orcado + excesso) / 100, cats["livre"], mes=SETEMBRO)

    budget.transferir(
        session,
        month=SETEMBRO,
        origem_id=cats["viagem"],
        destino_id=cats["livre"],
        valor_cents=excesso,
        expense_id=compra.id,
    )
    viagem_coberta = _saldo(session, "viagem", cats)

    repo.delete_expense(session, compra.id)

    assert _saldo(session, "viagem", cats) == viagem_coberta + excesso, (
        "o dinheiro emprestado tem de voltar para quem cobriu"
    )
    assert _saldo(session, "livre", cats) == orcado
