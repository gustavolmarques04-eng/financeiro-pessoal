"""Um usuário nunca enxerga, soma ou altera o dinheiro do outro.

Estes testes rodam no SQLite e provam o isolamento da **aplicação**. A
prova do isolamento do **banco** (RLS) é feita em ``test_rls.py``, contra
PostgreSQL de verdade — as duas camadas existem porque nenhuma sozinha
seria suficiente.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from core import auth
from core import budget_service as budget
from core import categories as cat
from core import escopo
from core import repositories as repo
from core.database import seed_defaults
from core.models import Base, Category, Expense, Income, IncomeType, PaymentMethod
from core.period import Period
from core.utils import to_cents

from .conftest import SETEMBRO, mes


@pytest.fixture()
def dois_usuarios(tmp_path: Path):
    """Um banco com dois donos, cada um com suas categorias semeadas."""
    caminho = tmp_path / "dois.db"
    engine = create_engine(f"sqlite:///{caminho.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)

    a = auth.Usuario(id=uuid.uuid4(), email="a@financeiro.local", login="a")
    b = auth.Usuario(id=uuid.uuid4(), email="b@financeiro.local", login="b")
    for usuario in (a, b):
        auth.definir_atual(usuario)
        with fabrica() as preparo:
            seed_defaults(preparo)
            preparo.commit()

    try:
        yield a, b, fabrica
    finally:
        auth.definir_atual(None)
        engine.dispose()


def _como(usuario: auth.Usuario, fabrica) -> Session:
    """Abre uma sessão já com o dono declarado."""
    auth.definir_atual(usuario)
    return fabrica()


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------
def test_um_usuario_nao_le_receitas_do_outro(dois_usuarios) -> None:
    """A receita de um não aparece — nem na lista, nem na soma."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        repo.create_income(
            sessao,
            on=SETEMBRO,
            description="Salário do A",
            type_=IncomeType.SALARIO,
            amount_cents=to_cents(3000),
        )
        sessao.commit()

    with _como(b, fabrica) as sessao:
        assert sessao.scalars(select(Income)).all() == []
        assert repo.sum_incomes(sessao, Period.of_month(SETEMBRO)) == 0
        assert sessao.scalar(select(func.count()).select_from(Income)) == 0


def test_um_usuario_nao_le_categorias_do_outro(dois_usuarios) -> None:
    """Cada um vê só as suas, mesmo com slugs iguais."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        cat.criar_categoria(
            sessao,
            name="Faculdade",
            behavior=cat.CategoryBehavior.ACCUMULATING_ENVELOPE,
            percent_bp=0,
            effective_month=SETEMBRO,
        )
        sessao.commit()

    with _como(b, fabrica) as sessao:
        slugs = {v.slug for v in cat.resolve_all(sessao, SETEMBRO)}
        assert "faculdade" not in slugs


def test_patrimonio_de_um_nunca_usa_saldo_do_outro(dois_usuarios) -> None:
    """O patrimônio é somado só sobre as categorias do próprio dono."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        repo.create_income(
            sessao,
            on=SETEMBRO,
            description="Salário",
            type_=IncomeType.SALARIO,
            amount_cents=to_cents(5000),
        )
        for vista in cat.resolve_active(sessao, SETEMBRO):
            if vista.requires_separation:
                budget.confirmar_separacao(sessao, SETEMBRO, vista.id)
        sessao.commit()
        patrimonio_de_a = budget.get_patrimonio(sessao, mes(SETEMBRO)).total_cents

    assert patrimonio_de_a > to_cents(1000), "o A separou de verdade"

    with _como(b, fabrica) as sessao:
        # O B tem apenas o que o próprio cadastro inicial lhe deu; nada do
        # que o A separou pode aparecer aqui.
        patrimonio_de_b = budget.get_patrimonio(sessao, mes(SETEMBRO)).total_cents
        assert patrimonio_de_b < to_cents(100)
        assert patrimonio_de_b != patrimonio_de_a


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------
def test_um_usuario_nao_apaga_receita_do_outro(dois_usuarios) -> None:
    """Saber o id alheio não basta: a linha nem é encontrada."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        receita = repo.create_income(
            sessao,
            on=SETEMBRO,
            description="Salário do A",
            type_=IncomeType.SALARIO,
            amount_cents=to_cents(3000),
        )
        sessao.commit()
        id_alheio = receita.id

    with _como(b, fabrica) as sessao:
        assert repo.get_income(sessao, id_alheio) is None
        repo.delete_income(sessao, id_alheio)
        sessao.commit()

    with _como(a, fabrica) as sessao:
        assert repo.get_income(sessao, id_alheio) is not None, (
            "a receita do dono continua intacta"
        )


def test_gasto_nao_pode_apontar_para_categoria_do_outro(dois_usuarios) -> None:
    """A chave composta (category_id, user_id) é recusada pelo banco."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        categoria_de_a = cat.resolve_active(sessao, SETEMBRO)[0].id

    with _como(b, fabrica) as sessao:
        sessao.add(
            Expense(
                user_id=b.id,
                purchase_date=SETEMBRO,
                description="Tentativa",
                category_id=categoria_de_a,
                total_cents=to_cents(10),
                payment_method=PaymentMethod.PIX_DEBITO,
                installments_count=1,
                first_installment_month=SETEMBRO,
            )
        )
        with pytest.raises(IntegrityError):
            sessao.flush()


