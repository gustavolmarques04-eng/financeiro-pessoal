"""Regras financeiras do aplicativo — **fonte única da verdade**.

Nenhuma tela calcula dinheiro por conta própria: todas chamam funções daqui.
Se uma regra precisar mudar, muda só neste arquivo.

O que decide o tratamento de cada categoria é o seu **comportamento**
(:class:`~core.models.CategoryBehavior`) e as propriedades da versão vigente
no mês — nunca o nome. Renomear "Namorada" para "Relacionamento" não muda
nada; trocar o comportamento é que muda, e por isso é bloqueado quando já há
histórico.

Conceitos centrais
------------------
**Recebido**
    Tudo que entrou como renda (exclui ``Saldo inicial``).

**Base de distribuição**
    O que é rateado pelos percentuais — inclui saldos anteriores marcados
    como participantes do orçamento.

**Separação**
    Valor efetivamente reservado para uma categoria. Guardado como *valor*,
    nunca como booleano: se a renda cresce, o plano cresce, a separação
    antiga continua registrada e a categoria volta a ficar pendente.

**Envelope**
    Categorias ``ACCUMULATING_ENVELOPE`` acumulam entre meses: saldo =
    inicial + separações confirmadas − gastos da categoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from . import cache
from . import categories as cat
from . import repositories as repo
from .categories import CategoryView
from .models import CategoryBehavior, ClosingField
from .period import Period
from .utils import add_months, month_start, split_proportionally

TOTAL_BP = cat.TOTAL_BP


# --------------------------------------------------------------------------
# Estruturas de saída
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SeparacaoLinha:
    """Uma categoria de separação no mês."""

    categoria: CategoryView
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
    """Uma categoria de orçamento mensal de consumo."""

    categoria: CategoryView
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
class EnvelopeLinha:
    """Saldo acumulado de um envelope."""

    categoria: CategoryView
    saldo_cents: int
    gasto_no_periodo_cents: int


@dataclass(frozen=True)
class MetaLinha:
    """Progresso de uma categoria com valor-alvo."""

    categoria: CategoryView
    atual_cents: int
    meta_cents: int
    informado: bool

    @property
    def limita_o_plano(self) -> bool:
        """Se atingir a meta reduz o valor planejado e redireciona a sobra.

        Só vale para ``ALLOCATION_GOAL``. Nas demais categorias a meta é de
        acompanhamento: mostra o progresso sem mexer no rateio.
        """
        return self.categoria.behavior is CategoryBehavior.ALLOCATION_GOAL

    @property
    def falta_cents(self) -> int:
        """Quanto falta para bater a meta."""
        return max(0, self.meta_cents - self.atual_cents)

    @property
    def percentual(self) -> float:
        """Percentual concluído, limitado a 100."""
        if self.meta_cents <= 0:
            return 0.0
        return min(self.atual_cents / self.meta_cents * 100, 100.0)


@dataclass(frozen=True)
class MonthPlan:
    """Retrato financeiro de um mês."""

    month: date
    revision: int
    recebido_cents: int
    base_cents: int
    gasto_cents: int
    categorias: list[CategoryView]
    planejado: dict[int, int]
    separacoes: list[SeparacaoLinha] = field(default_factory=list)
    gastos: list[GastoLinha] = field(default_factory=list)
    sobra_meta_cents: int = 0

    @property
    def total_planejado_cents(self) -> int:
        """Soma do plano — igual à base de distribuição."""
        return sum(self.planejado.values())

    @property
    def total_falta_separar_cents(self) -> int:
        """Quanto ainda falta separar somando as categorias de separação."""
        return sum(linha.falta_cents for linha in self.separacoes)

    @property
    def pendentes(self) -> list[SeparacaoLinha]:
        """Separações que ainda não estão concluídas."""
        return [
            linha
            for linha in self.separacoes
            if not linha.feito and linha.planejado_cents > 0
        ]

    def separacao(self, category_id: int) -> SeparacaoLinha:
        """Linha de separação de uma categoria."""
        for linha in self.separacoes:
            if linha.categoria.id == category_id:
                return linha
        raise KeyError(category_id)


@dataclass(frozen=True)
class ResumoSeparacoes:
    """Resumo usado tanto pelo aviso da Home quanto pela página Separações.

    Existe uma única implementação para os dois lugares mostrarem sempre o
    mesmo número.
    """

    month: date
    total: int
    concluidas: int
    falta_cents: int

    @property
    def pendentes(self) -> int:
        """Quantas separações ainda faltam."""
        return self.total - self.concluidas

    @property
    def tudo_feito(self) -> bool:
        """Se não há nada pendente."""
        return self.total > 0 and self.pendentes == 0


# --------------------------------------------------------------------------
# Rateio
# --------------------------------------------------------------------------
def distribuir(base_cents: int, vistas: list[CategoryView]) -> dict[int, int]:
    """Reparte a base pelos percentuais sem perder centavos.

    A soma do resultado é exatamente ``base_cents`` quando os pesos somam
    100% — os centavos de arredondamento vão para as maiores frações.
    """
    participantes = [v for v in vistas if v.active and v.receives_percent]
    if not participantes:
        return {}
    fatias = split_proportionally(base_cents, [v.percent_bp for v in participantes])
    return {v.id: fatia for v, fatia in zip(participantes, fatias)}


def aplicar_regras_de_meta(
    planejado: dict[int, int],
    vistas: list[CategoryView],
    saldos: dict[int, int],
) -> tuple[dict[int, int], int]:
    """Limita categorias-meta ao que falta e redireciona a sobra.

    Se faltam R$ 100 para a meta e o percentual daria R$ 300, a categoria
    recebe R$ 100 e os outros R$ 200 vão para o destino declarado em
    ``overflow_target_category_id``. O total distribuído não muda.

    A regra vale para qualquer categoria ``ALLOCATION_GOAL`` com valor-alvo
    — não existe nenhuma referência a "Reserva" aqui.

    Outros comportamentos podem ter meta também, mas só para acompanhar o
    progresso: nesses casos o plano não é cortado, porque continuar
    aportando depois de bater a meta é o comportamento desejado.
    """
    ajustado = dict(planejado)
    sobra_total = 0

    for vista in vistas:
        if vista.behavior is not CategoryBehavior.ALLOCATION_GOAL:
            continue
        if vista.target_amount_cents is None or not vista.active:
            continue
        previsto = ajustado.get(vista.id, 0)
        if previsto <= 0:
            continue

        falta_para_meta = max(0, vista.target_amount_cents - saldos.get(vista.id, 0))
        necessario = min(previsto, falta_para_meta)
        sobra = previsto - necessario
        if sobra <= 0:
            continue

        ajustado[vista.id] = necessario
        destino = vista.overflow_target_category_id
        if destino is not None and destino in ajustado:
            ajustado[destino] = ajustado[destino] + sobra
        else:
            # Sem destino declarado, o dinheiro volta para a própria
            # categoria: melhor não fazer centavos desaparecerem.
            ajustado[vista.id] = previsto
            continue
        sobra_total += sobra

    return ajustado, sobra_total


# --------------------------------------------------------------------------
# Saldos por categoria
# --------------------------------------------------------------------------
def _depois_do_checkpoint(
    mes_registro: date,
    quando_registro: datetime | None,
    checkpoint_mes: date | None,
    checkpoint_quando: datetime | None,
) -> bool:
    """Se um lançamento ainda não está refletido na última posição informada.

    Vale quando o lançamento é de um mês posterior ao do fechamento, ou
    quando foi registrado depois de você ter informado aquele saldo.
    """
    if checkpoint_mes is None:
        return True
    if mes_registro > checkpoint_mes:
        return True
    if quando_registro is None or checkpoint_quando is None:
        return False
    return quando_registro > checkpoint_quando


def saldo_categoria(
    session: Session,
    vista: CategoryView,
    month: date,
    *,
    incluir_o_mes: bool = True,
) -> int:
    """Saldo de uma categoria no fim do mês.

    Categorias sem ``balance_from_closing`` são puro envelope::

        saldo inicial + separações confirmadas − gastos da categoria

    Categorias ligadas a um campo do fechamento (Reserva, Investimentos)
    usam o valor informado como **checkpoint** e somam por cima o que veio
    depois dele::

        saldo informado + separações feitas depois − gastos feitos depois

    É o que faz "já separei R$ 502,82 para a Reserva" aparecer somado ao que
    você já tinha guardado, sem contar duas vezes quando você informar o
    novo saldo real no próximo fechamento.

    Com ``incluir_o_mes=False`` as movimentações do próprio mês ficam de
    fora. A regra da meta usa esse modo para o planejado não mudar enquanto
    você confirma as separações daquele mês.
    """
    alvo = month_start(month)
    limite = alvo if incluir_o_mes else add_months(alvo, -1)

    if vista.balance_from_closing is None:
        inicial = repo.get_opening_balance(session, vista.id)
        separado = repo.sum_allocations_until(session, limite, vista.id)
        gasto = repo.sum_expenses_until(session, limite, vista.id)
        return inicial + separado - gasto

    fechamento = repo.latest_closing_until(session, alvo)
    if fechamento is None:
        base, checkpoint_mes, checkpoint_quando = (
            repo.get_opening_balance(session, vista.id),
            None,
            None,
        )
    else:
        base = fechamento.valor_de(vista.balance_from_closing)
        checkpoint_mes, checkpoint_quando = fechamento.month, fechamento.updated_at

    separado = sum(
        estado.separated_cents
        for estado in repo.list_allocations_until(session, limite, vista.id)
        if _depois_do_checkpoint(
            estado.month, estado.confirmed_at, checkpoint_mes, checkpoint_quando
        )
    )
    gasto = sum(
        parcela.amount_cents
        for parcela, compra in repo.list_installments_until(session, limite, vista.id)
        if _depois_do_checkpoint(
            parcela.month, compra.created_at, checkpoint_mes, checkpoint_quando
        )
    )
    return base + separado - gasto


def saldos_das_categorias(
    session: Session, vistas: list[CategoryView], month: date
) -> dict[int, int]:
    """Saldo de cada categoria informada."""
    return {v.id: saldo_categoria(session, v, month) for v in vistas}


# --------------------------------------------------------------------------
# Plano do mês
# --------------------------------------------------------------------------
def get_month_plan(session: Session, month: date) -> MonthPlan:
    """Monta o retrato financeiro do mês.

    Ponto de entrada usado por todas as telas: Home, Separações, Receitas,
    Gastos e Fechamento leem daqui, nunca recalculam.

    O resultado fica em cache pelo tempo da sessão. Uma tela pede o plano
    várias vezes — os cartões, o aviso de separações, o orçamento — e sem
    isso cada pedido repetiria dezenas de consultas.
    """
    alvo = month_start(month)
    return cache.obter(session, f"plano:{alvo}", lambda: _montar_plano(session, alvo))


def _montar_plano(session: Session, alvo: date) -> MonthPlan:
    """Calcula o plano do mês de fato, sem passar pelo cache."""
    periodo = Period.of_month(alvo)
    vistas = cat.resolve_all(session, alvo)
    revision = repo.get_revision(session, alvo)

    recebido = repo.sum_incomes(session, periodo, only_renda=True)
    base = repo.sum_incomes(session, periodo, only_budget=True)
    gasto_total = repo.sum_expenses(session, periodo)

    bruto = distribuir(base, vistas)
    # O saldo de referência da meta ignora as separações do próprio mês:
    # sem isso, confirmar a separação encolheria o planejado no mesmo passo.
    saldos = {
        v.id: saldo_categoria(session, v, alvo, incluir_o_mes=False)
        for v in vistas
        if v.target_amount_cents is not None
    }
    planejado, sobra = aplicar_regras_de_meta(bruto, vistas, saldos)

    separacoes: list[SeparacaoLinha] = []
    for vista in vistas:
        if not vista.requires_separation:
            continue
        estado = repo.get_allocation(session, alvo, vista.id)
        separado = estado.separated_cents if estado else 0
        # Categoria desativada só continua aparecendo se ainda tiver valor
        # separado naquele mês — o histórico não some da tela.
        if not vista.active and separado == 0:
            continue
        separacoes.append(
            SeparacaoLinha(
                categoria=vista,
                planejado_cents=planejado.get(vista.id, 0),
                separado_cents=separado,
                confirmed_revision=(estado.confirmed_revision if estado else None),
            )
        )

    gastos = [
        GastoLinha(
            categoria=vista,
            orcamento_cents=planejado.get(vista.id, 0),
            gasto_cents=repo.sum_expenses(session, periodo, category_id=vista.id),
        )
        for vista in vistas
        if vista.is_monthly_budget and vista.active
    ]

    return MonthPlan(
        month=alvo,
        revision=revision,
        recebido_cents=recebido,
        base_cents=base,
        gasto_cents=gasto_total,
        categorias=vistas,
        planejado=planejado,
        separacoes=separacoes,
        gastos=gastos,
        sobra_meta_cents=sobra,
    )


def rotulos_do_fechamento(session: Session, month: date) -> dict[ClosingField, str]:
    """Nome da categoria que declara cada campo do fechamento.

    O formulário de fechamento tem campos fixos no banco, mas os rótulos
    saem das categorias — renomear "Reserva de emergência" muda o texto da
    tela sem tocar em código.
    """
    padrao = {
        ClosingField.RESERVA: "Reserva de emergência",
        ClosingField.INVESTIMENTOS: "Investimentos totais",
    }
    for vista in cat.resolve_active(session, month):
        if vista.balance_from_closing is not None:
            padrao[vista.balance_from_closing] = vista.name
    return padrao


def mes_de_referencia(session: Session, period: Period) -> date:
    """Mês usado pelos blocos que só fazem sentido mensalmente.

    Orçamento de consumo é sempre de um mês. No modo anual, mostrar janeiro
    (o primeiro do período) daria a impressão errada de que não há orçamento
    nenhum — então vale o mês mais recente do ano com dados; sem dados, o mês
    corrente quando ele cai no período, ou dezembro.
    """
    if period.is_month:
        return period.month

    conhecidos = [m for m in repo.known_months(session) if period.contains(m)]
    if conhecidos:
        return max(conhecidos)

    hoje = month_start(date.today())
    return hoje if period.contains(hoje) else period.end


def resumo_separacoes(session: Session, month: date) -> ResumoSeparacoes:
    """Resumo das separações do mês.

    A Home e a página Separações chamam esta mesma função — é por isso que
    os dois lugares nunca divergem.
    """
    plano = get_month_plan(session, month)
    relevantes = [linha for linha in plano.separacoes if linha.planejado_cents > 0]
    return ResumoSeparacoes(
        month=plano.month,
        total=len(relevantes),
        concluidas=sum(1 for linha in relevantes if linha.feito),
        falta_cents=plano.total_falta_separar_cents,
    )


# --------------------------------------------------------------------------
# Confirmação de separações
# --------------------------------------------------------------------------
def confirmar_separacao(session: Session, month: date, category_id: int) -> int:
    """Marca a categoria como separada até o valor planejado agora.

    Devolve o novo valor separado. Se depois entrar mais renda, o plano sobe
    e a categoria volta sozinha para "pendente", preservando este valor.
    """
    plano = get_month_plan(session, month)
    linha = plano.separacao(category_id)
    alvo = max(linha.planejado_cents, linha.separado_cents)
    repo.set_allocation(
        session, month, category_id, separated_cents=alvo, revision=plano.revision
    )
    return alvo


def desfazer_separacao(session: Session, month: date, category_id: int) -> None:
    """Zera a separação da categoria no mês (correção manual do usuário)."""
    plano = get_month_plan(session, month)
    repo.set_allocation(
        session, month, category_id, separated_cents=0, revision=plano.revision
    )


def ajustar_separacao(
    session: Session, month: date, category_id: int, separado_cents: int
) -> None:
    """Grava um valor separado específico (separação parcial)."""
    plano = get_month_plan(session, month)
    repo.set_allocation(
        session,
        month,
        category_id,
        separated_cents=max(0, separado_cents),
        revision=plano.revision,
    )


# --------------------------------------------------------------------------
# Envelopes e metas
# --------------------------------------------------------------------------
def envelopes(session: Session, period: Period) -> list[EnvelopeLinha]:
    """Saldo acumulado de cada envelope no fim do período."""
    fim = period.end
    vistas = [v for v in cat.resolve_active(session, fim) if v.accumulates]
    return [
        EnvelopeLinha(
            categoria=vista,
            saldo_cents=saldo_categoria(session, vista, fim),
            gasto_no_periodo_cents=repo.sum_expenses(
                session, period, category_id=vista.id
            ),
        )
        for vista in vistas
    ]


def metas(session: Session, period: Period) -> list[MetaLinha]:
    """Progresso de todas as categorias com valor-alvo no fim do período.

    Qualquer categoria pode ter meta — Independência, Viagem, Compras — e
    todas aparecem no mesmo bloco. O que muda entre elas é se a meta corta o
    plano (:attr:`MetaLinha.limita_o_plano`) ou é só acompanhamento.
    """
    fim = period.end
    linhas = []
    for vista in cat.resolve_active(session, fim):
        if vista.target_amount_cents is None:
            continue
        informado = True
        if vista.balance_from_closing is not None:
            informado = repo.latest_closing_until(session, fim) is not None
        linhas.append(
            MetaLinha(
                categoria=vista,
                atual_cents=saldo_categoria(session, vista, fim),
                meta_cents=vista.target_amount_cents,
                informado=informado,
            )
        )
    return linhas


# --------------------------------------------------------------------------
# Patrimônio
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParcelaPatrimonio:
    """Uma parcela do patrimônio acompanhado."""

    categoria: CategoryView
    valor_cents: int
    #: Se entra no patrimônio, ou se é apenas dinheiro guardado com destino
    #: certo de saída (uma viagem, uma compra). Vem de ``include_in_net_worth``.
    no_patrimonio: bool = True


@dataclass(frozen=True)
class Patrimonio:
    """Patrimônio acompanhado pelo aplicativo em um instante.

    ``posicao`` é o mês da foto: no modo anual, o mês do fechamento mais
    recente daquele ano. Patrimônio de meses diferentes nunca é somado —
    seria contar o mesmo dinheiro várias vezes.
    """

    posicao: date
    parcelas: list[ParcelaPatrimonio]
    informado: bool
    #: Mês do fechamento usado. Pode ser anterior a ``posicao`` quando o mês
    #: em foco ainda não foi fechado — o rótulo da tela precisa dizer isso.
    fechamento_em: date | None = None

    @property
    def fechamento_desatualizado(self) -> bool:
        """Se reserva e investimentos vêm de um mês anterior ao exibido."""
        return self.fechamento_em is not None and self.fechamento_em != self.posicao

    @property
    def total_cents(self) -> int:
        """Patrimônio: só o que foi marcado como tal, sem contar duas vezes."""
        return sum(p.valor_cents for p in self.parcelas if p.no_patrimonio)

    @property
    def guardado_cents(self) -> int:
        """Todo o dinheiro separado, inclusive o que não é patrimônio.

        A diferença para :attr:`total_cents` é o dinheiro com destino certo
        de saída — envelopes de viagem, de compras. Ele existe na conta, mas
        já está comprometido.
        """
        return sum(p.valor_cents for p in self.parcelas)

    @property
    def comprometido_cents(self) -> int:
        """Guardado que não conta como patrimônio."""
        return self.guardado_cents - self.total_cents

    @property
    def parcelas_no_patrimonio(self) -> list["ParcelaPatrimonio"]:
        """Só as parcelas que compõem o patrimônio."""
        return [p for p in self.parcelas if p.no_patrimonio]

    def valor_de(self, slug: str) -> int:
        """Valor de uma parcela pelo slug da categoria."""
        for parcela in self.parcelas:
            if parcela.categoria.slug == slug:
                return parcela.valor_cents
        return 0


def get_patrimonio(session: Session, period: Period) -> Patrimonio:
    """Patrimônio acompanhado no período.

    Entram todas as categorias marcadas com ``include_in_net_worth`` — o
    cálculo não conhece "Viagem" nem "Compras" pelo nome, então categorias
    novas passam a contar sozinhas.

    No modo anual usa o fechamento mais recente **dentro do ano**; se
    dezembro ainda não foi fechado, vale a última posição informada.
    """
    if period.is_month:
        posicao = period.month
        fechamento = repo.latest_closing_until(session, posicao)
    else:
        fechamento = repo.latest_closing_in(session, period)
        posicao = fechamento.month if fechamento else period.end

    # Entram todas as categorias que retêm saldo entre meses; a marca
    # ``include_in_net_worth`` decide depois quais somam no patrimônio. Assim
    # os dois números saem da mesma leitura, e nunca discordam.
    vistas = [
        v
        for v in cat.resolve_active(session, posicao)
        if v.behavior.requires_separation
    ]
    parcelas = [
        ParcelaPatrimonio(
            categoria=v,
            valor_cents=saldo_categoria(session, v, posicao),
            no_patrimonio=v.include_in_net_worth,
        )
        for v in vistas
    ]
    return Patrimonio(
        posicao=posicao,
        parcelas=parcelas,
        informado=fechamento is not None,
        fechamento_em=fechamento.month if fechamento else None,
    )


def disponivel_no_mes(session: Session, month: date) -> int:
    """O que sobrou do orçamento de consumo do mês.

    Categorias de gasto mensal recebem uma fatia da renda e não acumulam:
    o que não foi gasto ainda está na conta, mas some do cálculo quando o
    mês vira. Por isso este número é sempre do mês em foco, nunca somado
    entre meses.

    Fica negativo se você gastou mais do que o planejado — o que é a
    verdade: o dinheiro saiu de outro lugar.
    """
    alvo = month_start(month)
    plano = get_month_plan(session, alvo)
    gastos = repo.expenses_by_category(session, Period.of_month(alvo))
    return sum(
        plano.planejado.get(v.id, 0) - gastos.get(v.id, 0)
        for v in plano.categorias
        if v.behavior.is_monthly_budget
    )


def serie_patrimonio(session: Session, meses: list[date]) -> list[tuple[date, int]]:
    """Evolução do patrimônio acompanhado, mês a mês."""
    return [
        (mes, get_patrimonio(session, Period.of_month(mes)).total_cents) for mes in meses
    ]


# --------------------------------------------------------------------------
# Resumo do período (usado pelos cards da Home)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ResumoPeriodo:
    """Números agregados de um período, mensal ou anual."""

    period: Period
    recebido_cents: int
    base_cents: int
    gasto_cents: int
    dividendos_cents: int
    patrimonio: Patrimonio
    #: Sobra do orçamento de consumo na posição do patrimônio.
    disponivel_cents: int = 0

    @property
    def dinheiro_total_cents(self) -> int:
        """Todo o dinheiro que o app consegue enxergar.

        É o guardado nas separações mais o que ainda não foi gasto do
        orçamento do mês. Não conhece o extrato do banco: só o que foi
        registrado aqui.
        """
        return self.patrimonio.guardado_cents + self.disponivel_cents


def get_resumo_periodo(session: Session, period: Period) -> ResumoPeriodo:
    """Agrega receitas, gastos, dividendos e patrimônio do período.

    Receitas, gastos e dividendos somam os meses. Patrimônio **não** soma:
    é a posição mais recente disponível.
    """
    patrimonio = get_patrimonio(session, period)
    return ResumoPeriodo(
        period=period,
        recebido_cents=repo.sum_incomes(session, period, only_renda=True),
        base_cents=repo.sum_incomes(session, period, only_budget=True),
        gasto_cents=repo.sum_expenses(session, period),
        dividendos_cents=repo.sum_dividends(session, period),
        patrimonio=patrimonio,
        disponivel_cents=disponivel_no_mes(session, patrimonio.posicao),
    )
