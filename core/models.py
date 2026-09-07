"""Modelo de dados (SQLAlchemy 2.0).

Convenções do esquema:

* dinheiro sempre em ``Integer`` de **centavos**;
* percentuais em **pontos-base** (``4900`` = 49,00%), para nunca usar float;
* mês sempre gravado como ``Date`` no **primeiro dia do mês**.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarativa do projeto."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class IncomeType(str, enum.Enum):
    """Tipos de entrada de dinheiro."""

    SALARIO = "Salário"
    VA_VR = "VA/VR"
    RENDA_EXTRA = "Renda extra"
    OUTRO = "Outro"
    SALDO_INICIAL = "Saldo inicial"

    @classmethod
    def selecionaveis(cls) -> list["IncomeType"]:
        """Tipos oferecidos no formulário de receitas."""
        return [cls.SALARIO, cls.VA_VR, cls.RENDA_EXTRA, cls.OUTRO, cls.SALDO_INICIAL]


class Category(str, enum.Enum):
    """Categorias do orçamento."""

    INDEPENDENCIA = "Independência financeira"
    RESERVA = "Reserva de emergência"
    VIAGEM = "Viagem"
    COMPRAS = "Compras pessoais"
    NAMORADA = "Namorada"
    AMIGOS = "Amigos"
    LIVRE = "Livre"
    OUTRO = "Outro"


#: Categorias em que o dinheiro é **separado** (têm checkbox no dashboard).
SEPARACAO_CATEGORIES: tuple[Category, ...] = (
    Category.INDEPENDENCIA,
    Category.RESERVA,
    Category.VIAGEM,
    Category.COMPRAS,
)

#: Categorias de **gasto mensal** que não acumulam saldo.
GASTO_CATEGORIES: tuple[Category, ...] = (
    Category.NAMORADA,
    Category.AMIGOS,
    Category.LIVRE,
)

#: Categorias que funcionam como **envelope acumulativo**.
ENVELOPE_CATEGORIES: tuple[Category, ...] = (Category.VIAGEM, Category.COMPRAS)

#: Categorias disponíveis no formulário de gastos.
EXPENSE_CATEGORIES: tuple[Category, ...] = (
    Category.NAMORADA,
    Category.AMIGOS,
    Category.COMPRAS,
    Category.VIAGEM,
    Category.LIVRE,
    Category.OUTRO,
)


class PaymentMethod(str, enum.Enum):
    """Meios de pagamento aceitos."""

    PIX_DEBITO = "Pix/Débito"
    CREDITO = "Crédito"
    DINHEIRO = "Dinheiro"
    OUTRO = "Outro"


# --------------------------------------------------------------------------
# Tabelas
# --------------------------------------------------------------------------
class SettingsVersion(Base):
    """Versão da configuração de percentuais, válida a partir de um mês.

    Nunca se edita uma versão passada: cria-se outra com ``effective_month``
    mais recente. É isso que preserva o plano histórico de cada mês.
    """

    __tablename__ = "settings_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    effective_month: Mapped[date] = mapped_column(Date, unique=True, index=True)
    meta_reserva_cents: Mapped[int] = mapped_column(Integer)

    pct_independencia_bp: Mapped[int] = mapped_column(Integer)
    pct_reserva_bp: Mapped[int] = mapped_column(Integer)
    pct_viagem_bp: Mapped[int] = mapped_column(Integer)
    pct_compras_bp: Mapped[int] = mapped_column(Integer)
    pct_namorada_bp: Mapped[int] = mapped_column(Integer)
    pct_amigos_bp: Mapped[int] = mapped_column(Integer)
    pct_livre_bp: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        CheckConstraint("meta_reserva_cents >= 0", name="ck_meta_reserva_nao_negativa"),
    )

    def pesos_bp(self) -> dict[Category, int]:
        """Percentuais em pontos-base, por categoria."""
        return {
            Category.INDEPENDENCIA: self.pct_independencia_bp,
            Category.RESERVA: self.pct_reserva_bp,
            Category.VIAGEM: self.pct_viagem_bp,
            Category.COMPRAS: self.pct_compras_bp,
            Category.NAMORADA: self.pct_namorada_bp,
            Category.AMIGOS: self.pct_amigos_bp,
            Category.LIVRE: self.pct_livre_bp,
        }

    def total_bp(self) -> int:
        """Soma dos percentuais em pontos-base (deve ser 10.000)."""
        return sum(self.pesos_bp().values())


class Income(Base):
    """Uma entrada de dinheiro.

    ``counts_in_budget`` separa dois conceitos que não são a mesma coisa:

    * **Recebido no mês** = tudo que não é ``SALDO_INICIAL``;
    * **Base de distribuição** = tudo com ``counts_in_budget=True``.

    Assim um saldo que já existia pode entrar no rateio sem virar renda.
    """

    __tablename__ = "incomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(200))
    type: Mapped[IncomeType] = mapped_column(Enum(IncomeType))
    amount_cents: Mapped[int] = mapped_column(Integer)
    counts_in_budget: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_receita_nao_negativa"),
    )

    @property
    def is_renda(self) -> bool:
        """Se a entrada conta como renda recebida no mês."""
        return self.type is not IncomeType.SALDO_INICIAL


class Expense(Base):
    """Uma compra/gasto. As parcelas ficam em :class:`ExpenseInstallment`.

    Mesmo um gasto à vista gera **uma** parcela, para que o cálculo do gasto
    mensal seja sempre a mesma consulta.
    """

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(200))
    category: Mapped[Category] = mapped_column(Enum(Category), index=True)
    total_cents: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    installments_count: Mapped[int] = mapped_column(Integer, default=1)
    first_installment_month: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    installments: Mapped[list["ExpenseInstallment"]] = relationship(
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="ExpenseInstallment.number",
    )

    __table_args__ = (
        CheckConstraint("total_cents >= 0", name="ck_gasto_nao_negativo"),
        CheckConstraint("installments_count >= 1", name="ck_parcelas_minimo_um"),
    )

    @property
    def is_parcelado(self) -> bool:
        """Se a compra tem mais de uma parcela."""
        return self.installments_count > 1


class ExpenseInstallment(Base):
    """Parcela de uma compra, alocada a um mês específico."""

    __tablename__ = "expense_installments"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    month: Mapped[date] = mapped_column(Date, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)

    expense: Mapped[Expense] = relationship(back_populates="installments")

    __table_args__ = (
        UniqueConstraint("expense_id", "number", name="uq_parcela_por_compra"),
        CheckConstraint("number >= 1", name="ck_numero_parcela_positivo"),
    )


class MonthRevision(Base):
    """Contador de revisões do plano de um mês.

    Sobe sempre que algo muda o **plano** daquele mês (receitas ou
    configuração). Gastos não mexem no plano e por isso não incrementam.
    """

    __tablename__ = "month_revisions"

    month: Mapped[date] = mapped_column(Date, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AllocationState(Base):
    """Quanto já foi efetivamente separado para uma categoria em um mês.

    Guarda o **valor**, não um booleano: se a renda do mês aumentar, o plano
    cresce, a confirmação deixa de cobrir o total e a categoria volta a
    ficar pendente — sem esquecer o que já havia sido separado.
    """

    __tablename__ = "allocation_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[Category] = mapped_column(Enum(Category))
    separated_cents: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        UniqueConstraint("month", "category", name="uq_separacao_mes_categoria"),
        CheckConstraint("separated_cents >= 0", name="ck_separado_nao_negativo"),
    )


class MonthlyClosing(Base):
    """Fechamento informado manualmente uma vez por mês."""

    __tablename__ = "monthly_closings"

    month: Mapped[date] = mapped_column(Date, primary_key=True)
    reserva_cents: Mapped[int] = mapped_column(Integer, default=0)
    investimentos_cents: Mapped[int] = mapped_column(Integer, default=0)
    dividendos_cents: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(300), default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        CheckConstraint("reserva_cents >= 0", name="ck_reserva_nao_negativa"),
        CheckConstraint("investimentos_cents >= 0", name="ck_investimentos_nao_negativos"),
    )


class OpeningBalance(Base):
    """Saldo inicial de um envelope, anterior ao uso do aplicativo."""

    __tablename__ = "opening_balances"

    category: Mapped[Category] = mapped_column(Enum(Category), primary_key=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(300), default=None)


class ImportLog(Base):
    """Marca importações já executadas, para não duplicar dados."""

    __tablename__ = "import_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), unique=True)
    detail: Mapped[str | None] = mapped_column(String(500), default=None)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
