"""Testes de patrimônio, investimentos e consistência do fluxo completo."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core import budget_service as budget
from core import categories as cat
from core import investment_service as investimentos
from core import repositories as repo
from core.models import IncomeType
from core.utils import to_cents

from .conftest import OUTUBRO, SETEMBRO, mes


# --------------------------------------------------------------------------
# 11 — patrimônio não duplica
# --------------------------------------------------------------------------
def test_patrimonio_soma_as_quatro_parcelas(session: Session, receita, cats) -> None:
    """Patrimônio = reserva + investimentos + envelopes, sem repetir nada."""
    repo.set_opening_balance(session, cats["compras"], 0)
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=to_cents(1000),
        dividendos_cents=0,
    )

    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))

    assert patrimonio.valor_de("reserva") == to_cents(1774.88)
    assert patrimonio.valor_de("independencia") == to_cents(1000)
    assert patrimonio.valor_de("viagem") == to_cents(413.31)
    assert patrimonio.valor_de("compras") == to_cents(265.70)
    assert patrimonio.total_cents == (
        to_cents(1774.88) + to_cents(1000) + to_cents(413.31) + to_cents(265.70)
    )


def test_separacao_anterior_ao_fechamento_nao_conta_duas_vezes(
    session: Session, receita, cats
) -> None:
    """Se você separou e só depois informou o saldo, o informado manda.

    O número digitado no fechamento já reflete a transferência — somar a
    separação por cima contaria o mesmo dinheiro duas vezes.
    """
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])

    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=to_cents(1446.58),
        dividendos_cents=0,
    )

    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    assert patrimonio.valor_de("independencia") == to_cents(1446.58)


def test_separacao_posterior_ao_fechamento_soma_ao_informado(
    session: Session, receita, cats
) -> None:
    """Separar depois de informar o saldo soma ao que já estava guardado.

    É o caso do dia a dia: você confere a caixinha, digita o valor e só
    então marca a separação do mês.
    """
    receita(2952.21, mes=SETEMBRO)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1273.79),
        investimentos_cents=0,
        dividendos_cents=0,
    )

    antes = budget.get_patrimonio(session, mes(SETEMBRO)).valor_de("reserva")
    assert antes == to_cents(1273.79)

    budget.confirmar_separacao(session, SETEMBRO, cats["reserva"])

    depois = budget.get_patrimonio(session, mes(SETEMBRO)).valor_de("reserva")
    assert depois == to_cents(1273.79) + to_cents(501.88)


def test_separacao_de_mes_posterior_sempre_soma(
    session: Session, receita, cats
) -> None:
    """Uma separação de outubro nunca cabe no fechamento de setembro."""
    receita(2952.21, mes=SETEMBRO)
    receita(2952.21, mes=OUTUBRO)
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1000),
        investimentos_cents=0,
        dividendos_cents=0,
    )
    budget.confirmar_separacao(session, OUTUBRO, cats["reserva"])

    assert budget.get_patrimonio(session, mes(SETEMBRO)).valor_de("reserva") == (
        to_cents(1000)
    )
    assert budget.get_patrimonio(session, mes(OUTUBRO)).valor_de("reserva") == (
        to_cents(1000) + to_cents(501.88)
    )


def test_novo_fechamento_reconcilia_o_saldo(session: Session, receita, cats) -> None:
    """Informar o saldo de novo zera o acúmulo e volta a valer o número real."""
    receita(2952.21, mes=SETEMBRO)
    repo.upsert_closing(
        session, SETEMBRO, reserva_cents=to_cents(1273.79),
        investimentos_cents=0, dividendos_cents=0,
    )
    budget.confirmar_separacao(session, SETEMBRO, cats["reserva"])
    assert budget.get_patrimonio(session, mes(SETEMBRO)).valor_de("reserva") == (
        to_cents(1273.79) + to_cents(501.88)
    )

    # No mês seguinte você confere a conta e digita o valor real.
    repo.upsert_closing(
        session, OUTUBRO, reserva_cents=to_cents(1800),
        investimentos_cents=0, dividendos_cents=0,
    )
    assert budget.get_patrimonio(session, mes(OUTUBRO)).valor_de("reserva") == (
        to_cents(1800)
    )


def test_patrimonio_sem_fechamento_nao_inventa_valor(
    session: Session, cats
) -> None:
    """Sem fechamento informado, reserva e investimentos ficam zerados."""
    repo.set_opening_balance(session, cats["compras"], 0)
    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    assert patrimonio.informado is False
    assert patrimonio.valor_de("reserva") == 0
    assert patrimonio.valor_de("independencia") == 0
    assert patrimonio.total_cents == 0


def test_semente_traz_o_saldo_inicial_de_compras(session: Session) -> None:
    """As categorias iniciais já nascem com os R$ 5,54 do envelope Compras."""
    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    assert patrimonio.valor_de("compras") == to_cents(5.54)


def test_patrimonio_inclui_envelope_novo_automaticamente(
    session: Session, receita, cats
) -> None:
    """Uma categoria nova marcada para patrimônio passa a contar sozinha."""
    from core.models import CategoryBehavior

    nova = cat.criar_categoria(
        session,
        name="Reforma",
        behavior=CategoryBehavior.ACCUMULATING_ENVELOPE,
        percent_bp=0,
        effective_month=SETEMBRO,
        include_in_net_worth=True,
    )
    repo.set_opening_balance(session, nova.id, to_cents(300))

    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    assert patrimonio.valor_de("reforma") == to_cents(300)
    assert to_cents(300) <= patrimonio.total_cents


# --------------------------------------------------------------------------
# Investimentos e dividendos
# --------------------------------------------------------------------------
def test_capital_destinado_usa_a_propriedade_da_categoria(
    session: Session, receita, cats
) -> None:
    """Capital destinado soma as categorias marcadas, não um nome."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    receita(2952.21, mes=OUTUBRO)
    budget.confirmar_separacao(session, OUTUBRO, cats["independencia"])

    assert investimentos.capital_destinado(session, SETEMBRO) == to_cents(1446.58)
    assert investimentos.capital_destinado(session, OUTUBRO) == to_cents(1446.58) * 2

    marcadas = investimentos.categorias_de_investimento(session, SETEMBRO)
    assert [v.slug for v in marcadas] == ["independencia"]
    assert all(v.counts_as_investment_capital for v in marcadas)


