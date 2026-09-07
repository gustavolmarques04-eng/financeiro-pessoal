"""Regras financeiras do aplicativo — **fonte única da verdade**.

Nenhuma tela calcula dinheiro por conta própria: todas chamam funções daqui.
Se uma regra precisar mudar, muda só neste arquivo.

Conceitos centrais
------------------
**Recebido no mês**
    Tudo que entrou como renda (exclui ``Saldo inicial``).

**Base de distribuição**
    O que é rateado pelos percentuais — inclui saldos anteriores marcados
    como participantes do orçamento.

**Separação**
    Valor efetivamente reservado para uma categoria. Guardado como *valor*,
    nunca como booleano: se a renda cresce, o plano cresce, a separação
    antiga continua registrada e a categoria volta a ficar pendente.

**Envelope**
    Viagem e Compras acumulam entre meses: saldo = inicial + separações
    confirmadas − gastos da categoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from . import repositories as repo
from .models import (
    GASTO_CATEGORIES,
    SEPARACAO_CATEGORIES,
    Category,
    SettingsVersion,
)
from .utils import month_start, split_proportionally

#: Ordem canônica das sete categorias do rateio.
CATEGORIAS_RATEIO: tuple[Category, ...] = (
    Category.INDEPENDENCIA,
    Category.RESERVA,
    Category.VIAGEM,
    Category.COMPRAS,
    Category.NAMORADA,
    Category.AMIGOS,
    Category.LIVRE,
)

TOTAL_BP = 10_000


# --------------------------------------------------------------------------
# Estruturas de saída
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SeparacaoLinha:
    """Uma categoria de separação no mês."""

    categoria: Category
    planejado_cents: int
    separado_cents: int
    confirmed_revision: int | None

    @property
    def falta_cents(self) -> int:
        """Quanto ainda falta separar (nunca negativo)."""
        return max(0, self.planejado_cents - self.separado_cents)

    @property
    def feito(self) -> bool:
        """Se já foi separado pelo menos o planejado atual."""
        return self.separado_cents >= self.planejado_cents and self.planejado_cents > 0

    @property
    def excedente_cents(self) -> int:
        """Quanto foi separado além do planejado atual."""
        return max(0, self.separado_cents - self.planejado_cents)

    @property
    def status(self) -> str:
        """Rótulo de status para a interface."""
        if self.planejado_cents == 0 and self.separado_cents == 0:
            return "Sem valor"
        if self.feito:
            return "Feito"
        if self.separado_cents > 0:
            return "Parcial"
        return "Pendente"


@dataclass(frozen=True)
class GastoLinha:
    """Uma categoria de orçamento mensal de gasto."""

    categoria: Category
    orcamento_cents: int
    gasto_cents: int

    @property
    def disponivel_cents(self) -> int:
        """Sobra do orçamento; negativo quando estourou."""
        return self.orcamento_cents - self.gasto_cents

    @property
    def estourou(self) -> bool:
        """Se o gasto passou do orçamento."""
        return self.disponivel_cents < 0


@dataclass(frozen=True)
class MonthPlan:
    """Retrato financeiro completo de um mês."""

    month: date
    revision: int
    settings: SettingsVersion
    recebido_cents: int
    base_cents: int
    gasto_cents: int
    planejado: dict[Category, int]
    separacoes: list[SeparacaoLinha] = field(default_factory=list)
    gastos: list[GastoLinha] = field(default_factory=list)
    reserva_referencia_cents: int = 0
    sobra_reserva_cents: int = 0

    @property
    def meta_reserva_cents(self) -> int:
        """Meta da reserva vigente no mês."""
        return self.settings.meta_reserva_cents

    @property
    def total_planejado_cents(self) -> int:
        """Soma do plano — deve ser igual à base de distribuição."""
        return sum(self.planejado.values())

    @property
    def total_falta_separar_cents(self) -> int:
        """Quanto ainda falta separar somando as quatro categorias."""
        return sum(linha.falta_cents for linha in self.separacoes)

    def separacao(self, categoria: Category) -> SeparacaoLinha:
        """Linha de separação de uma categoria."""
        for linha in self.separacoes:
            if linha.categoria is categoria:
                return linha
        raise KeyError(categoria)


# --------------------------------------------------------------------------
# Rateio
# --------------------------------------------------------------------------
def distribuir(base_cents: int, pesos_bp: dict[Category, int]) -> dict[Category, int]:
    """Reparte a base pelos percentuais sem perder centavos.

    A soma do resultado é exatamente ``base_cents`` quando os pesos somam
    100% — os centavos de arredondamento vão para as maiores frações.
    """
    ordem = [c for c in CATEGORIAS_RATEIO if c in pesos_bp]
    fatias = split_proportionally(base_cents, [pesos_bp[c] for c in ordem])
    return dict(zip(ordem, fatias))


def aplicar_regra_reserva(
    planejado: dict[Category, int], reserva_atual_cents: int, meta_cents: int
) -> tuple[dict[Category, int], int]:
    """Limita a Reserva ao que falta para a meta e joga a sobra na Independência.

    Se faltam R$ 100 para a meta e o percentual daria R$ 300, a Reserva
    recebe R$ 100 e os outros R$ 200 vão para Independência financeira. O
    total distribuído não muda.

    Devolve o plano ajustado e quanto foi redirecionado.
    """
    ajustado = dict(planejado)
    previsto = ajustado.get(Category.RESERVA, 0)
    if previsto <= 0:
        return ajustado, 0

    falta_para_meta = max(0, meta_cents - reserva_atual_cents)
    necessario = min(previsto, falta_para_meta)
    sobra = previsto - necessario
    if sobra <= 0:
        return ajustado, 0

    ajustado[Category.RESERVA] = necessario
    ajustado[Category.INDEPENDENCIA] = ajustado.get(Category.INDEPENDENCIA, 0) + sobra
    return ajustado, sobra


# --------------------------------------------------------------------------
# Plano do mês
# --------------------------------------------------------------------------
def reserva_referencia(session: Session, month: date) -> int:
    """Reserva considerada ao planejar o mês.

    Usa o fechamento informado mais recente até o mês. Sem nenhum
    fechamento, assume zero — o plano então segue o percentual cheio.
    """
    fechamento = repo.latest_closing_until(session, month)
    return fechamento.reserva_cents if fechamento else 0


def get_month_plan(session: Session, month: date) -> MonthPlan:
    """Monta o retrato financeiro do mês.

    Este é o ponto de entrada usado por todas as telas: dashboard, receitas,
    gastos e fechamento leem daqui, nunca recalculam.
    """
    alvo = month_start(month)
    settings = repo.get_settings_for_month(session, alvo)
    revision = repo.get_revision(session, alvo)

    recebido = repo.sum_incomes(session, alvo, only_renda=True)
    base = repo.sum_incomes(session, alvo, only_budget=True)
    gasto_total = repo.sum_expenses(session, alvo)

    bruto = distribuir(base, settings.pesos_bp())
    referencia = reserva_referencia(session, alvo)
    planejado, sobra = aplicar_regra_reserva(
        bruto, referencia, settings.meta_reserva_cents
    )

    separacoes = []
    for categoria in SEPARACAO_CATEGORIES:
        estado = repo.get_allocation(session, alvo, categoria)
        separacoes.append(
            SeparacaoLinha(
                categoria=categoria,
                planejado_cents=planejado.get(categoria, 0),
                separado_cents=estado.separated_cents if estado else 0,
                confirmed_revision=estado.confirmed_revision if estado else None,
            )
        )

    gastos = [
        GastoLinha(
            categoria=categoria,
            orcamento_cents=planejado.get(categoria, 0),
            gasto_cents=repo.sum_expenses(session, alvo, category=categoria),
        )
        for categoria in GASTO_CATEGORIES
    ]

    return MonthPlan(
        month=alvo,
        revision=revision,
        settings=settings,
        recebido_cents=recebido,
        base_cents=base,
        gasto_cents=gasto_total,
        planejado=planejado,
        separacoes=separacoes,
        gastos=gastos,
        reserva_referencia_cents=referencia,
        sobra_reserva_cents=sobra,
    )


# --------------------------------------------------------------------------
# Confirmação de separações
# --------------------------------------------------------------------------
def confirmar_separacao(session: Session, month: date, categoria: Category) -> int:
    """Marca a categoria como separada até o valor planejado agora.

    Devolve o novo valor separado. Se depois entrar mais renda, o plano sobe
    e a categoria volta sozinha para "pendente", preservando este valor.
    """
    plano = get_month_plan(session, month)
    linha = plano.separacao(categoria)
    alvo = max(linha.planejado_cents, linha.separado_cents)
    repo.set_allocation(
        session, month, categoria, separated_cents=alvo, revision=plano.revision
    )
    return alvo


def desfazer_separacao(session: Session, month: date, categoria: Category) -> None:
    """Zera a separação da categoria no mês (correção manual do usuário)."""
    plano = get_month_plan(session, month)
    repo.set_allocation(
        session, month, categoria, separated_cents=0, revision=plano.revision
    )


def ajustar_separacao(
    session: Session, month: date, categoria: Category, separado_cents: int
) -> None:
    """Grava um valor separado específico (separação parcial)."""
    plano = get_month_plan(session, month)
    repo.set_allocation(
        session,
        month,
        categoria,
        separated_cents=max(0, separado_cents),
        revision=plano.revision,
    )


# --------------------------------------------------------------------------
# Envelopes acumulativos
# --------------------------------------------------------------------------
def saldo_envelope(session: Session, categoria: Category, month: date) -> int:
    """Saldo acumulado de Viagem ou Compras até o fim do mês.

    Conta apenas o que foi **efetivamente separado**, nunca o planejado::

        saldo = saldo inicial + separações confirmadas − gastos da categoria
    """
    inicial = repo.get_opening_balance(session, categoria)
    separado = repo.sum_allocations_until(session, month, categoria)
    gasto = repo.sum_expenses_until(session, month, categoria)
    return inicial + separado - gasto


def saldo_viagem(session: Session, month: date) -> int:
    """Saldo acumulado do envelope Viagem."""
    return saldo_envelope(session, Category.VIAGEM, month)


def saldo_compras(session: Session, month: date) -> int:
    """Saldo acumulado do envelope Compras pessoais."""
    return saldo_envelope(session, Category.COMPRAS, month)


# --------------------------------------------------------------------------
# Patrimônio
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Patrimonio:
    """Patrimônio acompanhado pelo aplicativo em um mês."""

    month: date
    reserva_cents: int
    investimentos_cents: int
    viagem_cents: int
    compras_cents: int
    informado: bool

    @property
    def total_cents(self) -> int:
        """Soma das quatro parcelas, sem contar nada duas vezes."""
        return (
            self.reserva_cents
            + self.investimentos_cents
            + self.viagem_cents
            + self.compras_cents
        )


def get_patrimonio(session: Session, month: date) -> Patrimonio:
    """Patrimônio acompanhado: reserva + investimentos + envelopes.

    Reserva e investimentos vêm do fechamento informado (o mais recente até
    o mês); os envelopes vêm das separações confirmadas. Nada é somado duas
    vezes porque as fontes são disjuntas.
    """
    alvo = month_start(month)
    fechamento = repo.latest_closing_until(session, alvo)
    return Patrimonio(
        month=alvo,
        reserva_cents=fechamento.reserva_cents if fechamento else 0,
        investimentos_cents=fechamento.investimentos_cents if fechamento else 0,
        viagem_cents=saldo_viagem(session, alvo),
        compras_cents=saldo_compras(session, alvo),
        informado=fechamento is not None,
    )


def serie_patrimonio(session: Session, meses: list[date]) -> list[tuple[date, int]]:
    """Evolução do patrimônio acompanhado ao longo dos meses informados."""
    return [(mes, get_patrimonio(session, mes).total_cents) for mes in meses]


def progresso_reserva(session: Session, month: date) -> tuple[int, int, float]:
    """Reserva atual, meta do mês e percentual concluído (0–100)."""
    settings = repo.get_settings_for_month(session, month)
    fechamento = repo.latest_closing_until(session, month)
    atual = fechamento.reserva_cents if fechamento else 0
    meta = settings.meta_reserva_cents
    pct = (atual / meta * 100) if meta else 0.0
    return atual, meta, min(pct, 100.0)
