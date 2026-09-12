"""Filtro automático por dono em toda consulta e toda gravação.

O isolamento entre usuários não pode depender de alguém lembrar de
escrever ``WHERE user_id = ...`` em cada consulta. Basta esquecer uma vez,
num lugar pouco visitado, para vazar dado de outra pessoa.

Então o filtro é aplicado **uma vez**, aqui:

* toda consulta ORM contra um modelo que tenha ``user_id`` ganha o filtro
  do usuário logado, via ``with_loader_criteria``;
* todo objeto novo recebe o ``user_id`` do usuário logado antes de ir para
  o banco.

É a mesma ideia da RLS, aplicada um nível acima — e as duas trabalham
juntas: no PostgreSQL, mesmo que este módulo falhasse, o banco recusaria.
No SQLite dos testes, onde não existe RLS, é este módulo que garante.
"""

from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, with_loader_criteria

from . import auth
from .models import Base

#: Modelos que guardam dinheiro de alguém, descobertos do próprio mapeamento.
#: Uma tabela nova com ``user_id`` entra sozinha — não há lista para manter
#: em dia, e portanto não há como esquecer de atualizá-la.
def modelos_com_dono() -> list[type]:
    """Classes mapeadas que têm coluna ``user_id``."""
    encontrados = []
    for mapeador in Base.registry.mappers:
        classe = mapeador.class_
        if "user_id" in mapeador.columns:
            encontrados.append(classe)
    return encontrados


_MODELOS: list[type] = []


def _garantir_modelos() -> list[type]:
    global _MODELOS
    if not _MODELOS:
        _MODELOS = modelos_com_dono()
    return _MODELOS


def instalar() -> None:
    """Liga os ganchos. Chamado uma vez, na inicialização do módulo."""

    @event.listens_for(Session, "do_orm_execute")
    def _filtrar_por_dono(estado) -> None:  # type: ignore[no-untyped-def]
        if not estado.is_select or estado.is_column_load or estado.is_relationship_load:
            return
        usuario = auth.atual()
        if usuario is None:
            return
        # A criteria vai como expressão pronta, e não como lambda: lambda
        # com variável de fora não entra no cache de SQL do SQLAlchemy.
        for modelo in _garantir_modelos():
            estado.statement = estado.statement.options(
                with_loader_criteria(
                    modelo,
                    modelo.user_id == usuario.id,
                    include_aliases=True,
                )
            )

    @event.listens_for(Session, "before_flush")
    def _carimbar_dono(session: Session, _contexto, _instancias) -> None:  # type: ignore[no-untyped-def]
        usuario = auth.atual()
        if usuario is None:
            return
        for objeto in session.new:
            estado = inspect(objeto)
            if "user_id" not in estado.mapper.columns:
                continue
            if getattr(objeto, "user_id", None) is None:
                objeto.user_id = usuario.id


instalar()


def buscar(session: Session, modelo: type, **chaves: object):
    """Busca uma linha pelas chaves informadas, sempre dentro do dono.

    Substitui ``session.get()`` nos modelos financeiros por dois motivos:
    as chaves primárias passaram a ser compostas com ``user_id``, e
    ``session.get()`` responde do cache de identidade **sem** passar pelo
    filtro de dono — bastaria saber um id alheio para ler a linha.
    """
    from sqlalchemy import select

    consulta = select(modelo)
    for coluna, valor in chaves.items():
        consulta = consulta.where(getattr(modelo, coluna) == valor)
    return session.scalars(consulta).first()