def test_transferencia_nao_pode_ter_destino_do_outro(dois_usuarios) -> None:
    """Dinheiro não atravessa de um usuário para o outro."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        categoria_de_a = cat.resolve_active(sessao, SETEMBRO)[0].id

    with _como(b, fabrica) as sessao:
        origem = cat.resolve_active(sessao, SETEMBRO)[0].id
        with pytest.raises((IntegrityError, budget.TransferenciaInvalida)):
            repo.criar_transferencia(
                sessao,
                month=SETEMBRO,
                from_category_id=origem,
                to_category_id=categoria_de_a,
                amount_cents=to_cents(10),
            )
            sessao.flush()


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def test_cache_nunca_devolve_dado_do_outro(dois_usuarios) -> None:
    """O cache vive dentro da sessão, e a sessão pertence a um dono só."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        repo.create_income(
            sessao,
            on=SETEMBRO,
            description="Salário do A",
            type_=IncomeType.SALARIO,
            amount_cents=to_cents(4000),
        )
        sessao.commit()
        # Aquece tudo que o cache guarda.
        budget.get_month_plan(sessao, SETEMBRO)
        budget.get_patrimonio(sessao, mes(SETEMBRO))
        plano_de_a = budget.get_month_plan(sessao, SETEMBRO)
        assert plano_de_a.base_cents == to_cents(4000)

    with _como(b, fabrica) as sessao:
        plano_de_b = budget.get_month_plan(sessao, SETEMBRO)
        assert plano_de_b.base_cents == 0, "sessão nova, cache novo, dono novo"
        # O B não separou nada, então o único saldo é o do próprio cadastro
        # inicial — em nenhuma hipótese os R$ 4.000 que o A recebeu.
        guardado_de_b = budget.get_patrimonio(sessao, mes(SETEMBRO)).guardado_cents
        assert guardado_de_b < to_cents(100)


def test_sem_usuario_autenticado_nada_e_gravado(dois_usuarios) -> None:
    """Sem dono, gravar é erro — nunca uma linha órfã que todos enxergam."""
    _a, _b, fabrica = dois_usuarios
    auth.definir_atual(None)

    with fabrica() as sessao:
        with pytest.raises((auth.AuthError, IntegrityError)):
            repo.create_income(
                sessao,
                on=SETEMBRO,
                description="Sem dono",
                type_=IncomeType.SALARIO,
                amount_cents=to_cents(100),
            )
            sessao.flush()


def test_busca_escopada_ignora_o_cache_de_identidade(dois_usuarios) -> None:
    """``escopo.buscar`` não devolve linha alheia já carregada na memória."""
    a, b, fabrica = dois_usuarios

    with _como(a, fabrica) as sessao:
        categoria = sessao.scalars(select(Category)).first()
        assert categoria is not None
        id_alheio = categoria.id
        sessao.commit()

    with _como(b, fabrica) as sessao:
        # O id existe no banco, mas não para este dono.
        alheia = escopo.buscar(sessao, Category, id=id_alheio)
        minhas = {c.id for c in sessao.scalars(select(Category))}
        assert alheia is None or alheia.id in minhas