def test_capital_ignora_categoria_renomeada_mas_segue_a_flag(
    session: Session, receita, cats
) -> None:
    """Trocar a flag muda o capital; trocar o nome não muda nada."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    cat.upsert_version(session, cats["independencia"], SETEMBRO, name="Aposentadoria")
    assert investimentos.capital_destinado(session, SETEMBRO) == to_cents(1446.58)

    cat.upsert_version(
        session, cats["viagem"], SETEMBRO, counts_as_investment_capital=True
    )
    assert investimentos.capital_destinado(session, SETEMBRO) == to_cents(
        1446.58
    ) + to_cents(413.31)


def test_resultado_dos_investimentos_pode_ser_negativo(
    session: Session, receita, cats
) -> None:
    """Valor atual abaixo do capital destinado gera resultado negativo."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=to_cents(1300),
        dividendos_cents=0,
    )

    resumo = investimentos.get_resumo(session, mes(SETEMBRO))
    assert resumo.capital_destinado_cents == to_cents(1446.58)
    assert resumo.resultado_cents == to_cents(1300) - to_cents(1446.58)
    assert resumo.resultado_cents < 0


def test_investimento_sem_valor_informado_nao_mostra_prejuizo(
    session: Session, receita, cats
) -> None:
    """Separar sem informar a carteira não vira perda.

    Antes desta regra o painel mostrava -100%, o que assustava sem motivo.
    """
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=0,
        dividendos_cents=0,
    )

    resumo = investimentos.get_resumo(session, mes(SETEMBRO))
    assert resumo.informado is False
    assert resumo.capital_destinado_cents == to_cents(1446.58)
    assert resumo.resultado_cents == 0
    assert resumo.rentabilidade_pct == 0.0
    assert resumo.rentabilidade_valida is False


