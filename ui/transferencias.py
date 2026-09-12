"""Telas de dinheiro mudando de categoria.

Duas situações, o mesmo movimento por baixo: mandar a sobra de um envelope
para outro lugar, e escolher de onde sai o dinheiro quando um gasto passa
do disponível.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import budget_service as budget
from core.categories import CategoryView
from core.database import session_scope
from core.utils import to_cents, to_decimal

from .shared import dinheiro, privacidade

#: Rótulo da opção que não move dinheiro nenhum.
DEIXAR_NEGATIVO = "Deixar negativo (desconta do próximo mês)"


def botao_transferir(
    categoria: CategoryView, saldo_cents: int, mes: date
) -> None:
    """Abre, a partir do saldo, a transferência para outra categoria.

    O valor mostrado é o **saldo** da categoria, e não uma sobra: inclui o
    que a pessoa declarou já ter guardado. Chamar isso de sobra sugeriria
    que é dinheiro excedente, quando muitas vezes é exatamente o oposto —
    é a reserva dela.
    """
    rotulo = "Transferir"
    with st.popover(rotulo, use_container_width=True):
        st.caption(
            f"Há {dinheiro(saldo_cents)} em {categoria.name}. "
            "Mover quanto, e para onde?"
        )
        with session_scope() as session:
            destinos = budget.fontes_para_cobrir(
                session, mes, excluindo=categoria.id, minimo_cents=0
            )
        if not destinos:
            st.info("Não há outra categoria para receber.", icon="🗒️")
            return

        chave = f"envio_{categoria.id}_{mes:%Y%m}"
        escolha = st.selectbox(
            "Destino",
            options=[v for v, _ in destinos],
            format_func=lambda v: v.label,
            key=f"{chave}_destino",
        )
        valor = st.number_input(
            "Valor",
            min_value=0.01,
            max_value=float(to_decimal(saldo_cents)),
            value=float(to_decimal(saldo_cents)),
            step=10.0,
            key=f"{chave}_valor",
        )
        if st.button("Enviar", key=f"{chave}_ok", type="primary"):
            with session_scope() as session:
                budget.transferir(
                    session,
                    month=mes,
                    origem_id=categoria.id,
                    destino_id=escolha.id,
                    valor_cents=to_cents(valor),
                    note=f"De {categoria.name}",
                )
            st.toast(f"Enviado para {escolha.name}.", icon="✅")
            st.rerun()


def escolher_origem(
    categoria: CategoryView, falta_cents: int, mes: date, *, chave: str
) -> int | None:
    """Pergunta de onde sai o dinheiro que faltou.

    Devolve o id da categoria escolhida, ou ``None`` para deixar o saldo
    negativo — que é uma resposta legítima: a dívida atravessa o mês e some
    quando o orçamento do mês seguinte entrar.
    """
    st.warning(
        f"Este gasto passa {dinheiro(falta_cents)} do disponível em "
        f"{categoria.name}. De onde sai a diferença?",
        icon="⚠️",
    )
    with session_scope() as session:
        fontes = budget.fontes_para_cobrir(
            session, mes, excluindo=categoria.id, minimo_cents=falta_cents
        )

    opcoes: list[tuple[str, int | None]] = [(DEIXAR_NEGATIVO, None)]
    opcoes += [(f"{v.label} — tem {dinheiro(s)}", v.id) for v, s in fontes]

    escolhido = st.radio(
        "Origem do dinheiro",
        options=[valor for _, valor in opcoes],
        format_func=lambda valor: next(r for r, v in opcoes if v == valor),
        key=f"{chave}_origem",
    )
    if not fontes:
        st.caption("Nenhuma categoria tem saldo suficiente para cobrir sozinha.")
    return escolhido
