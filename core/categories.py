"""Categorias: resolução por mês, edição versionada e validações.

Uma categoria tem identidade estável e propriedades que valem *a partir de*
um mês. Perguntar "como era a Namorada em setembro?" é resolver a versão
vigente naquele mês — por isso mudar o plano hoje não reescreve o passado.

Nenhuma regra aqui (nem em lugar nenhum) olha o **nome** da categoria: o que
decide é o comportamento e as propriedades declaradas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import cache
from .models import (
    SEED_CATEGORIES,
    SEED_EFFECTIVE_MONTH,
    AllocationState,
    Category,
    CategoryBehavior,
    CategoryVersion,
    ClosingField,
    Expense,
    OpeningBalance,
)
from .utils import month_start

TOTAL_BP = 10_000


class CategoryError(ValueError):
    """Erro de regra ao criar ou editar categorias."""


# --------------------------------------------------------------------------
# Visão resolvida
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CategoryView:
    """Uma categoria como ela era (ou é) em um mês específico.

    É o objeto que circula pelos services e pelas telas: imutável, já
    resolvido, sem precisar consultar versões de novo.
    """

    id: int
    slug: str
    month: date
    name: str
    emoji: str | None
    behavior: CategoryBehavior
    percent_bp: int
    display_order: int
    active: bool
    target_amount_cents: int | None
    overflow_target_category_id: int | None
    counts_as_investment_capital: bool
    include_in_net_worth: bool
    balance_from_closing: ClosingField | None
    accumulates_balance: bool = False

    @property
    def label(self) -> str:
        """Nome com emoji, quando houver."""
        return f"{self.emoji} {self.name}".strip() if self.emoji else self.name

    @property
    def requires_separation(self) -> bool:
        """Toda categoria ativa passa pela confirmação de separação.

        Não existe mais categoria que receba dinheiro sozinha. Enquanto o
        valor não é confirmado, ele está planejado — e planejado não é
        saldo. Vale para a mais óbvia (reserva) e para a menos óbvia
        (dinheiro livre): as duas só têm saldo depois que você confirma.
        """
        return self.active

    @property
    def accumulates(self) -> bool:
        """Todo saldo atravessa o mês.

        Nada expira na virada: o que sobrou continua na conta, e o que
        faltou continua faltando.
        """
        return True

    @property
    def receives_percent(self) -> bool:
        """Toda categoria ativa participa do rateio da renda."""
        return self.active


# --------------------------------------------------------------------------
# Semeadura
# --------------------------------------------------------------------------
def slugify(texto: str) -> str:
    """Gera um identificador estável a partir de um nome."""
    normalizado = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in normalizado if not unicodedata.combining(c))
    limpo = re.sub(r"[^a-zA-Z0-9]+", "_", sem_acento).strip("_").lower()
    return limpo or "categoria"


def seed_categories(session: Session) -> None:
    """Cria as categorias iniciais, se ainda não existirem."""
    if session.scalar(select(func.count()).select_from(Category)):
        return

    criadas: dict[str, Category] = {}
    for dados in SEED_CATEGORIES:
        categoria = Category(slug=str(dados["slug"]))
        session.add(categoria)
        criadas[categoria.slug] = categoria
    session.flush()

    for dados in SEED_CATEGORIES:
        slug_overflow = dados.get("overflow_target_slug")
        session.add(
            CategoryVersion(
                category_id=criadas[str(dados["slug"])].id,
                effective_month=SEED_EFFECTIVE_MONTH,
                name=str(dados["name"]),
                emoji=dados.get("emoji"),  # type: ignore[arg-type]
                behavior=dados["behavior"],  # type: ignore[arg-type]
                percent_bp=int(dados["percent_bp"]),  # type: ignore[arg-type]
                display_order=int(dados["display_order"]),  # type: ignore[arg-type]
                active=True,
                target_amount_cents=dados.get("target_amount_cents"),  # type: ignore[arg-type]
                overflow_target_category_id=(
                    criadas[str(slug_overflow)].id if slug_overflow else None
                ),
                counts_as_investment_capital=bool(
                    dados.get("counts_as_investment_capital", False)
                ),
                include_in_net_worth=bool(dados.get("include_in_net_worth", False)),
                accumulates_balance=bool(
                    dados.get(
                        "accumulates_balance",
                        dados["behavior"].acumula_por_padrao,  # type: ignore[union-attr]
                    )
                ),
                balance_from_closing=dados.get("balance_from_closing"),  # type: ignore[arg-type]
            )
        )
        saldo = int(dados.get("opening_balance_cents", 0))  # type: ignore[arg-type]
        if saldo:
            session.add(
                OpeningBalance(
                    category_id=criadas[str(dados["slug"])].id,
                    amount_cents=saldo,
                    note="Saldo anterior ao uso do aplicativo",
                )
            )
    session.flush()


# --------------------------------------------------------------------------
# Resolução por mês
# --------------------------------------------------------------------------
def _to_view(versao: CategoryVersion, slug: str, mes: date) -> CategoryView:
    """Converte uma versão persistida na visão imutável usada pelo app."""
    return CategoryView(
        id=versao.category_id,
        slug=slug,
        month=mes,
        name=versao.name,
        emoji=versao.emoji,
        behavior=versao.behavior,
        percent_bp=versao.percent_bp,
        display_order=versao.display_order,
        active=versao.active,
        target_amount_cents=versao.target_amount_cents,
        overflow_target_category_id=versao.overflow_target_category_id,
        counts_as_investment_capital=versao.counts_as_investment_capital,
        include_in_net_worth=versao.include_in_net_worth,
        balance_from_closing=versao.balance_from_closing,
        accumulates_balance=versao.accumulates_balance,
    )


def resolve_all(session: Session, month: date) -> list[CategoryView]:
    """Todas as categorias como estavam no mês, ativas ou não.

    Útil para editar lançamentos históricos: uma categoria desativada em
    2027 continua aparecendo corretamente num gasto de 2026.

    Resolve tudo em **uma consulta** e guarda o resultado na sessão: uma
    tela chega a pedir isto uma dúzia de vezes, e com o banco na nuvem cada
    ida custa caro.
    """
    alvo = month_start(month)
    return cache.obter(session, f"categorias:{alvo}", lambda: _resolver(session, alvo))


def todas_as_versoes(session: Session) -> list[tuple[str, CategoryVersion]]:
    """Todas as versões de todas as categorias, em ordem de vigência.

    São poucas linhas — uma por edição de categoria — e o histórico mês a
    mês precisa resolver as categorias de dezenas de meses. Lendo tudo uma
    vez, resolver qualquer mês passa a custar zero consultas.
    """
    return cache.obter(
        session,
        "versoes",
        lambda: [
            (slug, versao)
            for slug, versao in session.execute(
                select(Category.slug, CategoryVersion)
                .join(CategoryVersion, CategoryVersion.category_id == Category.id)
                .order_by(CategoryVersion.effective_month, CategoryVersion.id)
            )
        ],
    )


def _resolver(session: Session, alvo: date) -> list[CategoryView]:
    """Escolhe, para cada categoria, a última versão que já valia no mês."""
    linhas = [
        (slug, versao)
        for slug, versao in todas_as_versoes(session)
        if versao.effective_month <= alvo
    ]

    # Em ordem crescente de vigência, a última versão lida de cada categoria
    # é justamente a que vale no mês pedido.
    vigentes: dict[int, tuple[str, CategoryVersion]] = {}
    for slug, versao in linhas:
        vigentes[versao.category_id] = (slug, versao)

    vistas = [_to_view(versao, slug, alvo) for slug, versao in vigentes.values()]
    vistas.sort(key=lambda v: (v.display_order, v.name))
    return vistas


def resolve_active(session: Session, month: date) -> list[CategoryView]:
    """Categorias ativas no mês, na ordem de exibição."""
    return [v for v in resolve_all(session, month) if v.active]


def resolve_plan(session: Session, month: date) -> list[CategoryView]:
    """Categorias ativas que participam do rateio da renda no mês."""
    return [v for v in resolve_active(session, month) if v.receives_percent]


def get_view(session: Session, category_id: int, month: date) -> CategoryView | None:
    """Uma categoria específica como estava no mês."""
    for vista in resolve_all(session, month):
        if vista.id == category_id:
            return vista
    return None


def get_by_slug(session: Session, slug: str) -> Category | None:
    """Categoria pela identidade estável."""
    return session.scalar(select(Category).where(Category.slug == slug))


def latest_version(session: Session, category_id: int) -> CategoryVersion | None:
    """Versão mais recente de uma categoria, independentemente do mês."""
    return session.scalar(
        select(CategoryVersion)
        .where(CategoryVersion.category_id == category_id)
        .order_by(CategoryVersion.effective_month.desc())
        .limit(1)
    )


def version_for(
    session: Session, category_id: int, month: date
) -> CategoryVersion | None:
    """Versão vigente de uma categoria em um mês."""
    return session.scalar(
        select(CategoryVersion)
        .where(CategoryVersion.category_id == category_id)
        .where(CategoryVersion.effective_month <= month_start(month))
        .order_by(CategoryVersion.effective_month.desc())
        .limit(1)
    )


# --------------------------------------------------------------------------
# Validações
# --------------------------------------------------------------------------
def total_bp(vistas: list[CategoryView]) -> int:
    """Soma dos percentuais das categorias que participam do rateio."""
    return sum(v.percent_bp for v in vistas if v.active and v.receives_percent)


def validar_total(vistas: list[CategoryView]) -> None:
    """Garante que o plano soma exatamente 100%.

    Trabalha em pontos-base inteiros: 100% é ``10000``, sem margem para
    arredondamento visual esconder um plano furado.
    """
    total = total_bp(vistas)
    if total != TOTAL_BP:
        diferenca = abs(total - TOTAL_BP) / 100
        lado = "Faltam" if total < TOTAL_BP else "Excesso de"
        raise CategoryError(
            f"Os percentuais somam {total / 100:.2f}%. {lado} {diferenca:.2f}% "
            "para fechar 100%."
        )


def tem_historico(session: Session, category_id: int) -> bool:
    """Se a categoria já foi usada em gastos ou separações."""
    usada_em_gastos = session.scalar(
        select(func.count()).select_from(Expense).where(Expense.category_id == category_id)
    )
    usada_em_separacoes = session.scalar(
        select(func.count())
        .select_from(AllocationState)
        .where(AllocationState.category_id == category_id)
        .where(AllocationState.separated_cents > 0)
    )
    return bool(usada_em_gastos or usada_em_separacoes)


def pode_trocar_comportamento(
    session: Session, category_id: int, novo: CategoryBehavior
) -> tuple[bool, str]:
    """Se o comportamento pode mudar sem distorcer o histórico.

    Trocar, por exemplo, um orçamento mensal em envelope acumulativo muda o
    significado de tudo que já foi lançado. Quando há histórico, a mudança é
    bloqueada e o caminho correto é desativar e criar outra categoria.
    """
    atual = latest_version(session, category_id)
    if atual is None or atual.behavior is novo:
        return True, ""
    if not tem_historico(session, category_id):
        return True, ""
    return False, (
        f"Esta categoria já tem histórico como “{atual.behavior.label}”. "
        f"Mudar para “{novo.label}” alteraria o significado do que já foi "
        "registrado. Desative-a a partir de um mês e crie uma categoria nova."
    )


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------
def _copiar_versao(base: CategoryVersion, effective_month: date) -> CategoryVersion:
    """Nova versão idêntica à anterior, valendo a partir de outro mês."""
    return CategoryVersion(
        category_id=base.category_id,
        effective_month=effective_month,
        name=base.name,
        emoji=base.emoji,
        behavior=base.behavior,
        percent_bp=base.percent_bp,
        display_order=base.display_order,
        active=base.active,
        target_amount_cents=base.target_amount_cents,
        overflow_target_category_id=base.overflow_target_category_id,
        counts_as_investment_capital=base.counts_as_investment_capital,
        include_in_net_worth=base.include_in_net_worth,
        balance_from_closing=base.balance_from_closing,
        accumulates_balance=base.accumulates_balance,
    )


def upsert_version(
    session: Session,
    category_id: int,
    effective_month: date,
    **campos: object,
) -> CategoryVersion:
    """Cria ou atualiza a versão de uma categoria a partir de um mês.

    Os campos não informados são herdados da versão anterior, para que uma
    edição pequena não precise repetir tudo.
    """
    alvo = month_start(effective_month)
    anterior = version_for(session, category_id, alvo)

    existente = session.scalar(
        select(CategoryVersion)
        .where(CategoryVersion.category_id == category_id)
        .where(CategoryVersion.effective_month == alvo)
    )
    if existente is not None:
        versao = existente
    elif anterior is not None:
        versao = _copiar_versao(anterior, alvo)
        session.add(versao)
    else:
        versao = CategoryVersion(
            category_id=category_id,
            effective_month=alvo,
            name=str(campos.get("name", "Categoria")),
            behavior=campos.get("behavior", CategoryBehavior.MONTHLY_SPENDING),  # type: ignore[arg-type]
        )
        session.add(versao)

    for campo, valor in campos.items():
        if not hasattr(versao, campo):
            raise CategoryError(f"Campo desconhecido em categoria: {campo}")
        setattr(versao, campo, valor)

    session.flush()
    cache.limpar(session)
    return versao


def criar_categoria(
    session: Session,
    *,
    name: str,
    behavior: CategoryBehavior,
    percent_bp: int,
    effective_month: date,
    emoji: str | None = None,
    display_order: int | None = None,
    target_amount_cents: int | None = None,
    overflow_target_category_id: int | None = None,
    counts_as_investment_capital: bool = False,
    include_in_net_worth: bool | None = None,
    accumulates_balance: bool | None = None,
) -> Category:
    """Cria uma categoria valendo a partir de um mês.

    Envelopes entram no patrimônio por padrão, porque o saldo deles é
    dinheiro que existe de verdade.
    """
    nome = name.strip()
    if not nome:
        raise CategoryError("A categoria precisa de um nome.")

    slug_base = slugify(nome)
    slug, sufixo = slug_base, 2
    while get_by_slug(session, slug) is not None:
        slug = f"{slug_base}_{sufixo}"
        sufixo += 1

    categoria = Category(slug=slug)
    session.add(categoria)
    session.flush()
    cache.limpar(session)

    if display_order is None:
        maximo = session.scalar(select(func.max(CategoryVersion.display_order))) or 0
        display_order = int(maximo) + 1

    upsert_version(
        session,
        categoria.id,
        effective_month,
        name=nome,
        emoji=emoji,
        behavior=behavior,
        percent_bp=percent_bp,
        display_order=display_order,
        active=True,
        target_amount_cents=target_amount_cents,
        overflow_target_category_id=overflow_target_category_id,
        counts_as_investment_capital=counts_as_investment_capital,
        include_in_net_worth=(
            behavior.accumulates if include_in_net_worth is None else include_in_net_worth
        ),
        accumulates_balance=(
            behavior.acumula_por_padrao
            if accumulates_balance is None
            else accumulates_balance
        ),
    )
    return categoria


def desativar_categoria(
    session: Session, category_id: int, effective_month: date
) -> CategoryVersion:
    """Desativa a categoria a partir de um mês, sem apagar o histórico."""
    return upsert_version(session, category_id, effective_month, active=False, percent_bp=0)


def reativar_categoria(
    session: Session, category_id: int, effective_month: date, percent_bp: int
) -> CategoryVersion:
    """Reativa a categoria a partir de um mês."""
    return upsert_version(
        session, category_id, effective_month, active=True, percent_bp=percent_bp
    )
