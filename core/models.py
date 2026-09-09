"""Modelo de dados (SQLAlchemy 2.0).

Convenções do esquema:

* dinheiro sempre em ``Integer`` de **centavos**;
* percentuais em **pontos-base** (``4900`` = 49,00%), para nunca usar float;
* mês sempre gravado como ``Date`` no **primeiro dia do mês**.

Categorias têm identidade estável (:class:`Category`) e propriedades
versionadas por mês (:class:`CategoryVersion`). Nada de regra de negócio
depende do *nome* de uma categoria: o que manda é o comportamento e as
propriedades declaradas na versão vigente.
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
    """Tipos de entrada de dinheiro.

    Independentes das categorias de orçamento: uma coisa é de onde o
    dinheiro veio, outra é para onde ele vai.
    """

    SALARIO = "Salário"
    VA_VR = "VA/VR"
    RENDA_EXTRA = "Renda extra"
    OUTRO = "Outro"
    SALDO_INICIAL = "Saldo inicial"

    @classmethod
    def selecionaveis(cls) -> list["IncomeType"]:
        """Tipos oferecidos no formulário de receitas."""
        return [cls.SALARIO, cls.VA_VR, cls.RENDA_EXTRA, cls.OUTRO, cls.SALDO_INICIAL]


class CategoryBehavior(str, enum.Enum):
    """O que uma categoria faz com o dinheiro.

    O comportamento — e não o nome — determina as regras aplicadas.
    """

    #: Aporte de longo prazo. Exige separação; alimenta capital investido.
    ALLOCATION_LONG_TERM = "ALLOCATION_LONG_TERM"
    #: Meta com valor-alvo. Exige separação; a sobra vai para outra categoria.
    ALLOCATION_GOAL = "ALLOCATION_GOAL"
    #: Envelope que acumula entre meses. Exige separação; gastos reduzem.
    ACCUMULATING_ENVELOPE = "ACCUMULATING_ENVELOPE"
    #: Orçamento do mês. Não acumula; gastos reduzem o disponível.
    MONTHLY_SPENDING = "MONTHLY_SPENDING"
    #: Só acompanhamento: não recebe percentual nem exige separação.
    TRACKING_ONLY = "TRACKING_ONLY"

    @property
    def requires_separation(self) -> bool:
        """Se a categoria aparece na tela de separações com checkbox."""
        return self in {
            CategoryBehavior.ALLOCATION_LONG_TERM,
            CategoryBehavior.ALLOCATION_GOAL,
            CategoryBehavior.ACCUMULATING_ENVELOPE,
        }

    @property
    def accumulates(self) -> bool:
        """Se o saldo passa de um mês para o outro."""
        return self is CategoryBehavior.ACCUMULATING_ENVELOPE

    @property
    def acumula_por_padrao(self) -> bool:
        """Se uma categoria nova deste tipo deve guardar a sobra.

        Vale para todo mundo menos o acompanhamento puro: dinheiro que
        sobrou continua existindo, seja num envelope ou na conta corrente.
        """
        return self is not CategoryBehavior.TRACKING_ONLY

    @property
    def is_monthly_budget(self) -> bool:
        """Se é orçamento de consumo que reinicia todo mês."""
        return self is CategoryBehavior.MONTHLY_SPENDING

    @property
    def receives_percent(self) -> bool:
        """Se participa do rateio da renda."""
        return self is not CategoryBehavior.TRACKING_ONLY

    @property
    def label(self) -> str:
        """Nome legível do comportamento."""
        return {
            CategoryBehavior.ALLOCATION_LONG_TERM: "Aporte de longo prazo",
            CategoryBehavior.ALLOCATION_GOAL: "Meta com valor-alvo",
            CategoryBehavior.ACCUMULATING_ENVELOPE: "Envelope acumulativo",
            CategoryBehavior.MONTHLY_SPENDING: "Orçamento mensal",
            CategoryBehavior.TRACKING_ONLY: "Somente acompanhamento",
        }[self]


class ClosingField(str, enum.Enum):
    """Campo do fechamento mensal que informa o saldo real de uma categoria.

    Quando definido, o saldo da categoria vem do que o usuário declarou no
    fechamento, e não da soma das separações.
    """

    RESERVA = "reserva"
    INVESTIMENTOS = "investimentos"


class PaymentMethod(str, enum.Enum):
    """Meios de pagamento aceitos."""

    PIX_DEBITO = "Pix/Débito"
    CREDITO = "Crédito"
    DINHEIRO = "Dinheiro"
    OUTRO = "Outro"


# --------------------------------------------------------------------------
# Categorias
# --------------------------------------------------------------------------
class Category(Base):
    """Identidade estável de uma categoria.

    Só guarda o que nunca muda. Nome, emoji, percentual e comportamento
    ficam em :class:`CategoryVersion`, para que renomear ou reajustar não
    reescreva o passado.
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    versions: Mapped[list["CategoryVersion"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="CategoryVersion.effective_month",
        foreign_keys="CategoryVersion.category_id",
    )


class CategoryVersion(Base):
    """Propriedades de uma categoria a partir de um mês.

    Para saber como a categoria era em setembro, pega-se a versão mais
    recente cujo ``effective_month`` seja menor ou igual a setembro.
    """

    __tablename__ = "category_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    effective_month: Mapped[date] = mapped_column(Date, index=True)

    name: Mapped[str] = mapped_column(String(80))
    emoji: Mapped[str | None] = mapped_column(String(8), default=None)
    behavior: Mapped[CategoryBehavior] = mapped_column(
        Enum(CategoryBehavior, native_enum=False, length=30)
    )
    percent_bp: Mapped[int] = mapped_column(Integer, default=0)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)

    #: Valor-alvo, usado por ``ALLOCATION_GOAL``.
    target_amount_cents: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Para onde vai a sobra quando a meta é atingida.
    overflow_target_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), default=None
    )
    #: Se as separações desta categoria formam capital investido.
    counts_as_investment_capital: Mapped[bool] = mapped_column(default=False)
    #: Se o saldo desta categoria entra no patrimônio total.
    include_in_net_worth: Mapped[bool] = mapped_column(default=False)
    #: Se a sobra atravessa o mês. Independente de exigir separação: o
    #: "Livre" acumula sem que você precise transferir nada de banco.
    accumulates_balance: Mapped[bool] = mapped_column(default=False)
    #: Campo do fechamento que informa o saldo real (em vez de calculá-lo).
    balance_from_closing: Mapped[ClosingField | None] = mapped_column(
        Enum(ClosingField, native_enum=False, length=20), default=None
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    category: Mapped[Category] = relationship(
        back_populates="versions", foreign_keys=[category_id]
    )

    __table_args__ = (
        UniqueConstraint("category_id", "effective_month", name="uq_versao_por_mes"),
        CheckConstraint("percent_bp >= 0", name="ck_percentual_nao_negativo"),
        CheckConstraint(
            "target_amount_cents IS NULL OR target_amount_cents >= 0",
            name="ck_meta_nao_negativa",
        ),
    )


# --------------------------------------------------------------------------
# Movimentações
# --------------------------------------------------------------------------
class Income(Base):
    """Uma entrada de dinheiro.

    ``counts_in_budget`` separa dois conceitos que não são a mesma coisa:

    * **Recebido no mês** = tudo que não é ``SALDO_INICIAL``;
    * **Base de distribuição** = tudo com ``counts_in_budget=True``.

    Assim um saldo que já existia pode ficar registrado sem virar renda nem
    inflar o rateio.
    """

    __tablename__ = "incomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(200))
    type: Mapped[IncomeType] = mapped_column(Enum(IncomeType, native_enum=False, length=30))
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
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    total_cents: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False, length=20)
    )
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
    configuração de categorias). Gastos não mexem no plano.
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
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    separated_cents: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        UniqueConstraint("month", "category_id", name="uq_separacao_mes_categoria"),
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

    def valor_de(self, campo: ClosingField) -> int:
        """Valor do campo pedido, em centavos."""
        return (
            self.reserva_cents
            if campo is ClosingField.RESERVA
            else self.investimentos_cents
        )


