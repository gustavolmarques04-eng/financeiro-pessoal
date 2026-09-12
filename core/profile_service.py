"""Preferências de quem usa o aplicativo.

Guarda o que é escolha da pessoa e não cabe em nenhuma categoria: se o
primeiro acesso já foi concluído, e **quais** categorias ela considera
"meus investimentos" e "minha reserva".

Essas duas são guardadas por ``category_id``, nunca por nome. Renomear
"Reserva" para "Emergência" não pode quebrar gráfico nenhum, e a Melissa
pode chamar a dela de qualquer coisa sem que o app precise reconhecer a
palavra.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, cache
from .models import Profile


@dataclass(frozen=True)
class Preferencias:
    """Visão imutável do perfil, para as telas lerem sem tocar no ORM."""

    onboarding_completed: bool
    display_name: str | None
    investment_category_id: int | None
    reserve_category_id: int | None
    investment_cost_basis_cents: int | None


def _linha(session: Session) -> Profile:
    """Perfil do usuário logado, criado na primeira vez que é pedido."""
    dono = auth.dono()
    perfil = session.scalars(
        select(Profile).where(Profile.user_id == dono)
    ).first()
    if perfil is None:
        perfil = Profile(user_id=dono)
        session.add(perfil)
        session.flush()
        cache.limpar(session)
    return perfil


def obter(session: Session) -> Preferencias:
    """Preferências do usuário logado."""

    def calcular() -> Preferencias:
        perfil = _linha(session)
        return Preferencias(
            onboarding_completed=bool(perfil.onboarding_completed),
            display_name=perfil.display_name,
            investment_category_id=perfil.investment_category_id,
            reserve_category_id=perfil.reserve_category_id,
            investment_cost_basis_cents=perfil.investment_cost_basis_cents,
        )

    return cache.obter(session, "perfil", calcular)


def atualizar(session: Session, **campos: object) -> Preferencias:
    """Grava preferências. Campos não informados ficam como estavam."""
    perfil = _linha(session)
    permitidos = {
        "display_name",
        "onboarding_completed",
        "investment_category_id",
        "reserve_category_id",
        "investment_cost_basis_cents",
    }
    for nome, valor in campos.items():
        if nome not in permitidos:
            raise ValueError(f"Campo desconhecido no perfil: {nome}")
        setattr(perfil, nome, valor)
    session.flush()
    cache.limpar(session)
    return obter(session)


def concluir_onboarding(session: Session) -> None:
    """Marca o primeiro acesso como concluído."""
    atualizar(session, onboarding_completed=True)


def precisa_de_onboarding(session: Session) -> bool:
    """Se o usuário ainda não montou a divisão dele."""
    return not obter(session).onboarding_completed