def test_dividendos_ficam_fora_do_resultado(session: Session, receita, cats) -> None:
    """Dividendos são informativos e não mexem no resultado estimado."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["independencia"])
    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=0,
        investimentos_cents=to_cents(1500),
        dividendos_cents=to_cents(80),
    )

    resumo = investimentos.get_resumo(session, mes(SETEMBRO))
    assert resumo.resultado_cents == to_cents(1500) - to_cents(1446.58)
    assert investimentos.dividendos_do_periodo(session, mes(SETEMBRO)) == to_cents(80)
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
    assert budget.get_patrimonio(session, mes(SETEMBRO)).valor_de("reserva") == to_cents(
        1774.88
    )


# --------------------------------------------------------------------------
# Fluxo completo: receita → plano → separação → gasto → fechamento
# --------------------------------------------------------------------------
def test_fluxo_completo_permanece_consistente(
    session: Session, receita, gasto, cats
) -> None:
    """Percorre o ciclo inteiro conferindo que tudo bate ponta a ponta."""
    repo.set_opening_balance(session, cats["compras"], to_cents(5.54))

    receita(1775.94, descricao="Primeiro salário")
    receita(700.00, descricao="VA/VR")
    receita(476.27, tipo=IncomeType.SALDO_INICIAL, descricao="Saldo anterior Rico")

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.recebido_cents == to_cents(2475.94)
    assert plano.base_cents == to_cents(2952.21)
    assert plano.total_planejado_cents == plano.base_cents

    for slug in ("independencia", "reserva", "viagem", "compras"):
        budget.confirmar_separacao(session, SETEMBRO, cats[slug])

    plano = budget.get_month_plan(session, SETEMBRO)
    assert plano.total_falta_separar_cents == 0
    assert all(linha.feito for linha in plano.separacoes)

    gasto(150.00, cats["namorada"], mes=SETEMBRO)
    plano = budget.get_month_plan(session, SETEMBRO)
    namorada = next(g for g in plano.gastos if g.categoria.slug == "namorada")
    assert namorada.disponivel_cents == to_cents(206.65) - to_cents(150)
    assert plano.gasto_cents == to_cents(150)

    repo.upsert_closing(
        session,
        SETEMBRO,
        reserva_cents=to_cents(1774.88),
        investimentos_cents=to_cents(1446.58),
        dividendos_cents=0,
    )
    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    assert patrimonio.total_cents == (
        to_cents(1774.88) + to_cents(1446.58) + to_cents(413.31) + to_cents(265.70 + 5.54)
    )

    resumo = investimentos.get_resumo(session, mes(SETEMBRO))
    assert resumo.capital_destinado_cents == to_cents(1446.58)
    assert resumo.resultado_cents == 0


# --------------------------------------------------------------------------
# Dinheiro guardado x patrimônio
# --------------------------------------------------------------------------
def test_guardado_soma_tudo_e_patrimonio_so_o_marcado(
    session: Session, receita, cats
) -> None:
    """Os dois números saem da mesma leitura e diferem pelo que foi marcado.

    "Guardado" é todo dinheiro separado; "patrimônio" é a parte que não tem
    destino certo de saída. Tirar Viagem do patrimônio não pode fazer o
    dinheiro sumir do guardado.
    """
    repo.set_opening_balance(session, cats["compras"], 0)
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    budget.confirmar_separacao(session, SETEMBRO, cats["compras"])

    antes = budget.get_patrimonio(session, mes(SETEMBRO))
    assert antes.guardado_cents == antes.total_cents, "nada excluído ainda"
    assert antes.comprometido_cents == 0

    cat.upsert_version(
        session, cats["viagem"], SETEMBRO, include_in_net_worth=False
    )
    depois = budget.get_patrimonio(session, mes(SETEMBRO))

    viagem = antes.valor_de("viagem")
    assert viagem > 0, "o teste só vale se houver dinheiro na viagem"
    assert depois.guardado_cents == antes.guardado_cents, "o dinheiro continua lá"
    assert depois.total_cents == antes.total_cents - viagem
    assert depois.comprometido_cents == viagem
    assert depois.valor_de("viagem") == viagem, "a parcela não some da lista"


def test_categoria_fora_do_patrimonio_nao_entra_nas_parcelas_do_patrimonio(
    session: Session, receita, cats
) -> None:
    """A lista usada pelos gráficos respeita a marcação."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    cat.upsert_version(
        session, cats["viagem"], SETEMBRO, include_in_net_worth=False
    )

    patrimonio = budget.get_patrimonio(session, mes(SETEMBRO))
    slugs = {p.categoria.slug for p in patrimonio.parcelas_no_patrimonio}

    assert "viagem" not in slugs
    assert "reserva" in slugs
    assert sum(p.valor_cents for p in patrimonio.parcelas_no_patrimonio) == (
        patrimonio.total_cents
    )


