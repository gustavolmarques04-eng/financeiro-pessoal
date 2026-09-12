"""Montagem do plano no primeiro acesso.

O usuário descreve as categorias dele numa tela só e grava tudo de uma
vez. "De uma vez" é o ponto: se as categorias fossem salvas uma a uma, um
erro no meio deixaria o banco com um plano somando 63%, e o aplicativo
inteiro é construído sobre a premissa de que o plano fecha em 100%.

Por isso existe :func:`criar_plano_inicial`: valida o conjunto **antes** de
escrever qualquer linha e grava tudo na mesma transação.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from . import categories as cat
from . import profile_service
from . import repositories as repo
from .categories import CategoryError, TOTAL_BP
from .models import CategoryBehavior
from .utils import month_start


@dataclass
class CategoriaDesejada:
    """Uma categoria descrita pelo usuário no primeiro acesso."""

    nome: str
    percent_bp: int
    emoji: str | None = None
    #: Meta opcional, em centavos. ``None`` significa "sem meta".
    meta_cents: int | None = None
    #: Quanto já existe guardado nessa categoria hoje.
    saldo_inicial_cents: int = 0
    #: Se o dinheiro dela faz parte do patrimônio que está sendo construído.
    conta_no_patrimonio: bool = True
    #: Se ela rende dividendos. Quando sim, o Fechamento pergunta quanto
    #: rendeu no mês, e o valor entra nela.
    rende_dividendos: bool = False


@dataclass
class PlanoInicial:
    """Tudo que o primeiro acesso coleta."""

    categorias: list[CategoriaDesejada] = field(default_factory=list)
    #: Nome (não id: as categorias ainda não existem) da que representa
    #: investimentos e da que representa a reserva. Opcionais.
    investimentos: str | None = None
    reserva: str | None = None
    investimento_aportado_cents: int | None = None


def validar(plano: PlanoInicial) -> None:
    """Recusa um plano que não pode virar banco de dados.

    Roda antes de qualquer escrita: é mais fácil recusar por inteiro do que
    desfazer pela metade.
    """
    if not plano.categorias:
        raise CategoryError("Crie ao menos uma categoria para continuar.")

    nomes = [c.nome.strip() for c in plano.categorias]
    if any(not nome for nome in nomes):
        raise CategoryError("Toda categoria precisa de um nome.")

    vistos: set[str] = set()
    for nome in nomes:
        chave = nome.casefold()
        if chave in vistos:
            raise CategoryError(f"Há duas categorias chamadas “{nome}”.")
        vistos.add(chave)

    if any(c.percent_bp < 0 for c in plano.categorias):
        raise CategoryError("Percentual não pode ser negativo.")

    total = sum(c.percent_bp for c in plano.categorias)
    if total != TOTAL_BP:
        diferenca = abs(total - TOTAL_BP) / 100
        lado = "Faltam" if total < TOTAL_BP else "Excesso de"
        raise CategoryError(
            f"Os percentuais somam {total / 100:.2f}%. {lado} {diferenca:.2f}% "
            "para fechar 100%."
        )

    for rotulo, escolhido in (
        ("investimentos", plano.investimentos),
        ("reserva", plano.reserva),
    ):
        if escolhido and escolhido not in nomes:
            raise CategoryError(
                f"A categoria escolhida como {rotulo} não está na lista."
            )


def criar_plano_inicial(
    session: Session, plano: PlanoInicial, *, a_partir_de: date
) -> dict[str, int]:
    """Grava o plano inteiro e conclui o primeiro acesso.

    Devolve ``nome -> category_id``. Tudo acontece na transação de quem
    chama: se qualquer passo falhar, nada é gravado.
    """
    validar(plano)
    mes = month_start(a_partir_de)

    criadas: dict[str, int] = {}
    for ordem, desejada in enumerate(plano.categorias, start=1):
        categoria = cat.criar_categoria(
            session,
            name=desejada.nome.strip(),
            # Todas as categorias funcionam igual agora; o comportamento
            # existe só como registro, e não manda em nenhuma regra.
            behavior=CategoryBehavior.ACCUMULATING_ENVELOPE,
            percent_bp=desejada.percent_bp,
            effective_month=mes,
            emoji=(desejada.emoji or None),
            display_order=ordem,
            target_amount_cents=desejada.meta_cents,
            include_in_net_worth=desejada.conta_no_patrimonio,
            accumulates_balance=True,
            receives_dividends=desejada.rende_dividendos,
        )
        criadas[desejada.nome.strip()] = categoria.id

        if desejada.saldo_inicial_cents:
            repo.set_opening_balance(
                session, categoria.id, desejada.saldo_inicial_cents
            )

    # Confere o que foi realmente gravado, e não o que se pretendia gravar.
    cat.validar_total(cat.resolve_active(session, mes))

    profile_service.atualizar(
        session,
        investment_category_id=criadas.get(plano.investimentos or ""),
        reserve_category_id=criadas.get(plano.reserva or ""),
        investment_cost_basis_cents=plano.investimento_aportado_cents,
        onboarding_completed=True,
    )
    repo.bump_revisions_from(session, mes)
    return criadas
