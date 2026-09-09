"""Formulários compartilhados entre páginas.

O formulário de gasto vive aqui porque é usado em dois lugares — o atalho
da Home e a página Gastos. Uma implementação só, chamando o mesmo
repositório: não existe uma segunda regra de gasto escondida na Home.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

from core import categories as cat
from core.categories import CategoryView
from core.database import session_scope
from core.models import PaymentMethod
from core.repositories import create_expense, update_expense
from core.utils import (
    month_label,
    month_start,
    split_installments,
    to_cents,
    to_decimal,
)


@dataclass(frozen=True)
class DadosGasto:
    """Valores já validados de um gasto, prontos para o repositório."""

    purchase_date: date
    description: str
    category_id: int
    total_cents: int
    payment_method: PaymentMethod
    installments_count: int
    first_installment_month: date
    note: str | None


def categorias_para(month: date, *, incluir_id: int | None = None) -> list[CategoryView]:
    """Categorias oferecidas no formulário para a data da compra.

    São as ativas naquele mês. ``incluir_id`` garante que a categoria de um
    lançamento antigo continue na lista mesmo depois de desativada — assim
    dá para editar um gasto de 2026 em 2027 sem perder a classificação.
    """
    with session_scope() as session:
        ativas = cat.resolve_active(session, month)
        if incluir_id is not None and all(v.id != incluir_id for v in ativas):
            historica = cat.get_view(session, incluir_id, month)
            if historica is None:
                historica = cat.get_view(session, incluir_id, date.today())
            if historica is not None:
                ativas = [*ativas, historica]
    return ativas


def _campos_gasto(
    prefixo: str,
    *,
    inicial: DadosGasto | None,
    mes_referencia: date,
    compacto: bool,
) -> tuple[DadosGasto | None, str]:
    """Desenha os campos e devolve os dados ou a mensagem de erro."""
    padrao_data = inicial.purchase_date if inicial else (
        date.today() if month_start(date.today()) == mes_referencia else mes_referencia
    )

    colunas = st.columns(1) if compacto else st.columns(2)
    esquerda = colunas[0]
    direita = colunas[0] if compacto else colunas[1]

    with esquerda:
        data_compra = st.date_input(
            "Data da compra", value=padrao_data, format="DD/MM/YYYY", key=f"{prefixo}_data"
        )
    mes_da_compra = month_start(data_compra)
    opcoes = categorias_para(
        mes_da_compra, incluir_id=inicial.category_id if inicial else None
    )
    indice = 0
    if inicial is not None:
        indice = next(
            (i for i, v in enumerate(opcoes) if v.id == inicial.category_id), 0
        )

    with esquerda:
        categoria = st.selectbox(
            "Categoria",
            options=opcoes,
            index=indice if opcoes else None,
            format_func=lambda v: v.label,
            key=f"{prefixo}_cat",
            help=f"Categorias válidas em {month_label(mes_da_compra)}.",
        )
    with direita:
        descricao = st.text_input(
            "Descrição",
            value=inicial.description if inicial else "",
            placeholder="Ex.: jantar de aniversário",
            key=f"{prefixo}_desc",
        )
        valor_total = st.number_input(
            "Valor total (R$)",
            min_value=0.0,
            step=25.0,
            value=float(to_decimal(inicial.total_cents)) if inicial else 0.0,
            key=f"{prefixo}_valor",
        )
        meios = list(PaymentMethod)
        meio = st.selectbox(
            "Meio de pagamento",
            options=meios,
            index=meios.index(inicial.payment_method) if inicial else 0,
            format_func=lambda m: m.value,
            key=f"{prefixo}_meio",
        )

    parcelado = st.checkbox(
        "Parcelado?",
        value=bool(inicial and inicial.installments_count > 1),
        key=f"{prefixo}_parcelado",
    )
    col_a, col_b = st.columns(2)
    with col_a:
        num_parcelas = st.number_input(
            "Número de parcelas",
            min_value=1,
            max_value=72,
            value=int(inicial.installments_count) if inicial else 1,
            step=1,
            key=f"{prefixo}_nparc",
            disabled=not parcelado,
        )
    with col_b:
        primeira = st.date_input(
            "Mês da primeira parcela",
            value=inicial.first_installment_month if inicial else mes_da_compra,
            format="DD/MM/YYYY",
            key=f"{prefixo}_primeira",
            disabled=not parcelado,
            help="Por padrão, o mês da compra. Só o mês é considerado.",
        )

    observacao = st.text_input(
        "Observação (opcional)",
        value=(inicial.note or "") if inicial else "",
        key=f"{prefixo}_obs",
    )

    if categoria is None:
        return None, "Nenhuma categoria ativa nesta data."
    if not descricao.strip():
        return None, "Informe uma descrição."
    if valor_total <= 0:
        return None, "Informe um valor maior que zero."

    quantidade = int(num_parcelas) if parcelado else 1
    return (
        DadosGasto(
            purchase_date=data_compra,
            description=descricao,
            category_id=categoria.id,
            total_cents=to_cents(valor_total),
            payment_method=meio,
            installments_count=quantidade,
            first_installment_month=month_start(primeira if parcelado else data_compra),
            note=observacao.strip() or None,
        ),
        "",
    )


def quanto_falta(dados: DadosGasto) -> int:
    """Quanto este gasto passa do disponível da categoria, no primeiro mês.

    Só a primeira parcela pesa agora; as seguintes cairão nos meses delas e
    serão avaliadas quando aqueles meses chegarem.
    """
    from core import budget_service as budget

    parcelas = split_installments(dados.total_cents, dados.installments_count)
    nesta_vez = parcelas[0] if parcelas else dados.total_cents
    mes = month_start(dados.first_installment_month)

    with session_scope() as session:
        vistas = {v.id: v for v in cat.resolve_all(session, mes)}
        vista = vistas.get(dados.category_id)
        if vista is None or not vista.accumulates:
            return 0
        disponivel = budget.saldo_categoria(session, vista, mes)
    return max(0, nesta_vez - disponivel)


def salvar_gasto(
    dados: DadosGasto,
    expense_id: int | None = None,
    *,
    cobrir_com: int | None = None,
    cobrir_cents: int = 0,
) -> list[tuple[date, int]]:
    """Grava o gasto e devolve as parcelas geradas.

    Ponto único de escrita de gastos: a Home e a página Gastos passam por
    aqui, então a regra de parcelamento existe uma vez só.
    """
    with session_scope() as session:
        if expense_id is None:
            gasto = create_expense(
                session,
                purchase_date=dados.purchase_date,
                description=dados.description,
                category_id=dados.category_id,
                total_cents=dados.total_cents,
                payment_method=dados.payment_method,
                installments_count=dados.installments_count,
                first_installment_month=dados.first_installment_month,
                note=dados.note,
            )
        else:
            gasto = update_expense(
                session,
                expense_id,
                purchase_date=dados.purchase_date,
                description=dados.description,
                category_id=dados.category_id,
                total_cents=dados.total_cents,
                payment_method=dados.payment_method,
                installments_count=dados.installments_count,
                first_installment_month=dados.first_installment_month,
                note=dados.note,
            )
        if cobrir_com is not None and cobrir_cents > 0:
            # A cobertura nasce junto com o gasto e aponta para ele: apagar
            # o gasto desfaz a transferência, sem deixar dinheiro solto.
            from core import budget_service as budget

            budget.transferir(
                session,
                month=dados.first_installment_month,
                origem_id=cobrir_com,
                destino_id=dados.category_id,
                valor_cents=cobrir_cents,
                note=f"Cobertura de {dados.description}",
                expense_id=gasto.id,
            )
        return [(p.month, p.amount_cents) for p in gasto.installments]


def formulario_gasto(
    prefixo: str,
    *,
    mes_referencia: date,
    inicial: DadosGasto | None = None,
    expense_id: int | None = None,
    rotulo_botao: str = "Adicionar gasto",
    compacto: bool = False,
) -> bool:
    """Formulário completo de gasto. Devolve ``True`` quando gravou.

    Usado tanto pelo atalho da Home (``compacto=True``) quanto pela página
    Gastos — mesmos campos, mesma validação, mesmo service.
    """
    pendente = f"gasto_pendente_{prefixo}"
    if pendente in st.session_state:
        return _confirmar_cobertura(prefixo, pendente, mes_referencia)

    with st.form(f"form_gasto_{prefixo}", clear_on_submit=expense_id is None):
        dados, erro = _campos_gasto(
            prefixo, inicial=inicial, mes_referencia=mes_referencia, compacto=compacto
        )
        enviado = st.form_submit_button(rotulo_botao, use_container_width=True)

    if not enviado:
        return False
    if dados is None:
        st.error(erro)
        return False

    # Um gasto que passa do disponível precisa vir de algum lugar. A
    # pergunta não cabe dentro do formulário (o Streamlit só reexecuta
    # depois do envio), então guardamos a intenção e perguntamos no passo
    # seguinte.
    falta = quanto_falta(dados) if expense_id is None else 0
    if falta > 0:
        st.session_state[pendente] = (dados, falta)
        st.rerun()

    parcelas = salvar_gasto(dados, expense_id)
    if dados.installments_count > 1:
        resumo = " · ".join(
            f"{month_label(m).split('/')[0][:3]}/{str(m.year)[2:]}" for m, _ in parcelas[:6]
        )
        st.success(
            f"Gasto em {dados.installments_count}x registrado. Parcelas em: {resumo}"
            + (" …" if len(parcelas) > 6 else "")
        )
    else:
        st.success("Gasto registrado." if expense_id is None else "Gasto atualizado.")
    return True


def _confirmar_cobertura(prefixo: str, chave: str, mes_referencia: date) -> bool:
    """Segundo passo do gasto que estourou: de onde sai o dinheiro."""
    from ui.transferencias import escolher_origem

    dados, falta = st.session_state[chave]
    with session_scope() as session:
        vistas = {v.id: v for v in cat.resolve_all(session, dados.first_installment_month)}
    categoria = vistas[dados.category_id]

    st.markdown(f"**{dados.description}** · {month_label(dados.first_installment_month)}")
    origem = escolher_origem(
        categoria, falta, dados.first_installment_month, chave=f"{prefixo}_cobre"
    )

    confirmar, cancelar = st.columns(2)
    if confirmar.button("Confirmar gasto", key=f"{prefixo}_confirma", type="primary"):
        salvar_gasto(dados, cobrir_com=origem, cobrir_cents=falta if origem else 0)
        del st.session_state[chave]
        if origem is None:
            st.toast("Gasto registrado. O saldo negativo segue para o mês seguinte.")
        else:
            st.toast(f"Gasto registrado, coberto por {vistas[origem].name}.", icon="✅")
        st.rerun()
    if cancelar.button("Cancelar", key=f"{prefixo}_cancela"):
        del st.session_state[chave]
        st.rerun()
    return False
