"""Acesso a dados: consultas e escritas, sem nenhuma regra financeira.

As regras vivem em :mod:`core.budget_service`. Aqui só há SQL/ORM. As funções
que alteram várias tabelas (compras parceladas, versões de configuração)
recebem a ``Session`` de fora, para participarem da transação de quem chama.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    AllocationState,
    Category,
    Expense,
    ExpenseInstallment,
    Income,
    IncomeType,
    MonthlyClosing,
    MonthRevision,
    OpeningBalance,
    PaymentMethod,
    SettingsVersion,
)
from .utils import add_months, month_start, split_installments


# --------------------------------------------------------------------------
# Configurações versionadas
# --------------------------------------------------------------------------
def get_settings_for_month(session: Session, month: date) -> SettingsVersion:
    """Configuração vigente no mês: a mais recente com ``effective_month <= month``.

    É o que preserva o histórico — mudar os percentuais hoje não reescreve o
    plano de meses anteriores.
    """
    alvo = month_start(month)
    versao = session.scalar(
        select(SettingsVersion)
        .where(SettingsVersion.effective_month <= alvo)
        .order_by(SettingsVersion.effective_month.desc())
        .limit(1)
    )
    if versao is None:
        versao = session.scalar(
            select(SettingsVersion).order_by(SettingsVersion.effective_month).limit(1)
        )
    if versao is None:
        raise RuntimeError("Nenhuma configuração encontrada. Rode init_db().")
    return versao


def list_settings_versions(session: Session) -> list[SettingsVersion]:
    """Todas as versões de configuração, da mais recente para a mais antiga."""
    return list(
        session.scalars(
            select(SettingsVersion).order_by(SettingsVersion.effective_month.desc())
        )
    )


def upsert_settings_version(
    session: Session,
    *,
    effective_month: date,
    meta_reserva_cents: int,
    percentuais_bp: dict[Category, int],
) -> SettingsVersion:
    """Cria ou substitui a versão de configuração que vale a partir de um mês."""
    alvo = month_start(effective_month)
    versao = session.scalar(
        select(SettingsVersion).where(SettingsVersion.effective_month == alvo)
    )
    if versao is None:
        versao = SettingsVersion(effective_month=alvo, meta_reserva_cents=meta_reserva_cents)
        session.add(versao)

    versao.meta_reserva_cents = meta_reserva_cents
    versao.pct_independencia_bp = percentuais_bp[Category.INDEPENDENCIA]
    versao.pct_reserva_bp = percentuais_bp[Category.RESERVA]
    versao.pct_viagem_bp = percentuais_bp[Category.VIAGEM]
    versao.pct_compras_bp = percentuais_bp[Category.COMPRAS]
    versao.pct_namorada_bp = percentuais_bp[Category.NAMORADA]
    versao.pct_amigos_bp = percentuais_bp[Category.AMIGOS]
    versao.pct_livre_bp = percentuais_bp[Category.LIVRE]
    session.flush()
    return versao


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
    excluída, e configuração que passa a valer para aquele mês.
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


def bump_revisions_from(session: Session, month: date) -> list[date]:
    """Sobe a revisão do mês informado e de todos os meses posteriores com dados.

    Usado quando a configuração muda: só os meses afetados são invalidados.
    """
    alvo = month_start(month)
    meses = {alvo}
    meses.update(session.scalars(select(Income.month).where(Income.month >= alvo)))
    meses.update(
        session.scalars(select(AllocationState.month).where(AllocationState.month >= alvo))
    )
    for mes in sorted(meses):
        bump_revision(session, mes)
    return sorted(meses)


# --------------------------------------------------------------------------
# Receitas
# --------------------------------------------------------------------------
def list_incomes(session: Session, month: date) -> list[Income]:
    """Receitas do mês, mais recentes primeiro."""
    return list(
        session.scalars(
            select(Income)
            .where(Income.month == month_start(month))
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
    session: Session, month: date, *, only_budget: bool = False, only_renda: bool = False
) -> int:
    """Soma das receitas do mês, em centavos.

    ``only_renda`` exclui ``Saldo inicial`` (dinheiro que já existia);
    ``only_budget`` mantém apenas o que entra na base de distribuição.
    """
    consulta = select(func.coalesce(func.sum(Income.amount_cents), 0)).where(
        Income.month == month_start(month)
    )
    if only_budget:
        consulta = consulta.where(Income.counts_in_budget.is_(True))
    if only_renda:
        consulta = consulta.where(Income.type != IncomeType.SALDO_INICIAL)
    return int(session.scalar(consulta) or 0)


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
    category: Category,
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
        category=category,
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
    category: Category,
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
    gasto.category = category
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
    session: Session, month: date, *, category: Category | None = None
) -> list[tuple[ExpenseInstallment, Expense]]:
    """Parcelas que caem no mês, com a compra de origem."""
    consulta = (
        select(ExpenseInstallment, Expense)
        .join(Expense, ExpenseInstallment.expense_id == Expense.id)
        .where(ExpenseInstallment.month == month_start(month))
        .order_by(Expense.purchase_date.desc(), Expense.id.desc())
    )
    if category is not None:
        consulta = consulta.where(Expense.category == category)
    return [(parcela, gasto) for parcela, gasto in session.execute(consulta)]


def sum_expenses(
    session: Session, month: date, *, category: Category | None = None
) -> int:
    """Total gasto no mês (somando parcelas), opcionalmente por categoria."""
    consulta = (
        select(func.coalesce(func.sum(ExpenseInstallment.amount_cents), 0))
        .join(Expense, ExpenseInstallment.expense_id == Expense.id)
        .where(ExpenseInstallment.month == month_start(month))
    )
    if category is not None:
        consulta = consulta.where(Expense.category == category)
    return int(session.scalar(consulta) or 0)


def sum_expenses_until(session: Session, month: date, category: Category) -> int:
    """Total gasto na categoria até o fim do mês (para envelopes acumulativos)."""
    consulta = (
        select(func.coalesce(func.sum(ExpenseInstallment.amount_cents), 0))
        .join(Expense, ExpenseInstallment.expense_id == Expense.id)
        .where(ExpenseInstallment.month <= month_start(month))
        .where(Expense.category == category)
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
def get_allocation(session: Session, month: date, category: Category) -> AllocationState | None:
    """Estado de separação de uma categoria no mês."""
    return session.scalar(
        select(AllocationState)
        .where(AllocationState.month == month_start(month))
        .where(AllocationState.category == category)
    )


def set_allocation(
    session: Session,
    month: date,
    category: Category,
    *,
    separated_cents: int,
    revision: int,
) -> AllocationState:
    """Grava quanto já foi separado, junto da revisão do plano confirmada."""
    if separated_cents < 0:
        raise ValueError("Valor separado não pode ser negativo.")

    estado = get_allocation(session, month, category)
    if estado is None:
        estado = AllocationState(month=month_start(month), category=category)
        session.add(estado)

    estado.separated_cents = separated_cents
    estado.confirmed_revision = revision
    estado.confirmed_at = datetime.now(timezone.utc) if separated_cents else None
    session.flush()
    return estado


def sum_allocations_until(session: Session, month: date, category: Category) -> int:
    """Total já separado para a categoria até o fim do mês."""
    consulta = (
        select(func.coalesce(func.sum(AllocationState.separated_cents), 0))
        .where(AllocationState.month <= month_start(month))
        .where(AllocationState.category == category)
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


def get_opening_balance(session: Session, category: Category) -> int:
    """Saldo inicial do envelope, em centavos."""
    registro = session.get(OpeningBalance, category)
    return registro.amount_cents if registro else 0


def set_opening_balance(
    session: Session, category: Category, amount_cents: int, note: str | None = None
) -> OpeningBalance:
    """Define o saldo inicial de um envelope."""
    registro = session.get(OpeningBalance, category)
    if registro is None:
        registro = OpeningBalance(category=category, amount_cents=amount_cents, note=note)
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
