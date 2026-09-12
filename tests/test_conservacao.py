"""Nenhuma operação cria ou destrói dinheiro em silêncio.

Um aplicativo de finanças pode errar de duas formas: mostrar um número
errado, ou — bem pior — fazer dinheiro aparecer e sumir sem explicação.
Os testes daqui perseguem a segunda.

A conta que precisa fechar, sempre::

    recebido = separado + ainda não separado

    saldo de uma categoria =
        saldo inicial
        + separações confirmadas
        + transferências recebidas - enviadas
        + ajustes
        - gastos
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import ledger
from core import onboarding_service as onboarding
from core import repositories as repo
from core.models import AdjustmentKind, IncomeType, PaymentMethod
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, mes


def _plano_de_tres(session: Session) -> dict[str, int]:
    """Três categorias somando 100%, uma delas fora do patrimônio."""
    return onboarding.criar_plano_inicial(
        session,
        onboarding.PlanoInicial(
            categorias=[
                onboarding.CategoriaDesejada(
                    nome="Guardar", percent_bp=5000, conta_no_patrimonio=True
                ),
                onboarding.CategoriaDesejada(
                    nome="Planos", percent_bp=3000, conta_no_patrimonio=True
                ),
                onboarding.CategoriaDesejada(
                    nome="Solto", percent_bp=2000, conta_no_patrimonio=False
                ),
            ]
        ),
        a_partir_de=SETEMBRO,
    )


def _receber(session: Session, reais: float, quando=SETEMBRO) -> None:
    repo.create_income(
        session,
        on=quando,
        description="Salário",
        type_=IncomeType.SALARIO,
        amount_cents=to_cents(reais),
    )


def _gastar(session: Session, reais: float, categoria: int, parcelas: int = 1) -> None:
    repo.create_expense(
        session,
        purchase_date=SETEMBRO,
        description="Compra",
        category_id=categoria,
        total_cents=to_cents(reais),
        payment_method=PaymentMethod.CREDITO,
        installments_count=parcelas,
        first_installment_month=SETEMBRO,
    )


def _soma_dos_saldos(session: Session, quando=SETEMBRO) -> int:
    return sum(
        budget.saldo_categoria(session, vista, quando)
        for vista in cat.resolve_active(session, quando)
    )


# --------------------------------------------------------------------------
# O rateio não perde centavo
# --------------------------------------------------------------------------
def test_o_planejado_soma_exatamente_a_base(session_vazia: Session) -> None:
    """Com três fatias e um valor quebrado, a soma tem de bater no centavo."""
    _plano_de_tres(session_vazia)
    _receber(session_vazia, 2952.21)

    plano = budget.get_month_plan(session_vazia, SETEMBRO)

    assert sum(plano.planejado.values()) == plano.base_cents == to_cents(2952.21)


def test_o_rateio_fecha_para_muitos_valores_quebrados(
    session_vazia: Session,
) -> None:
    """Varre valores que costumam expor erro de arredondamento."""
    _plano_de_tres(session_vazia)

    for centavos in (1, 2, 7, 99, 100, 333, 100_001, 295_221, 999_999):
        vistas = cat.resolve_active(session_vazia, SETEMBRO)
        reparte = budget.distribuir(centavos, vistas)
        assert sum(reparte.values()) == centavos, (
            f"{centavos} centavos viraram {sum(reparte.values())}"
        )


def test_o_rateio_e_estavel_entre_chamadas(session_vazia: Session) -> None:
    """O mesmo plano tem de dar sempre o mesmo resultado.

    Se o desempate dependesse da ordem em que o banco devolveu as linhas,
    o centavo de sobra passearia entre categorias a cada clique.
    """
    _plano_de_tres(session_vazia)
    vistas = cat.resolve_active(session_vazia, SETEMBRO)

    primeiro = budget.distribuir(to_cents(1000.01), vistas)
    for _ in range(5):
        assert budget.distribuir(to_cents(1000.01), vistas) == primeiro


# --------------------------------------------------------------------------
# Recebido = separado + não separado
# --------------------------------------------------------------------------
def test_recebido_e_separado_mais_nao_separado(session_vazia: Session) -> None:
    """Todo centavo que entrou está num dos dois lados."""
    criadas = _plano_de_tres(session_vazia)
    _receber(session_vazia, 2952.21)

    budget.confirmar_separacao(session_vazia, SETEMBRO, criadas["Guardar"])

    plano = budget.get_month_plan(session_vazia, SETEMBRO)
    confirmado = sum(linha.separado_cents for linha in plano.separacoes)
    nao_separado = budget.nao_separado_no_mes(session_vazia, SETEMBRO)

    assert confirmado + nao_separado == plano.base_cents


def test_separar_tudo_zera_o_que_falta(session_vazia: Session) -> None:
    """Confirmando todas, não sobra centavo sem destino."""
    _plano_de_tres(session_vazia)
    _receber(session_vazia, 2952.21)

    for vista in cat.resolve_active(session_vazia, SETEMBRO):
        budget.confirmar_separacao(session_vazia, SETEMBRO, vista.id)

    assert budget.nao_separado_no_mes(session_vazia, SETEMBRO) == 0
    assert _soma_dos_saldos(session_vazia) == to_cents(2952.21)


# --------------------------------------------------------------------------
# O ciclo inteiro
# --------------------------------------------------------------------------
def test_ciclo_completo_explica_cada_centavo(session_vazia: Session) -> None:
    """Receita, separação, gasto parcelado, transferência, estorno e ajuste.

    Ao fim, a soma dos saldos tem de ser exatamente o que entrou, menos o
    que foi gasto, mais o que foi ajustado. Nada a mais, nada a menos.
    """
    criadas = _plano_de_tres(session_vazia)
    _receber(session_vazia, 3000.00)

    for vista in cat.resolve_active(session_vazia, SETEMBRO):
        budget.confirmar_separacao(session_vazia, SETEMBRO, vista.id)
    assert _soma_dos_saldos(session_vazia) == to_cents(3000)

    # Um gasto parcelado: só a parcela deste mês pesa agora.
    _gastar(session_vazia, 100.00, criadas["Solto"], parcelas=3)
    gasto_do_mes = repo.sum_expenses(session_vazia, mes(SETEMBRO))
    assert 0 < gasto_do_mes < to_cents(100), "só uma parcela caiu em setembro"
    assert _soma_dos_saldos(session_vazia) == to_cents(3000) - gasto_do_mes

    # Transferência não muda o total.
    antes = _soma_dos_saldos(session_vazia)
    budget.transferir(
        session_vazia,
        month=SETEMBRO,
        origem_id=criadas["Guardar"],
        destino_id=criadas["Planos"],
        valor_cents=to_cents(250),
    )
    assert _soma_dos_saldos(session_vazia) == antes

    # Estorno também não.
    original = repo.list_transferencias(session_vazia, mes(SETEMBRO))[0]
    budget.estornar_transferencia(session_vazia, original.id)
    assert _soma_dos_saldos(session_vazia) == antes

    # Ajuste muda o total exatamente pela diferença declarada.
    saldo_guardar = budget.saldo_categoria(
        session_vazia,
        next(v for v in cat.resolve_active(session_vazia, SETEMBRO) if v.name == "Guardar"),
        SETEMBRO,
    )
    budget.conferir_saldo(
        session_vazia,
        month=SETEMBRO,
        category_id=criadas["Guardar"],
        saldo_real_cents=saldo_guardar + to_cents(7.32),
        motivo=AdjustmentKind.RENDIMENTO,
    )
    assert _soma_dos_saldos(session_vazia) == antes + to_cents(7.32)


def test_parcelas_somam_exatamente_o_total(session_vazia: Session) -> None:
    """R$ 100 em 3x são 33,33 + 33,33 + 33,34 — nunca 99,99."""
    criadas = _plano_de_tres(session_vazia)
    _receber(session_vazia, 3000.00)
    _gastar(session_vazia, 100.00, criadas["Solto"], parcelas=3)

    parcelas = repo.gastos_por_mes_e_categoria(session_vazia)
    total = sum(
        valor for (_mes, cid), valor in parcelas.items() if cid == criadas["Solto"]
    )
    assert total == to_cents(100)


def test_saldo_bate_com_a_soma_dos_movimentos(session_vazia: Session) -> None:
    """A definição de saldo e o saldo calculado têm de concordar.

    Se divergissem, existiriam duas verdades sobre o mesmo dinheiro.
    """
    criadas = _plano_de_tres(session_vazia)
    _receber(session_vazia, 3000.00)
    for vista in cat.resolve_active(session_vazia, SETEMBRO):
        budget.confirmar_separacao(session_vazia, SETEMBRO, vista.id)
    _gastar(session_vazia, 120.00, criadas["Planos"])
    budget.transferir(
        session_vazia,
        month=SETEMBRO,
        origem_id=criadas["Guardar"],
        destino_id=criadas["Planos"],
        valor_cents=to_cents(80),
    )

    historico = ledger.obter(session_vazia, SETEMBRO)
    for vista in cat.resolve_active(session_vazia, SETEMBRO):
        movimento = historico.do_mes(SETEMBRO, vista.id)
        esperado = (
            movimento.inicial_cents
            + movimento.entrada_cents
            - movimento.gasto_cents
            + movimento.transferencia_cents
            + movimento.ajuste_cents
        )
        assert budget.saldo_categoria(session_vazia, vista, SETEMBRO) == esperado


def test_transferencias_se_anulam_no_total(session_vazia: Session) -> None:
    """O que sai de uma entra na outra: a soma global é sempre zero."""
    criadas = _plano_de_tres(session_vazia)
    _receber(session_vazia, 3000.00)
    for vista in cat.resolve_active(session_vazia, SETEMBRO):
        budget.confirmar_separacao(session_vazia, SETEMBRO, vista.id)

    for origem, destino, valor in (
        ("Guardar", "Planos", 100),
        ("Planos", "Solto", 40),
        ("Solto", "Guardar", 25),
    ):
        budget.transferir(
            session_vazia,
            month=SETEMBRO,
            origem_id=criadas[origem],
            destino_id=criadas[destino],
            valor_cents=to_cents(valor),
        )

    efeitos = repo.transferencias_por_mes_e_categoria(session_vazia)
    assert sum(efeitos.values()) == 0, "transferência não cria nem destrói dinheiro"


def test_dinheiro_atravessa_o_mes_sem_perda(session_vazia: Session) -> None:
    """O saldo de setembro é o ponto de partida de outubro, exatamente."""
    _plano_de_tres(session_vazia)
    _receber(session_vazia, 3000.00)
    for vista in cat.resolve_active(session_vazia, SETEMBRO):
        budget.confirmar_separacao(session_vazia, SETEMBRO, vista.id)

    em_setembro = _soma_dos_saldos(session_vazia, SETEMBRO)
    em_outubro = _soma_dos_saldos(session_vazia, OUTUBRO)

    assert em_outubro == em_setembro, "sem receita nova, nada muda na virada"
