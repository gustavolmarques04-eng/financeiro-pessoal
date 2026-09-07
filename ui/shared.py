"""Peças de interface reutilizadas pelas páginas.

Aqui só existe apresentação: seletor de mês, cartões, estilo e formatação.
Nenhum cálculo financeiro — isso é responsabilidade dos serviços.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core.database import init_db, session_scope
from core.repositories import known_months
from core.utils import add_months, format_brl, month_label, month_start

MES_KEY = "mes_selecionado"

#: Paleta herdada da planilha original.
NAVY = "#17324D"
TEAL = "#2A7F7F"
VERDE = "#0B6B3A"
VERMELHO = "#B3261E"
CINZA = "#667085"

CSS = f"""
<style>
  .block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1100px; }}

  .fin-header {{
    background: {NAVY}; color: #fff; border-radius: 12px;
    padding: 0.9rem 1.1rem; margin-bottom: 1.1rem;
  }}
  .fin-header h1 {{ font-size: 1.35rem; margin: 0; font-weight: 700; }}
  .fin-header p {{ margin: 0.15rem 0 0; opacity: 0.85; font-size: 0.9rem; }}

  .fin-card {{
    background: #fff; border: 1px solid #E4EAF1; border-radius: 12px;
    padding: 0.85rem 1rem; height: 100%;
    box-shadow: 0 1px 2px rgba(16,24,40,0.04);
  }}
  .fin-card .rotulo {{
    font-size: 0.72rem; letter-spacing: .06em; text-transform: uppercase;
    color: {CINZA}; font-weight: 700; margin-bottom: 0.25rem;
  }}
  .fin-card .valor {{ font-size: 1.5rem; font-weight: 700; color: {NAVY}; line-height: 1.2; }}
  .fin-card .apoio {{ font-size: 0.78rem; color: {CINZA}; margin-top: 0.15rem; }}
  .fin-card.positivo .valor {{ color: {VERDE}; }}
  .fin-card.negativo .valor {{ color: {VERMELHO}; }}

  .fin-secao {{
    background: {NAVY}; color: #fff; border-radius: 8px;
    padding: 0.45rem 0.8rem; font-weight: 700; font-size: 0.95rem;
    margin: 1.4rem 0 0.7rem;
  }}

  .fin-linha {{
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 0.6rem; padding: 0.3rem 0; border-bottom: 1px solid #EEF3F7;
  }}
  .fin-linha .chave {{ color: {CINZA}; font-size: 0.85rem; }}
  .fin-linha .val {{ font-weight: 600; color: {NAVY}; }}

  .fin-pill {{
    display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
    font-size: 0.72rem; font-weight: 700;
  }}
  .fin-pill.ok  {{ background: #E3F5EA; color: {VERDE}; }}
  .fin-pill.pend{{ background: #FDECEA; color: {VERMELHO}; }}
  .fin-pill.parc{{ background: #FFF4E0; color: #8A5A00; }}

  .fin-neg {{ color: {VERMELHO}; font-weight: 700; }}
  .fin-pos {{ color: {VERDE}; font-weight: 700; }}

  /* Toque confortável no celular */
  .stButton > button {{ min-height: 2.6rem; border-radius: 9px; }}
  div[data-testid="stForm"] .stButton > button {{ width: 100%; }}

  /* Telas pequenas: colunas viram uma coluna só, sem zoom horizontal */
  @media (max-width: 640px) {{
    .block-container {{ padding-left: 0.8rem; padding-right: 0.8rem; padding-top: 1.2rem; }}
    div[data-testid="stHorizontalBlock"] {{ flex-direction: column; gap: 0.5rem; }}
    div[data-testid="stColumn"] {{
      width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important;
    }}
    .fin-card .valor {{ font-size: 1.35rem; }}
    .stButton > button {{ min-height: 2.9rem; font-size: 1rem; }}

    /* O seletor de mês continua em uma linha só: ←  mês  → */
    .st-key-seletor-mes div[data-testid="stHorizontalBlock"] {{
      flex-direction: row; gap: 0.4rem; align-items: center;
    }}
    .st-key-seletor-mes div[data-testid="stColumn"] {{
      width: auto !important; min-width: 0 !important;
    }}
    .st-key-seletor-mes div[data-testid="stColumn"]:first-child,
    .st-key-seletor-mes div[data-testid="stColumn"]:last-child {{
      flex: 0 0 3.2rem !important;
    }}
    .st-key-seletor-mes div[data-testid="stColumn"]:nth-child(2) {{
      flex: 1 1 auto !important;
    }}
  }}
</style>
"""


def configurar_pagina(titulo: str) -> None:
    """Configuração comum de página: layout, ícone e estilo."""
    st.set_page_config(
        page_title=f"{titulo} · Financeiro",
        page_icon="💰",
        layout="centered",
    )
    st.markdown(CSS, unsafe_allow_html=True)


def garantir_banco() -> None:
    """Cria o banco na primeira execução do processo."""
    if not st.session_state.get("_db_pronto"):
        init_db()
        st.session_state["_db_pronto"] = True


def mes_atual() -> date:
    """Mês selecionado, guardado no ``session_state`` e válido em todas as telas."""
    if MES_KEY not in st.session_state:
        st.session_state[MES_KEY] = month_start(date.today())
    return st.session_state[MES_KEY]


def definir_mes(novo: date) -> None:
    """Troca o mês selecionado."""
    st.session_state[MES_KEY] = month_start(novo)


def _meses_disponiveis(selecionado: date) -> list[date]:
    """Lista de meses do seletor: histórico conhecido com folga nas pontas."""
    with session_scope() as session:
        conhecidos = known_months(session)

    referencias = conhecidos + [selecionado, month_start(date.today())]
    inicio = add_months(min(referencias), -6)
    fim = add_months(max(referencias), 12)

    meses, atual = [], inicio
    while atual <= fim:
        meses.append(atual)
        atual = add_months(atual, 1)
    return meses


def cabecalho(titulo: str, subtitulo: str = "") -> None:
    """Faixa de título no topo da página."""
    extra = f"<p>{subtitulo}</p>" if subtitulo else ""
    st.markdown(
        f'<div class="fin-header"><h1>{titulo}</h1>{extra}</div>',
        unsafe_allow_html=True,
    )


def seletor_mes(chave: str) -> date:
    """Seletor de mês com botões ← e →. Devolve o mês escolhido.

    ``chave`` diferencia os widgets entre páginas; o valor em si é
    compartilhado por todas via ``session_state``.
    """
    selecionado = mes_atual()
    meses = _meses_disponiveis(selecionado)
    if selecionado not in meses:
        meses.append(selecionado)
        meses.sort()

    # O container nomeado vira a classe .st-key-seletor-mes, usada pelo CSS
    # para manter os três controles lado a lado também no celular.
    with st.container(key="seletor-mes"):
        esquerda, centro, direita = st.columns([1, 4, 1])

        with esquerda:
            if st.button(
                "←", key=f"{chave}_ant", help="Mês anterior", use_container_width=True
            ):
                definir_mes(add_months(selecionado, -1))
                st.rerun()

        with centro:
            escolhido = st.selectbox(
                "Mês analisado",
                options=meses,
                index=meses.index(selecionado),
                format_func=month_label,
                key=f"{chave}_sel",
                label_visibility="collapsed",
            )

        with direita:
            if st.button(
                "→", key=f"{chave}_prox", help="Mês seguinte", use_container_width=True
            ):
                definir_mes(add_months(selecionado, 1))
                st.rerun()

    if escolhido != selecionado:
        definir_mes(escolhido)
        st.rerun()
    return selecionado


def _sem_escape(texto: str) -> str:
    """Remove o escape do cifrão, para uso dentro de HTML."""
    return texto.replace(r"\$", "$")


def cartao(rotulo: str, valor: str, apoio: str = "", tom: str = "") -> str:
    """HTML de um cartão de indicador. ``tom`` aceita ``positivo``/``negativo``.

    O conteúdo vira HTML puro, onde o markdown não roda — então o escape do
    cifrão que ``brl`` aplica é desfeito aqui.
    """
    classe = f"fin-card {tom}".strip()
    valor, apoio = _sem_escape(valor), _sem_escape(apoio)
    linha_apoio = f'<div class="apoio">{apoio}</div>' if apoio else ""
    return (
        f'<div class="{classe}"><div class="rotulo">{rotulo}</div>'
        f'<div class="valor">{valor}</div>{linha_apoio}</div>'
    )


def mostrar_cartoes(itens: list[tuple[str, str, str, str]], por_linha: int = 2) -> None:
    """Desenha cartões em grade. No celular o CSS empilha em uma coluna."""
    for inicio in range(0, len(itens), por_linha):
        bloco = itens[inicio : inicio + por_linha]
        colunas = st.columns(len(bloco))
        for coluna, (rotulo, valor, apoio, tom) in zip(colunas, bloco):
            with coluna:
                st.markdown(cartao(rotulo, valor, apoio, tom), unsafe_allow_html=True)


def secao(titulo: str) -> None:
    """Faixa de título de seção."""
    st.markdown(f'<div class="fin-secao">{titulo}</div>', unsafe_allow_html=True)


def brl(cents: int) -> str:
    """Valor em reais pronto para markdown.

    O Streamlit lê ``$...$`` como LaTeX, então dois "R$" no mesmo texto
    viravam fórmula. Escapar o cifrão resolve. Use esta função em texto
    markdown; dentro de HTML use ``format_brl``, porque lá o markdown não roda.
    """
    return format_brl(cents).replace("$", r"\$")


def valor_colorido(cents: int) -> str:
    """Valor formatado, vermelho quando negativo e verde quando positivo."""
    classe = "fin-neg" if cents < 0 else "fin-pos"
    return f'<span class="{classe}">{format_brl(cents)}</span>'


def selo(status: str) -> str:
    """Selo visual de status de separação."""
    mapa = {"Feito": "ok", "Pendente": "pend", "Parcial": "parc", "Sem valor": "parc"}
    return f'<span class="fin-pill {mapa.get(status, "parc")}">{status}</span>'


def aviso_vazio(texto: str) -> None:
    """Mensagem padrão para listas sem registros."""
    st.info(texto, icon="🗒️")