class OpeningBalance(Base):
    """Saldo de um envelope anterior ao uso do aplicativo."""

    __tablename__ = "opening_balances"

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(300), default=None)


class Transfer(Base):
    """Dinheiro que mudou de categoria.

    Nasce de duas situações: um gasto estourou o disponível e outra
    categoria cobriu a diferença, ou você decidiu mandar a sobra de um mês
    para outro lugar. Nos dois casos é o mesmo movimento — sai de uma,
    entra na outra — e por isso é uma linha só.
    """

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    from_category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    to_category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer)
    #: Preenchido quando a transferência cobre um gasto: apagar o gasto
    #: desfaz a cobertura junto, em vez de deixar dinheiro perdido.
    expense_id: Mapped[int | None] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), default=None, index=True
    )
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_transferencia_positiva"),
        CheckConstraint(
            "from_category_id <> to_category_id",
            name="ck_transferencia_entre_diferentes",
        ),
    )


class ImportLog(Base):
    """Marca importações já executadas, para não duplicar dados."""

    __tablename__ = "import_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), unique=True)
    detail: Mapped[str | None] = mapped_column(String(500), default=None)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------
# Sementes
# --------------------------------------------------------------------------
#: Categorias criadas na primeira execução. Os *slugs* são estáveis; os
#: nomes são apenas o rótulo inicial e podem ser trocados pelo usuário.
SEED_CATEGORIES: tuple[dict[str, object], ...] = (
    {
        "slug": "independencia",
        "name": "Independência financeira",
        "emoji": "📈",
        "behavior": CategoryBehavior.ALLOCATION_LONG_TERM,
        "percent_bp": 4900,
        "display_order": 1,
        "counts_as_investment_capital": True,
        "include_in_net_worth": True,
        "balance_from_closing": ClosingField.INVESTIMENTOS,
    },
    {
        "slug": "reserva",
        "name": "Reserva de emergência",
        "emoji": "🛟",
        "behavior": CategoryBehavior.ALLOCATION_GOAL,
        "percent_bp": 1700,
        "display_order": 2,
        "target_amount_cents": 600_000,
        "overflow_target_slug": "independencia",
        "include_in_net_worth": True,
        "balance_from_closing": ClosingField.RESERVA,
    },
    {
        "slug": "viagem",
        "name": "Viagem",
        "emoji": "✈️",
        "behavior": CategoryBehavior.ACCUMULATING_ENVELOPE,
        "percent_bp": 1400,
        "display_order": 3,
        "include_in_net_worth": True,
    },
    {
        "slug": "compras",
        "name": "Compras pessoais",
        "emoji": "🛍️",
        "behavior": CategoryBehavior.ACCUMULATING_ENVELOPE,
        "percent_bp": 900,
        "display_order": 4,
        "include_in_net_worth": True,
        "opening_balance_cents": 554,
    },
    {
        "slug": "namorada",
        "name": "Namorada",
        "emoji": "💕",
        "behavior": CategoryBehavior.MONTHLY_SPENDING,
        "percent_bp": 700,
        "display_order": 5,
    },
    {
        "slug": "amigos",
        "name": "Amigos",
        "emoji": "🍻",
        "behavior": CategoryBehavior.MONTHLY_SPENDING,
        "percent_bp": 300,
        "display_order": 6,
    },
    {
        "slug": "livre",
        "name": "Livre",
        "emoji": "🎲",
        "behavior": CategoryBehavior.MONTHLY_SPENDING,
        "percent_bp": 100,
        "display_order": 7,
    },
    {
        # Destino dos gastos que não pertencem a nenhum orçamento.
        # Não recebe percentual, então fica fora da conta dos 100%.
        "slug": "outro",
        "name": "Outro",
        "emoji": "📦",
        "behavior": CategoryBehavior.TRACKING_ONLY,
        "percent_bp": 0,
        "display_order": 99,
    },
)

#: Mês em que as categorias-semente passam a valer (bem antes de qualquer dado).
SEED_EFFECTIVE_MONTH = date(2000, 1, 1)

#: Categoria usada para gastos que não se encaixam em nenhuma outra.
FALLBACK_CATEGORY_SLUG = "outro"
