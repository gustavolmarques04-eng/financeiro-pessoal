"""Primeiro acesso: como você quer dividir seu dinheiro.

Tudo numa página só, em passos. O botão de concluir fica desligado
enquanto os percentuais não fecharem 100% — e a gravação acontece de uma
vez, para o banco nunca guardar um plano pela metade.

A linguagem aqui é a de quem usa, não a do código: "separação", "meta",
"patrimônio". Nada de comportamento, versão vigente ou pontos-base.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import onboarding_service as onboarding
from core import profile_service
from core.categories import CategoryError
from core.database import session_scope
from core.utils import month_start, to_cents
from ui.login import require_auth

# Antes de qualquer leitura do banco: sem usuário, a página nem começa.
usuario = require_auth()

from ui.shared import cabecalho, dinheiro, secao  # noqa: E402

#: Onde as linhas em edição ficam entre um clique e outro.
CHAVE = "_onboarding_linhas"

LINHA_VAZIA = {
    "nome": "",
    "emoji": "",
    "percentual": 0.0,
    "meta": 0.0,
    "ja_tenho": 0.0,
    "patrimonio": True,
    "dividendos": False,
}


def _linhas() -> list[dict]:
    if CHAVE not in st.session_state:
        st.session_state[CHAVE] = [dict(LINHA_VAZIA) for _ in range(3)]
    return st.session_state[CHAVE]


def _total_bp(linhas: list[dict]) -> int:
    """Soma dos percentuais em pontos-base, sem passar por float."""
    return sum(int(round(float(linha["percentual"]) * 100)) for linha in linhas)


cabecalho(
    "👋 Bem-vindo",
    "Vamos montar a sua divisão do dinheiro. Leva dois minutos.",
    chave="onb",
)

linhas = _linhas()

# --------------------------------------------------------------------------
# Passo 1 e 2 — categorias e percentuais
# --------------------------------------------------------------------------
secao("1. Como você quer dividir seu dinheiro?")
st.caption(
    "Crie uma linha para cada destino do seu dinheiro. Não precisa acertar "
    "de primeira: dá para mudar tudo depois."
)

for indice, linha in enumerate(linhas):
    with st.container(border=True):
        coluna_nome, coluna_emoji, coluna_pct = st.columns([5, 2, 3])
        linha["nome"] = coluna_nome.text_input(
            "Nome", value=linha["nome"], key=f"onb_nome_{indice}",
            placeholder="Reserva, Viagem, Livre…",
        )
        linha["emoji"] = coluna_emoji.text_input(
            "Emoji", value=linha["emoji"], key=f"onb_emoji_{indice}",
            max_chars=4, placeholder="🛟",
        )
        linha["percentual"] = coluna_pct.number_input(
            "% da renda", min_value=0.0, max_value=100.0, step=1.0,
            value=float(linha["percentual"]), key=f"onb_pct_{indice}",
        )

        with st.expander("Opcional: meta, saldo e patrimônio", expanded=False):
            linha["meta"] = st.number_input(
                "Quero juntar até (deixe 0 para não ter meta)",
                min_value=0.0, step=100.0, value=float(linha["meta"]),
                key=f"onb_meta_{indice}",
            )
            linha["ja_tenho"] = st.number_input(
                "Quanto já tenho guardado nessa categoria hoje",
                min_value=0.0, step=100.0, value=float(linha["ja_tenho"]),
                key=f"onb_saldo_{indice}",
            )
            linha["patrimonio"] = st.toggle(
                "Esse dinheiro faz parte do patrimônio que você está construindo?",
                value=bool(linha["patrimonio"]),
                key=f"onb_patr_{indice}",
                help=(
                    "Sim para reserva, investimento, viagem guardada. "
                    "Não para dinheiro livre, diversão, gasto pessoal."
                ),
            )
            linha["dividendos"] = st.toggle(
                "Essa categoria rende dividendos?",
                value=bool(linha["dividendos"]),
                key=f"onb_div_{indice}",
                help=(
                    "O Fechamento vai perguntar quanto ela rendeu no mês, "
                    "e o valor soma ao saldo dela."
                ),
            )

coluna_add, coluna_rem = st.columns(2)
if coluna_add.button("➕ Adicionar categoria", use_container_width=True):
    linhas.append(dict(LINHA_VAZIA))
    st.rerun()
if coluna_rem.button("➖ Remover a última", use_container_width=True, disabled=len(linhas) <= 1):
    linhas.pop()
    st.rerun()

# --------------------------------------------------------------------------
# O total, sempre à vista
# --------------------------------------------------------------------------
total = _total_bp(linhas)
fecha = total == 10_000
if fecha:
    st.success("Total: 100,00% ✅", icon="✅")
elif total < 10_000:
    st.warning(
        f"Total: {total / 100:.2f}%  ·  Faltam {(10_000 - total) / 100:.2f}%",
        icon="⚠️",
    )
else:
    st.error(
        f"Total: {total / 100:.2f}%  ·  Excesso de {(total - 10_000) / 100:.2f}%",
        icon="⚠️",
    )

# --------------------------------------------------------------------------
# Passo 6 — reserva e investimentos (opcional)
# --------------------------------------------------------------------------
nomes = [linha["nome"].strip() for linha in linhas if linha["nome"].strip()]

secao("2. Alguma delas é sua reserva ou seus investimentos?")
st.caption("É opcional, e serve só para o app montar alguns gráficos melhores.")

sem = "— Nenhuma —"
coluna_inv, coluna_res = st.columns(2)
investimentos = coluna_inv.selectbox(
    "Meus investimentos", options=[sem, *nomes], key="onb_inv"
)
reserva = coluna_res.selectbox(
    "Minha reserva de emergência", options=[sem, *nomes], key="onb_res"
)

aportado = None
if investimentos != sem:
    escolhida = next(
        (linha for linha in linhas if linha["nome"].strip() == investimentos), None
    )
    if escolhida and float(escolhida["ja_tenho"]) > 0:
        aportado = st.number_input(
            "Desse valor, quanto foi dinheiro que você colocou?",
            min_value=0.0,
            max_value=float(escolhida["ja_tenho"]),
            value=float(escolhida["ja_tenho"]),
            step=100.0,
            key="onb_aportado",
            help=(
                "O resto é rendimento. Serve para o app calcular seu ganho; "
                "não muda saldo nenhum."
            ),
        )

# --------------------------------------------------------------------------
# Concluir
# --------------------------------------------------------------------------
st.divider()
if not fecha:
    st.caption("O botão libera quando os percentuais somarem exatamente 100%.")

if st.button(
    "Concluir e começar a usar",
    type="primary",
    use_container_width=True,
    disabled=not fecha,
):
    plano = onboarding.PlanoInicial(
        categorias=[
            onboarding.CategoriaDesejada(
                nome=linha["nome"].strip(),
                percent_bp=int(round(float(linha["percentual"]) * 100)),
                emoji=(linha["emoji"].strip() or None),
                meta_cents=(to_cents(linha["meta"]) or None),
                saldo_inicial_cents=to_cents(linha["ja_tenho"]),
                conta_no_patrimonio=bool(linha["patrimonio"]),
                rende_dividendos=bool(linha["dividendos"]),
            )
            for linha in linhas
            if linha["nome"].strip()
        ],
        investimentos=(investimentos if investimentos != sem else None),
        reserva=(reserva if reserva != sem else None),
        investimento_aportado_cents=(
            to_cents(aportado) if aportado is not None else None
        ),
    )
    try:
        with session_scope() as session:
            onboarding.criar_plano_inicial(
                session, plano, a_partir_de=month_start(date.today())
            )
    except CategoryError as erro:
        st.error(str(erro))
    else:
        st.session_state.pop(CHAVE, None)
        st.balloons()
        st.rerun()
