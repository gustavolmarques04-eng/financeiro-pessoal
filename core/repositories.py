"""Acesso a dados: consultas e escritas, sem nenhuma regra financeira.

As regras vivem em :mod:`core.budget_service` e :mod:`core.categories`.
Aqui só há SQL/ORM. As funções que alteram várias tabelas (compras
parceladas, versões de categoria) recebem a ``Session`` de fora, para
participarem da transação de quem chama.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from . import categories as cat
from .models import (
    AllocationState,
    CategoryVersion,
    Expense,
    ExpenseInstallment,
    Income,
    IncomeType,
    MonthlyClosing,
    MonthRevision,
    OpeningBalance,
    PaymentMethod,
)
from .period import Period
from .utils import add_months, month_start, split_installments


# --------------------------------------------------------------------------
# Revisões do plano do mês
# --------------------------------------------------------------------------
def get_revision(session: Session, month: date) -> int:
    """Revisão atual do plano do mês (1 quando ainda não houve alteração)."""
    registro = session.get(MonthRevision, month_start(month))
    return registro.revision if registro else 1


def bump_revision(session: Session, month: date) -> int:
    """Incrementa a revisão do mês e devolve o novo número.

    Chamado sempre que o **plano** do mês muda: receita criada, alterada ou
    excluída, e configuração de categoria que passa a valer para o mês.
    """
    alvo = month_start(month)
    registro = session.get(MonthRevision, alvo)
    if registro is None:
        registro = MonthRevision(month=alvo, revision=2)
        session.add(registro)
    else:
        registro.revision += 1
    session.flush()
    return registro.revision


def months_with_data_from(session: Session, month: date) -> list[date]:
    """Meses com dados a partir de um mês (inclusive)."""
    alvo = month_start(month)
    meses = {alvo}
    meses.update(session.scalars(select(Income.month).where(Income.month >= alvo)))
    meses.update(
        session.scalars(select(AllocationState.month).where(AllocationState.month >= alvo))
    )
    meses.update(
        session.scalars(
            select(ExpenseInstallment.month).where(ExpenseInstallment.month >= alvo)
        )
    )
    return sorted(meses)


def bump_revisions_from(session: Session, month: date) -> list[date]:
    """Sobe a revisão do mês informado e dos meses posteriores com dados.

    Usado quando a configuração muda: só os meses realmente afetados são
    invalidados — nada anterior é tocado.
    """
    meses = months_with_data_from(session, month)
    for mes in meses:
        bump_revision(session, mes)
    return meses


# --------------------------------------------------------------------------
# Receitas
# --------------------------------------------------------------------------
def list_incomes(session: Session, period: Period) -> list[Income]:
    """Receitas do período, mais recentes primeiro."""
    return list(
        session.scalars(
            select(Income)
            .where(Income.month.in_(period.months))
            .order_by(Income.date.desc(), Income.id.desc())
        )
    )


def get_income(session: Session, income_id: int) -> Income | None:
    """Uma receita pelo id."""
    return session.get(Income, income_id)


def create_income(
    session: Session,
    *,
    on: date,
    description: str,
    type_: IncomeType,
    amount_cents: int,
    counts_in_budget: bool = True,
    note: str | None = None,
) -> Income:
    """Insere uma receita e invalida o plano do mês correspondente."""
    receita = Income(
        date=on,
        month=month_start(on),
        description=description.strip(),
        type=type_,
        amount_cents=amount_cents,
        counts_in_budget=counts_in_budget,
        note=note,
    )
    session.add(receita)
    session.flush()
    bump_revision(session, receita.month)
    return receita


def update_income(
    session: Session,
    income_id: int,
    *,
    on: date,
    description: str,
    type_: IncomeType,
    amount_cents: int,
    counts_in_budget: bool,
    note: str | None = None,
) -> Income:
    """Atualiza uma receita, invalidando o mês de origem e o de destino."""
    receita = session.get(Income, income_id)
    if receita is None:
        raise ValueError(f"Receita {income_id} não encontrada.")

    mes_antigo = receita.month
    receita.date = on
    receita.month = month_start(on)
    receita.description = description.strip()
    receita.type = type_
    receita.amount_cents = amount_cents
    receita.counts_in_budget = counts_in_budget
    receita.note = note
    session.flush()

    for mes in {mes_antigo, receita.month}:
        bump_revision(session, mes)
    return receita


def delete_income(session: Session, income_id: int) -> None:
    """Remove uma receita e invalida o plano do mês."""
    receita = session.get(Income, income_id)
    if receita is None:
        return
    mes = receita.month
    session.delete(receita)
    session.flush()
    bump_revision(session, mes)


def sum_incomes(
    session: Session,
    period: Period,
    *,
    only_budget: bool = False,
    only_renda: bool = False,
) -> int:
    """Soma das receitas do período, em centavos.

    ``only_renda`` exclui ``Saldo inicial`` (dinheiro que já existia);
    ``only_budget`` mantém apenas o que entra na base de distribuição.
    """
    consulta = select(func.coalesce(func.sum(Income.amount_cents), 0)).where(
        Income.month.in_(period.months)
    )
    if only_budget:
        consulta = consulta.where(Income.counts_in_budget.is_(True))
    if only_renda:
        consulta = consulta.where(Income.type != IncomeType.SALDO_INICIAL)
    return int(session.scalar(consulta) or 0)


def incomes_by_month(session: Session, period: Period, *, only_renda: bool = True) -> dict[date, int]:
    """Receita de cada mês do período, para os gráficos anuais."""
    consulta = (
        select(Income.month, func.sum(Income.amount_cents))
        .where(Income.month.in_(period.months))
        .group_by(Income.month)
    )
    if only_renda:
        consulta = consulta.where(Income.type != IncomeType.SALDO_INICIAL)
    encontrados = {mes: int(total or 0) for mes, total in session.execute(consulta)}
    return {mes: encontrados.get(mes, 0) for mes in period.months}


def months_with_incomes(session: Session) -> list[date]:
    """Meses que possuem alguma receita registrada."""
    return sorted(set(session.scalars(select(Income.month))))


# --------------------------------------------------------------------------
# Gastos e parcelas
# --------------------------------------------------------------------------
def _rebuild_installments(session: Session, expense: Expense) -> None:
    """Regera as parcelas de uma compra a partir dos seus campos atuais."""
    session.execute(
        delete(ExpenseInstallment).where(ExpenseInstallment.expense_id == expense.id)
    )
    valores = split_installments(expense.total_cents, expense.installments_count)
    inicio = month_start(expense.first_installment_month)
    for indice, valor in enumerate(valores):
        session.add(
            ExpenseInstallment(
                expense_id=expense.id,
                number=indice + 1,
                month=add_months(inicio, indice),
                amount_cents=valor,
            )
        )
    session.flush()


def create_expense(
    session: Session,
    *,
    purchase_date: date,
    description: str,
    category_id: int,
    total_cents: int,
    payment_method: PaymentMethod,
    installments_count: int = 1,
    first_installment_month: date | None = None,
    note: str | None = None,
) -> Expense:
    """Insere uma compra e gera todas as suas parcelas na mesma transação."""
    if installments_count < 1:
        raise ValueError("Número de parcelas deve ser >= 1.")

    gasto = Expense(
        purchase_date=purchase_date,
        description=description.strip(),
        category_id=category_id,
        total_cents=total_cents,
        payment_method=payment_method,
        installments_count=installments_count,
        first_installment_month=month_start(first_installment_month or purchase_date),
        note=note,
    )
    session.add(gasto)
    session.flush()
    _rebuild_installments(session, gasto)
    return gasto


def update_expense(
    session: Session,
    expense_id: int,
    *,
    purchase_date: date,
    description: str,
    category_id: int,
    total_cents: int,
    payment_method: PaymentMethod,
    installments_count: int,
    first_installment_month: date | None = None,
    note: str | None = None,
) -> Expense:
    """Atualiza uma compra e recalcula as parcelas do zero."""
    gasto = session.get(Expense, expense_id)
    if gasto is None:
        raise ValueError(f"Gasto {expense_id} não encontrado.")
    if installments_count < 1:
        raise ValueError("Número de parcelas deve ser >= 1.")

    gasto.purchase_date = purchase_date
    gasto.description = description.strip()
    gasto.category_id = category_id
    gasto.total_cents = total_cents
    gasto.payment_method = payment_method
    gasto.installments_count = installments_count
    gasto.first_installment_month = month_start(first_installment_month or purchase_date)
    gasto.note = note
    session.flush()
    _rebuild_installments(session, gasto)
    return gasto


def delete_expense(session: Session, expense_id: int) -> None:
    """Remove a compra e, por cascata, todas as suas parcelas."""
    gasto = session.get(Expense, expense_id)
    if gasto is None:
        return
    session.delete(gasto)
    session.flush()


def get_expense(session: Session, expense_id: int) -> Expense | None:
    """Uma compra pelo id, já com as parcelas carregadas."""
    return session.scalar(
        select(Expense)
        .where(Expense.id == expense_id)
        .options(selectinload(Expense.installments))
    )


def list_installments(
    session: Session, period: Period, *, category_id: int | None = None
) -> list[tuple[ExpenseInstallment, Expense]]:
    """Parcelas que caem no período, com a compra de origem."""
    consulta = (
        select(ExpenseInstallment, Expense)
        .join(Expense, ExpenseInstallment.expense_id == Expense.id)
        .where(ExpenseInstallment.month.in_(period.months))
        .order_by(Expense.purchase_date.desc(), Expense.id.desc())
    )
    if category_id is not None:
        consulta = consulta.where(Expense.category_id == category_id)
    return [(parcela, gasto) for parcela, gasto in session.execute(consulta)]


def sum_expenses(
    session: Session, period: Period, *, category_id: int | None = None
) -> int:
    """Total gasto no período (somando parcelas), opcionalmente por categoria."""
    consulta = (
        select(func.coalesce(func.sum(ExpenseInstallment.amount_cents), 0))
        .join(Expense, ExpenseInstallment.expense_id == Expense.id)
        .where(ExpenseInstallment.month.in_(period.months))
    )
    if category_id is not None:
        consulta = consulta.where(Expense.category_id == category_id)
    return int(session.scalar(consulta) or 0)


def expenses_by_month(session: Session, period: Period) -> dict[date, int]:
    """Gasto de cada mês do período, para os gráficos anuais."""
    consulta = (
        select(ExpenseInstallment.month, func.sum(ExpenseInstallment.amount_cents))
        .where(ExpenseInstallment.month.in_(period.months))
        .group_by(ExpenseInstallment.month)
    )
    encontrados = {mes: int(total or 0) for mes, total in session.execute(consulta)}
    return {mes: encontrados.get(mes, 0) for mes in period.months}


def sum_expenses_until(session: Session, month: date, category_id: int) -> int:
    """Total gasto na categoria até o fim do mês (para envelopes acumulativos)."""
    consulta = (
        select(func.coalesce(func.sum(ExpenseInstallment.amount_cents), 0))
        .join(Expense, ExpenseInstallment.expense_id == Expense.id)
        .where(ExpenseInstallment.month <= month_start(month))
        .where(Expense.category_id == category_id)
    )
    return int(session.scalar(consulta) or 0)


def future_installments(session: Session, after_month: date) -> list[tuple[date, int]]:
    """Compromissos já assumidos: total de parcelas por mês futuro."""
    consulta = (
        select(ExpenseInstallment.month, func.sum(ExpenseInstallment.amount_cents))
        .where(ExpenseInstallment.month > month_start(after_month))
        .group_by(ExpenseInstallment.month)
        .order_by(ExpenseInstallment.month)
    )
    return [(mes, int(total or 0)) for mes, total in session.execute(consulta)]


def months_with_expenses(session: Session) -> list[date]:
    """Meses que possuem alguma parcela."""
    return sorted(set(session.scalars(select(ExpenseInstallment.month))))


# --------------------------------------------------------------------------
# Separações confirmadas
# --------------------------------------------------------------------------
def get_allocation(
    session: Session, month: date, category_id: int
) -> AllocationState | None:
    """Estado de separação de uma categoria no mês."""
    return session.scalar(
        select(AllocationState)
        .where(AllocationState.month == month_start(month))
        .where(AllocationState.category_id == category_id)
    )


def set_allocation(
    session: Session,
    month: date,
    category_id: int,
    *,
    separated_cents: int,
    revision: int,
) -> AllocationState:
    """Grava quanto já foi separado, junto da revisão do plano confirmada."""
    if separated_cents < 0:
        raise ValueError("Valor separado não pode ser negativo.")

    estado = get_allocation(session, month, category_id)
    if estado is None:
        estado = AllocationState(month=month_start(month), category_id=category_id)
        session.add(estado)

    estado.separated_cents = separated_cents
    estado.confirmed_revision = revision
    estado.confirmed_at = datetime.now(timezone.utc) if separated_cents else None
    session.flush()
    return estado


def sum_allocations_until(session: Session, month: date, category_id: int) -> int:
    """Total já separado para a categoria até o fim do mês."""
    consulta = (
        select(func.coalesce(func.sum(AllocationState.separated_cents), 0))
        .where(AllocationState.month <= month_start(month))
        .where(AllocationState.category_id == category_id)
    )
    return int(session.scalar(consulta) or 0)


def months_with_allocations(session: Session) -> list[date]:
    """Meses que possuem alguma separação registrada."""
    return sorted(set(session.scalars(select(AllocationState.month))))


# --------------------------------------------------------------------------
# Fechamentos e saldos iniciais
# --------------------------------------------------------------------------
def get_closing(session: Session, month: date) -> MonthlyClosing | None:
    """Fechamento informado para o mês, se existir."""
    return session.get(MonthlyClosing, month_start(month))


def latest_closing_until(session: Session, month: date) -> MonthlyClosing | None:
    """Fechamento mais recente até o mês (inclusive)."""
    return session.scalar(
        select(MonthlyClosing)
        .where(MonthlyClosing.month <= month_start(month))
        .order_by(MonthlyClosing.month.desc())
        .limit(1)
    )


def latest_closing_in(session: Session, period: Period) -> MonthlyClosing | None:
    """Fechamento mais recente dentro do período.

    No modo anual é o que dá o patrimônio do ano: a posição mais nova
    informada naquele ano, nunca a soma dos meses.
    """
    return session.scalar(
        select(MonthlyClosing)
        .where(MonthlyClosing.month.in_(period.months))
        .order_by(MonthlyClosing.month.desc())
        .limit(1)
    )


def upsert_closing(
    session: Session,
    month: date,
    *,
    reserva_cents: int,
    investimentos_cents: int,
    dividendos_cents: int,
    note: str | None = None,
) -> MonthlyClosing:
    """Cria ou atualiza o fechamento de um mês."""
    alvo = month_start(month)
    fechamento = session.get(MonthlyClosing, alvo)
    if fechamento is None:
        fechamento = MonthlyClosing(month=alvo)
        session.add(fechamento)

    fechamento.reserva_cents = reserva_cents
    fechamento.investimentos_cents = investimentos_cents
    fechamento.dividendos_cents = dividendos_cents
    fechamento.note = note
    session.flush()
    return fechamento


def delete_closing(session: Session, month: date) -> None:
    """Remove o fechamento de um mês."""
    fechamento = session.get(MonthlyClosing, month_start(month))
    if fechamento is not None:
        session.delete(fechamento)
        session.flush()


def list_closings(session: Session) -> list[MonthlyClosing]:
    """Todos os fechamentos, do mais antigo para o mais novo."""
    return list(session.scalars(select(MonthlyClosing).order_by(MonthlyClosing.month)))


def sum_dividends(session: Session, period: Period) -> int:
    """Dividendos informados dentro do período."""
    consulta = select(func.coalesce(func.sum(MonthlyClosing.dividendos_cents), 0)).where(
        MonthlyClosing.month.in_(period.months)
    )
    return int(session.scalar(consulta) or 0)


def dividends_by_month(session: Session, period: Period) -> dict[date, int]:
    """Dividendos de cada mês do período."""
    consulta = select(MonthlyClosing.month, MonthlyClosing.dividendos_cents).where(
        MonthlyClosing.month.in_(period.months)
    )
    encontrados = {mes: int(valor or 0) for mes, valor in session.execute(consulta)}
    return {mes: encontrados.get(mes, 0) for mes in period.months}


def get_opening_balance(session: Session, category_id: int) -> int:
    """Saldo inicial do envelope, em centavos."""
    registro = session.get(OpeningBalance, category_id)
    return registro.amount_cents if registro else 0


def set_opening_balance(
    session: Session, category_id: int, amount_cents: int, note: str | None = None
) -> OpeningBalance:
    """Define o saldo inicial de um envelope."""
    registro = session.get(OpeningBalance, category_id)
    if registro is None:
        registro = OpeningBalance(
            category_id=category_id, amount_cents=amount_cents, note=note
        )
        session.add(registro)
    else:
        registro.amount_cents = amount_cents
        if note is not None:
            registro.note = note
    session.flush()
    return registro


def known_months(session: Session) -> list[date]:
    """Todos os meses com qualquer dado registrado."""
    meses: set[date] = set()
    meses.update(months_with_incomes(session))
    meses.update(months_with_expenses(session))
    meses.update(months_with_allocations(session))
    meses.update(f.month for f in list_closings(session))
    return sorted(meses)


# --------------------------------------------------------------------------
# Configuração do plano (atalho sobre as versões de categoria)
# --------------------------------------------------------------------------
def list_plan_effective_months(session: Session) -> list[date]:
    """Meses em que alguma versão de categoria passou a valer."""
    return sorted(
        set(session.scalars(select(CategoryVersion.effective_month))), reverse=True
    )


def upsert_settings_version(
    session: Session,
    *,
    effective_month: date,
    meta_reserva_cents: int | None = None,
    percentuais_bp: dict[int, int] | None = None,
) -> None:
    """Aplica percentuais e meta a partir de um mês, em uma tacada.

    Atalho conveniente sobre :mod:`core.categories` — não guarda nada por
    fora: tudo vira versão de categoria, que continua sendo a única fonte
    da verdade. ``percentuais_bp`` é indexado por ``category_id``.
    """
    alvo = month_start(effective_month)

    for category_id, bp in (percentuais_bp or {}).items():
        cat.upsert_version(session, category_id, alvo, percent_bp=bp)

    if meta_reserva_cents is not None:
        for vista in cat.resolve_active(session, alvo):
            if vista.target_amount_cents is not None:
                cat.upsert_version(
                    session, vista.id, alvo, target_amount_cents=meta_reserva_cents
                )