def test_gasto_em_categoria_de_consumo_nao_mexe_no_guardado(
    session: Session, receita, gasto, cats
) -> None:
    """Gasto em orçamento mensal não é dinheiro guardado que saiu."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])
    antes = budget.get_patrimonio(session, mes(SETEMBRO))

    gasto(85.10, cats["livre"], mes=SETEMBRO)
    depois = budget.get_patrimonio(session, mes(SETEMBRO))

    assert depois.guardado_cents == antes.guardado_cents
    assert depois.total_cents == antes.total_cents


# --------------------------------------------------------------------------
# Todo o dinheiro (guardado + sobra do orçamento do mês)
# --------------------------------------------------------------------------
def test_gasto_no_orcamento_mensal_reduz_o_dinheiro_total(
    session: Session, receita, gasto, cats
) -> None:
    """Gastar do orçamento do mês tem de aparecer no total.

    Era o furo: "Livre" não separa dinheiro, então ficava fora do guardado
    e gastar dele não mexia em número nenhum.
    """
    receita(2952.21, mes=SETEMBRO)
    antes = budget.get_resumo_periodo(session, mes(SETEMBRO))

    gasto(85.10, cats["livre"], mes=SETEMBRO)
    depois = budget.get_resumo_periodo(session, mes(SETEMBRO))

    assert depois.dinheiro_total_cents == antes.dinheiro_total_cents - to_cents(85.10)
    assert depois.patrimonio.guardado_cents == antes.patrimonio.guardado_cents, (
        "gasto de consumo não sai do dinheiro separado"
    )


def test_dinheiro_total_e_guardado_mais_disponivel(
    session: Session, receita, cats
) -> None:
    """O total é a soma exata das duas partes que a Home mostra."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    resumo = budget.get_resumo_periodo(session, mes(SETEMBRO))

    assert resumo.dinheiro_total_cents == (
        resumo.patrimonio.guardado_cents + resumo.disponivel_cents
    )


def test_disponivel_nao_soma_categorias_que_acumulam(
    session: Session, receita, cats
) -> None:
    """A sobra do mês olha só o orçamento de consumo, senão conta duas vezes."""
    receita(2952.21, mes=SETEMBRO)
    budget.confirmar_separacao(session, SETEMBRO, cats["viagem"])

    disponivel = budget.disponivel_no_mes(session, SETEMBRO)
    guardado = budget.get_patrimonio(session, mes(SETEMBRO)).guardado_cents

    assert disponivel > 0
    assert disponivel + guardado <= to_cents(2952.21) + guardado, "sem inventar dinheiro"
    plano = budget.get_month_plan(session, SETEMBRO)
    consumo = sum(
        plano.planejado.get(v.id, 0)
        for v in plano.categorias
        if v.behavior.is_monthly_budget
    )
    assert disponivel == consumo, "nada foi gasto ainda"
