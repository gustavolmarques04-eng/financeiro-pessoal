"""Peças de interface reutilizadas pelas páginas.

Aqui só existe apresentação: seletor de período, botão de privacidade,
cartões e estilo. Nenhum cálculo financeiro — isso é dos serviços.

Duas coisas moram no ``session_state`` e valem para o app inteiro: o
**período** selecionado e o **modo privacidade**. As páginas leem os dois
por estas funções, nunca por chaves soltas.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core.database import init_db, session_scope
from core.period import Period, PeriodMode
from core.repositories import known_months
from core.utils import add_months, month_label, month_start
from ui.money import display_money, display_percent, money_md, money_plain

PERIODO_KEY = "periodo"
PRIVACIDADE_KEY = "privacidade"

#: Paleta herdada da planilha original.
NAVY = "#17324D"
TEAL = "#2A7F7F"
VERDE = "#0B6B3A"
VERMELHO = "#B3261E"
CINZA = "#667085"

CSS = f"""
<style>
  /* Folga suficiente para o conteúdo passar por baixo da barra de navegação. */
  .block-container {{ padding-top: 3rem; padding-bottom: 3rem; max-width: 1100px; }}

  /* Cabeçalho e botão de privacidade alinhados na mesma linha. */
  .st-key-barra-topo div[data-testid="stHorizontalBlock"] {{ align-items: center; }}

  .fin-header {{
    background: {NAVY}; color: #fff; border-radius: 12px;
    padding: 0.8rem 1rem; margin-bottom: 0.8rem;
  }}
  .fin-header h1 {{ font-size: 1.3rem; margin: 0; font-weight: 700; }}
  .fin-header p {{ margin: 0.15rem 0 0; opacity: 0.85; font-size: 0.88rem; }}

  .fin-card {{
    background: #fff; border: 1px solid #E4EAF1; border-radius: 12px;
    padding: 0.8rem 0.95rem; height: 100%;
    box-shadow: 0 1px 2px rgba(16,24,40,0.04);
  }}
  .fin-card .rotulo {{
    font-size: 0.7rem; letter-spacing: .06em; text-transform: uppercase;
    color: {CINZA}; font-weight: 700; margin-bottom: 0.2rem;
  }}
  .fin-card .valor {{ font-size: 1.45rem; font-weight: 700; color: {NAVY}; line-height: 1.2; }}
  .fin-card .apoio {{ font-size: 0.76rem; color: {CINZA}; margin-top: 0.15rem; }}
  .fin-card.positivo .valor {{ color: {VERDE}; }}
  .fin-card.negativo .valor {{ color: {VERMELHO}; }}

  .fin-secao {{
    background: {NAVY}; color: #fff; border-radius: 8px;
    padding: 0.42rem 0.8rem; font-weight: 700; font-size: 0.93rem;
    margin: 1.3rem 0 0.65rem;
  }}
  .fin-sub {{
    font-size: 0.72rem; letter-spacing: .06em; text-transform: uppercase;
    color: {CINZA}; font-weight: 700; margin: 0.7rem 0 0.35rem;
  }}

  .fin-linha {{
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 0.6rem; padding: 0.28rem 0; border-bottom: 1px solid #EEF3F7;
  }}
  .fin-linha .chave {{ color: {CINZA}; font-size: 0.84rem; }}
  .fin-linha .val {{ font-weight: 600; color: {NAVY}; }}

  .fin-pill {{
    display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
    font-size: 0.71rem; font-weight: 700;
  }}
  .fin-pill.ok  {{ background: #E3F5EA; color: {VERDE}; }}
  .fin-pill.pend{{ background: #FDECEA; color: {VERMELHO}; }}
  .fin-pill.parc{{ background: #FFF4E0; color: #8A5A00; }}

  .fin-neg {{ color: {VERMELHO}; font-weight: 700; }}
  .fin-pos {{ color: {VERDE}; font-weight: 700; }}
  .fin-oculto {{ color: {CINZA}; font-style: italic; }}

  /* Toque confortável no celular */
  .stButton > button {{ min-height: 2.6rem; border-radius: 9px; }}
  div[data-testid="stForm"] .stButton > button {{ width: 100%; }}

  /* A página nunca rola de lado. Conteúdo largo (tabela, gráfico) rola
     dentro do próprio quadro, que é o comportamento esperado no celular:
     arrastar a tabela, e não a tela inteira. */
  html, body {{ overflow-x: hidden; }}
  div[data-testid="stDataFrame"], .stPlotlyChart {{
    max-width: 100%; overflow-x: auto;
  }}

  /* Telas pequenas: colunas viram uma coluna só, sem zoom horizontal */
  @media (max-width: 640px) {{
    .block-container {{ padding-left: 0.75rem; padding-right: 0.75rem; padding-top: 2.6rem; }}
    div[data-testid="stHorizontalBlock"] {{ flex-direction: column; gap: 0.45rem; }}
    div[data-testid="stColumn"] {{
      width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important;
    }}
    .fin-card .valor {{ font-size: 1.35rem; }}
    .stButton > button {{ min-height: 2.9rem; font-size: 1rem; }}

    /* Barras que precisam continuar horizontais no celular */
    .st-key-nav-periodo div[data-testid="stHorizontalBlock"],
    .st-key-barra-topo div[data-testid="stHorizontalBlock"] {{
      flex-direction: row; gap: 0.4rem; align-items: center;
    }}
    .st-key-nav-periodo div[data-testid="stColumn"],
    .st-key-barra-topo div[data-testid="stColumn"] {{
      width: auto !important; min-width: 0 !important;
    }}
    .st-key-nav-periodo div[data-testid="stColumn"]:first-child,
    .st-key-nav-periodo div[data-testid="stColumn"]:last-child {{
      flex: 0 0 3rem !important;
    }}
    .st-key-nav-periodo div[data-testid="stColumn"]:nth-child(2) {{
      flex: 1 1 auto !important;
    }}
    .st-key-barra-topo div[data-testid="stColumn"]:first-child {{ flex: 1 1 auto !important; }}
    .st-key-barra-topo div[data-testid="stColumn"]:last-child {{ flex: 0 0 8.5rem !important; }}
  }}
</style>
"""


# --------------------------------------------------------------------------
# Página
# --------------------------------------------------------------------------
def configurar_pagina(titulo: str) -> None:
    """Configuração comum de página: layout, ícone e estilo."""
    st.set_page_config(
        page_title=f"{titulo} · Financeiro",
        page_icon="💰",
        layout="centered",
    )
    st.markdown(CSS, unsafe_allow_html=True)


def garantir_banco() -> None:
    """Aplica migrações e semeia o banco na primeira execução do processo."""
    if not st.session_state.get("_db_pronto"):
        init_db()
        st.session_state["_db_pronto"] = True


# --------------------------------------------------------------------------
# Modo privacidade
# --------------------------------------------------------------------------
def privacidade() -> bool:
    """Se o modo privacidade está ligado nesta sessão."""
    return bool(st.session_state.get(PRIVACIDADE_KEY, False))


def alternar_privacidade() -> None:
    """Liga/desliga o modo privacidade."""
    st.session_state[PRIVACIDADE_KEY] = not privacidade()


def dinheiro(cents: int | None) -> str:
    """Valor para markdown, já respeitando o modo privacidade."""
    return money_md(cents, privacidade())


def dinheiro_html(cents: int | None) -> str:
    """Valor para HTML e tabelas, já respeitando o modo privacidade."""
    return money_plain(cents, privacidade())


def percentual(valor: float, casas: int = 1) -> str:
    """Percentual — continua visível no modo privado por não revelar saldo."""
    return display_percent(valor, privacidade(), casas)


def valor_colorido(cents: int) -> str:
    """Valor formatado, vermelho quando negativo e verde quando positivo."""
    if privacidade():
        return f'<span class="fin-oculto">{dinheiro_html(cents)}</span>'
    classe = "fin-neg" if cents < 0 else "fin-pos"
    return f'<span class="{classe}">{dinheiro_html(cents)}</span>'


# --------------------------------------------------------------------------
# Período
# --------------------------------------------------------------------------
def periodo_atual() -> Period:
    """Período selecionado, compartilhado por todas as telas."""
    if PERIODO_KEY not in st.session_state:
        st.session_state[PERIODO_KEY] = Period.of_month(date.today())
    return st.session_state[PERIODO_KEY]


def definir_periodo(novo: Period) -> None:
    """Troca o período selecionado."""
    st.session_state[PERIODO_KEY] = novo


def mes_atual() -> date:
    """Mês de referência do período — usado por telas que só operam por mês."""
    return periodo_atual().month


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


def _anos_disponiveis(selecionado: int) -> list[int]:
    """Anos oferecidos no seletor anual."""
    with session_scope() as session:
        conhecidos = [m.year for m in known_months(session)]
    referencias = conhecidos + [selecionado, date.today().year]
    return list(range(min(referencias) - 1, max(referencias) + 3))


ANUNCIO_KEY = "_periodo_anunciado"


def _rotulo_relativo(periodo: Period) -> str:
    """Diz onde o período está em relação a hoje, em palavras."""
    hoje = month_start(date.today())
    if periodo.is_year:
        diferenca = periodo.year - hoje.year
        if diferenca == 0:
            return "ano atual"
        return f"{abs(diferenca)} ano(s) {'atrás' if diferenca < 0 else 'à frente'}"

    diferenca = (periodo.month.year - hoje.year) * 12 + (periodo.month.month - hoje.month)
    if diferenca == 0:
        return "mês atual"
    if diferenca == -1:
        return "mês passado"
    if diferenca == 1:
        return "mês que vem"
    return f"{abs(diferenca)} meses {'atrás' if diferenca < 0 else 'à frente'}"


def _anunciar_periodo(periodo: Period) -> None:
    """Avisa qual período está em foco, ao abrir e a cada troca.

    O aviso só aparece quando o período realmente muda — trocar de página
    não dispara nada.
    """
    anterior = st.session_state.get(ANUNCIO_KEY)
    if anterior == periodo:
        return

    st.session_state[ANUNCIO_KEY] = periodo
    if anterior is None:
        st.toast(f"Mostrando {periodo.label} — {_rotulo_relativo(periodo)}", icon="📅")
    else:
        st.toast(f"Período alterado para {periodo.label}", icon="📅")


def seletor_periodo(chave: str, *, permitir_anual: bool = True) -> Period:
    """Seletor global de período: modo mensal/anual e navegação ← →.

    Componente único — nenhuma página reimplementa esta lógica. Páginas que
    só fazem sentido no mês (Receitas, Gastos, Fechamento) passam
    ``permitir_anual=False`` e recebem sempre um período mensal.

    As chaves dos widgets carregam o período vigente de propósito: com uma
    chave fixa, o Streamlit preservaria o valor antigo do seletor e desfaria
    a navegação feita pelas setas.
    """
    periodo = periodo_atual()

    if permitir_anual:
        modo = st.segmented_control(
            "Modo",
            options=["Mensal", "Anual"],
            default="Mensal" if periodo.is_month else "Anual",
            key=f"{chave}_modo_{periodo.mode.value}",
            label_visibility="collapsed",
        )
        if modo == "Mensal" and periodo.is_year:
            definir_periodo(periodo.as_month())
            st.rerun()
        if modo == "Anual" and periodo.is_month:
            definir_periodo(periodo.as_year())
            st.rerun()
    elif periodo.is_year:
        # A página não trabalha por ano: mantém o mesmo ano, no modo mensal.
        definir_periodo(periodo.as_month())
        periodo = periodo_atual()

    with st.container(key="nav-periodo"):
        esquerda, centro, direita = st.columns([1, 4, 1])

        with esquerda:
            if st.button(
                "←", key=f"{chave}_ant", use_container_width=True, help="Período anterior"
            ):
                definir_periodo(periodo.shift(-1))
                st.rerun()

        with centro:
            if periodo.is_month:
                opcoes = _meses_disponiveis(periodo.month)
                if periodo.month not in opcoes:
                    opcoes = sorted({*opcoes, periodo.month})
                atual, formatar = periodo.month, month_label
            else:
                opcoes = _anos_disponiveis(periodo.year)
                atual, formatar = periodo.year, str

            escolhido = st.selectbox(
                "Período",
                options=opcoes,
                index=opcoes.index(atual),
                format_func=formatar,
                key=f"{chave}_sel_{periodo.mode.value}_{periodo.anchor:%Y%m}",
                label_visibility="collapsed",
            )

        with direita:
            if st.button(
                "→", key=f"{chave}_prox", use_container_width=True, help="Período seguinte"
            ):
                definir_periodo(periodo.shift(1))
                st.rerun()

    if escolhido != atual:
        definir_periodo(
            Period.of_month(escolhido) if periodo.is_month else Period.of_year(escolhido)
        )
        st.rerun()

    _anunciar_periodo(periodo)
    _indicador_periodo(chave, periodo)
    return periodo


def _indicador_periodo(chave: str, periodo: Period) -> None:
    """Mostra o período em foco e oferece a volta para hoje."""
    relativo = _rotulo_relativo(periodo)
    if relativo in ("mês atual", "ano atual"):
        st.caption(f"📅 **{periodo.label}** — {relativo}")
        return

    coluna_texto, coluna_botao = st.columns([3, 1])
    coluna_texto.caption(f"📅 **{periodo.label}** — {relativo}")
    if coluna_botao.button(
        "Voltar para hoje", key=f"{chave}_hoje", use_container_width=True
    ):
        hoje = date.today()
        definir_periodo(
            Period.of_month(hoje) if periodo.is_month else Period.of_year(hoje.year)
        )
        st.rerun()


# --------------------------------------------------------------------------
# Blocos visuais
# --------------------------------------------------------------------------
def cabecalho(titulo: str, subtitulo: str = "", *, chave: str = "topo") -> None:
    """Faixa de título com o botão global de privacidade."""
    with st.container(key="barra-topo"):
        esquerda, direita = st.columns([3, 1])
        with esquerda:
            extra = f"<p>{subtitulo}</p>" if subtitulo else ""
            st.markdown(
                f'<div class="fin-header"><h1>{titulo}</h1>{extra}</div>',
                unsafe_allow_html=True,
            )
        with direita:
            # O ícone mostra o estado atual: olho aberto = valores à vista,
            # olho riscado = valores escondidos. O texto diz o que o clique faz.
            oculto = privacidade()
            if st.button(
                "Mostrar" if oculto else "Ocultar",
                icon=":material/visibility_off:" if oculto else ":material/visibility:",
                key=f"{chave}_privacidade",
                use_container_width=True,
                help=(
                    "Voltar a exibir os valores."
                    if oculto
                    else "Esconde todos os valores em todas as telas."
                ),
            ):
                alternar_privacidade()
                st.rerun()


def cartao(rotulo: str, valor: str, apoio: str = "", tom: str = "") -> str:
    """HTML de um cartão de indicador. ``tom`` aceita ``positivo``/``negativo``.

    O conteúdo vira HTML puro, onde o markdown não roda — então o escape do
    cifrão é desfeito aqui.
    """
    classe = f"fin-card {tom}".strip()
    valor, apoio = valor.replace(r"\$", "$"), apoio.replace(r"\$", "$")
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


def subtitulo(texto: str) -> None:
    """Rótulo discreto para separar blocos dentro de uma seção."""
    st.markdown(f'<div class="fin-sub">{texto}</div>', unsafe_allow_html=True)


def linha(chave: str, valor_html: str) -> str:
    """HTML de uma linha chave/valor."""
    return (
        f'<div class="fin-linha"><span class="chave">{chave}</span>'
        f'<span class="val">{valor_html}</span></div>'
    )


def selo(status: str) -> str:
    """Selo visual de status de separação."""
    mapa = {"Feito": "ok", "Pendente": "pend", "Parcial": "parc", "Sem valor": "parc"}
    return f'<span class="fin-pill {mapa.get(status, "parc")}">{status}</span>'


def aviso_vazio(texto: str) -> None:
    """Mensagem padrão para listas sem registros."""
    st.info(texto, icon="🗒️")


def aviso_valores_ocultos() -> None:
    """Substitui um gráfico monetário quando o modo privado está ligado."""
    st.info(
        "Valores ocultos. Toque em **Mostrar**, no topo, para ver o gráfico.",
        icon=":material/visibility_off:",
    )
