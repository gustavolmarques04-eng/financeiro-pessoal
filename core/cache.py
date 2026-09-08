"""Cache de leitura com o tempo de vida de uma sessão.

Uma tela pede os mesmos dados várias vezes: o plano do mês, as categorias
vigentes, os fechamentos. Com o banco na nuvem, cada ida custa latência —
e o Streamlit reexecuta a página inteira a cada clique.

O cache vive dentro da própria ``Session``, então dura exatamente um bloco
``session_scope()``: some junto com ela. Qualquer escrita chama
:func:`limpar`, para nenhuma leitura seguinte enxergar dado velho.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")

#: Prefixo das chaves guardadas em ``Session.info``.
PREFIXO = "_cache_"


def obter(session: Session, chave: str, calcular: Callable[[], T]) -> T:
    """Devolve o valor em cache ou calcula e guarda."""
    completa = f"{PREFIXO}{chave}"
    if completa not in session.info:
        session.info[completa] = calcular()
    return session.info[completa]


def limpar(session: Session) -> None:
    """Descarta tudo que estiver em cache nesta sessão."""
    for chave in [c for c in session.info if str(c).startswith(PREFIXO)]:
        del session.info[chave]
