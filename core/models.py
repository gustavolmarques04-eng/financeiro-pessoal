"""Modelo de dados (SQLAlchemy 2.0).

Convenções do esquema:

* dinheiro sempre em ``Integer`` de **centavos**;
* percentuais em **pontos-base** (``4900`` = 49,00%), para nunca usar float;
* mês sempre gravado como ``Date`` no **primeiro dia do mês**.

Categorias têm identidade estável (:class:`Category`) e propriedades
versionadas por mês (:class:`CategoryVersion`). Nada de regra de negócio
depende do *nome* de uma categoria: o que manda é o comportamento e as
propriedades declaradas na versão vigente.

Cada linha financeira pertence a um usuário (``user_id``), e as chaves
estrangeiras são **compostas** — ``(category_id, user_id)`` em vez de só
``category_id``. Sem isso o banco aceitaria um gasto de um usuário apontando
para a categoria de outro; com isso, é o próprio PostgreSQL que recusa.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarativa do projeto."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def coluna_do_dono() -> Mapped[uuid.UUID]:
    """Coluna ``user_id`` presente em toda tabela financeira.

    É o eixo de todo o isolamento: as policies de RLS comparam esta coluna
    com ``auth.uid()``, e as chaves compostas a usam para impedir que uma
    linha aponte para a categoria de outra pessoa.
    """
    return mapped_column(Uuid(as_uuid=True), nullable=False, index=True)


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
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    slug: Mapped[str] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    versions: Mapped[list["CategoryVersion"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="CategoryVersion.effective_month",
        foreign_keys="CategoryVersion.category_id",
    )

    __table_args__ = (
        # O slug identifica a categoria dentro do usuário: os dois podem ter
        # uma "Viagem" sem colidir.
        UniqueConstraint("user_id", "slug", name="uq_slug_por_usuario"),
        # Alvo das chaves estrangeiras compostas das outras tabelas.
        UniqueConstraint("id", "user_id", name="uq_categoria_do_usuario"),
    )


class CategoryVersion(Base):
    """Propriedades de uma categoria a partir de um mês.

    Para saber como a categoria era em setembro, pega-se a versão mais
    recente cujo ``effective_month`` seja menor ou igual a setembro.
    """

    __tablename__ = "category_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    category_id: Mapped[int] = mapped_column(Integer, index=True)
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
        Integer, default=None
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
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="CASCADE",
            name="fk_versao_categoria_do_usuario",
        ),
        # A categoria de destino da sobra também tem de ser do mesmo dono.
        ForeignKeyConstraint(
            ["overflow_target_category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="SET NULL",
            name="fk_versao_destino_do_usuario",
        ),
        UniqueConstraint(
            "user_id", "category_id", "effective_month", name="uq_versao_por_mes"
        ),
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
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
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
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    purchase_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int] = mapped_column(Integer, index=True)
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
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="RESTRICT",
            name="fk_gasto_categoria_do_usuario",
        ),
        UniqueConstraint("id", "user_id", name="uq_gasto_do_usuario"),
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
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    expense_id: Mapped[int] = mapped_column(Integer, index=True)
    number: Mapped[int] = mapped_column(Integer)
    month: Mapped[date] = mapped_column(Date, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)

    expense: Mapped[Expense] = relationship(back_populates="installments")

    __table_args__ = (
        ForeignKeyConstraint(
            ["expense_id", "user_id"],
            ["expenses.id", "expenses.user_id"],
            ondelete="CASCADE",
            name="fk_parcela_gasto_do_usuario",
        ),
        UniqueConstraint("expense_id", "number", name="uq_parcela_por_compra"),
        CheckConstraint("number >= 1", name="ck_numero_parcela_positivo"),
    )


class MonthRevision(Base):
    """Contador de revisões do plano de um mês.

    Sobe sempre que algo muda o **plano** daquele mês (receitas ou
    configuração de categorias). Gastos não mexem no plano.
    """

    __tablename__ = "month_revisions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
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
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    month: Mapped[date] = mapped_column(Date, index=True)
    category_id: Mapped[int] = mapped_column(Integer, index=True)
    separated_cents: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="RESTRICT",
            name="fk_separacao_categoria_do_usuario",
        ),
        UniqueConstraint(
            "user_id", "month", "category_id", name="uq_separacao_mes_categoria"
        ),
        CheckConstraint("separated_cents >= 0", name="ck_separado_nao_negativo"),
    )


class MonthlyClosing(Base):
    """Fechamento informado manualmente uma vez por mês."""

    __tablename__ = "monthly_closings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
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

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="CASCADE",
            name="fk_saldo_inicial_categoria_do_usuario",
        ),
    )
    note: Mapped[str | None] = mapped_column(String(300), default=None)


class CategoryTransfer(Base):
    """Dinheiro que mudou de categoria.

    Nasce de tres situacoes, todas o mesmo movimento por baixo: mandar a
    sobra de uma categoria para outra, cobrir um gasto que estourou o
    disponivel, e esvaziar uma categoria antes de desativa-la.

    Transferencia nunca cria nem destroi dinheiro - o que sai de uma entra
    na outra. Por isso nao se edita nem se apaga uma transferencia antiga:
    corrige-se com um **estorno**, que e outra transferencia, no sentido
    inverso, apontando para a original em ``reversal_of_id``.
    """

    __tablename__ = "category_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    #: Data escolhida por quem registrou.
    transfer_date: Mapped[date] = mapped_column(Date, index=True)
    #: Primeiro dia do mes de ``transfer_date``; e por ele que o saldo anda.
    month: Mapped[date] = mapped_column(Date, index=True)
    from_category_id: Mapped[int] = mapped_column(Integer, index=True)
    to_category_id: Mapped[int] = mapped_column(Integer, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(200), default=None)
    #: Preenchido quando a transferencia cobre um gasto: apagar o gasto
    #: desfaz a cobertura junto, em vez de deixar dinheiro perdido.
    expense_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    #: Quando preenchido, esta linha e o estorno da transferencia apontada.
    reversal_of_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["from_category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="CASCADE",
            name="fk_transferencia_origem_do_usuario",
        ),
        # E esta constraint que impede uma transferencia de um usuario de
        # ter como destino a categoria de outro: o par (id, user_id) nao
        # existe na tabela de categorias dele.
        ForeignKeyConstraint(
            ["to_category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="CASCADE",
            name="fk_transferencia_destino_do_usuario",
        ),
        ForeignKeyConstraint(
            ["expense_id", "user_id"],
            ["expenses.id", "expenses.user_id"],
            ondelete="CASCADE",
            name="fk_transferencia_gasto_do_usuario",
        ),
        UniqueConstraint("id", "user_id", name="uq_transferencia_do_usuario"),
        # O estorno só pode apontar para uma transferência do mesmo dono.
        ForeignKeyConstraint(
            ["reversal_of_id", "user_id"],
            ["category_transfers.id", "category_transfers.user_id"],
            name="fk_estorno_do_usuario",
        ),
        CheckConstraint("amount_cents > 0", name="ck_transferencia_positiva"),
        CheckConstraint(
            "from_category_id <> to_category_id",
            name="ck_transferencia_entre_diferentes",
        ),
    )


class AdjustmentKind(str, enum.Enum):
    """Por que o saldo foi corrigido."""

    RENDIMENTO = "RENDIMENTO"
    CORRECAO = "CORRECAO"
    MANUAL = "MANUAL"
    OUTRO = "OUTRO"

    @property
    def label(self) -> str:
        """Nome legivel."""
        return {
            AdjustmentKind.RENDIMENTO: "Rendimento",
            AdjustmentKind.CORRECAO: "Correcao",
            AdjustmentKind.MANUAL: "Ajuste manual",
            AdjustmentKind.OUTRO: "Outro",
        }[self]


class BalanceAdjustment(Base):
    """Diferenca entre o saldo calculado e o saldo real informado.

    Quando voce confere a conta e o banco mostra R$ 1.007,32 onde o app
    calculava R$ 1.000, a diferenca nao sobrescreve o historico: vira uma
    linha de +R$ 7,32 com um motivo. Assim cada centavo continua tendo
    explicacao, e o rendimento nao e confundido com erro de digitacao.

    ``amount_cents`` pode ser negativo: nem toda correcao e para cima.
    """

    __tablename__ = "balance_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    month: Mapped[date] = mapped_column(Date, index=True)
    category_id: Mapped[int] = mapped_column(Integer, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    kind: Mapped[AdjustmentKind] = mapped_column(
        Enum(AdjustmentKind, native_enum=False, length=20),
        default=AdjustmentKind.MANUAL,
    )
    note: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="CASCADE",
            name="fk_ajuste_categoria_do_usuario",
        ),
        CheckConstraint("amount_cents <> 0", name="ck_ajuste_nao_nulo"),
    )


class Profile(Base):
    """Preferencias de quem usa o aplicativo.

    Nao guarda senha nem e-mail: quem cuida disso e o Supabase Auth. Aqui
    ficam apenas as escolhas do usuario, inclusive quais categorias ele
    considera "seus investimentos" e "sua reserva" - guardadas por **id**,
    nunca por nome, para que renomear nao quebre nenhum grafico.
    """

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(80), default=None)
    onboarding_completed: Mapped[bool] = mapped_column(default=False)
    investment_category_id: Mapped[int | None] = mapped_column(Integer, default=None)
    reserve_category_id: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Quanto do saldo da categoria de investimentos foi dinheiro aportado.
    #: Serve de base para calcular ganho; nao altera saldo nenhum.
    investment_cost_basis_cents: Mapped[int | None] = mapped_column(
        Integer, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["investment_category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="SET NULL",
            name="fk_perfil_investimentos_do_usuario",
        ),
        ForeignKeyConstraint(
            ["reserve_category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="SET NULL",
            name="fk_perfil_reserva_do_usuario",
        ),
    )


class ImportLog(Base):
    """Marca importações já executadas, para não duplicar dados."""

    __tablename__ = "import_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = coluna_do_dono()
    source: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(String(500), default=None)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("user_id", "source", name="uq_import_por_usuario"),
    )


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
