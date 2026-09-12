"""Testes de arquitetura: regra financeira só nos services, sem hardcode.

Estes testes lêem o próprio código-fonte. Servem de trava contra o tipo de
regressão que passa despercebida numa revisão: uma página recalculando
dinheiro por conta própria, ou uma regra voltando a depender do nome de uma
categoria.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PAGINAS = sorted((RAIZ / "pages").glob("*.py"))
NUCLEO = sorted((RAIZ / "core").glob("*.py"))

#: Nomes que existem nas sementes e em rótulos, mas não podem virar regra.
NOMES_DE_CATEGORIA = (
    "Namorada",
    "Amigos",
    "Livre",
    "Viagem",
    "Compras pessoais",
    "Independência financeira",
    "Reserva",
)


# --------------------------------------------------------------------------
# 20 — quick-add e página Gastos usam a mesma camada
# --------------------------------------------------------------------------
def test_quick_add_e_pagina_gastos_usam_o_mesmo_servico() -> None:
    """As duas telas chamam ``ui.forms``; nenhuma grava gasto por fora."""
    home = (RAIZ / "pages" / "dashboard.py").read_text(encoding="utf-8")
    gastos = (RAIZ / "pages" / "gastos.py").read_text(encoding="utf-8")

    assert "formulario_gasto" in home
    assert "formulario_gasto" in gastos

    for nome, codigo in (("dashboard.py", home), ("gastos.py", gastos)):
        assert "create_expense(" not in codigo, (
            f"{nome} grava gasto direto no repositório em vez de usar ui.forms"
        )
        assert "update_expense(" not in codigo, (
            f"{nome} atualiza gasto direto no repositório em vez de usar ui.forms"
        )


def test_escrita_de_gasto_existe_em_um_lugar_so() -> None:
    """Só ``ui.forms`` chama a criação/edição de gasto na camada de interface."""
    chamadores = [
        arquivo.name
        for arquivo in [*PAGINAS, *sorted((RAIZ / "ui").glob("*.py"))]
        if re.search(r"(?<![\w.])(create|update)_expense\s*\(", arquivo.read_text(encoding="utf-8"))
    ]
    assert chamadores == ["forms.py"], chamadores


# --------------------------------------------------------------------------
# 32 — auditoria de hardcode
# --------------------------------------------------------------------------
def test_regras_nao_comparam_nome_de_categoria() -> None:
    """Nenhuma comparação de igualdade com nome de categoria.

    Os nomes podem aparecer em sementes e migrações (dados iniciais), mas
    nunca controlando fluxo.
    """
    permitidos = {"models.py"}  # sementes
    infratores: list[str] = []

    for arquivo in [*PAGINAS, *NUCLEO, *sorted((RAIZ / "ui").glob("*.py"))]:
        if arquivo.name in permitidos:
            continue
        codigo = arquivo.read_text(encoding="utf-8")
        for nome in NOMES_DE_CATEGORIA:
            padroes = [
                rf'==\s*["\']{re.escape(nome)}["\']',
                rf'["\']{re.escape(nome)}["\']\s*==',
                rf'\.name\s*==\s*["\']',
                rf'category\s*==\s*["\']',
            ]
            for padrao in padroes:
                if re.search(padrao, codigo):
                    infratores.append(f"{arquivo.name}: {padrao}")
    assert infratores == [], infratores


def test_paginas_nao_tem_lista_fixa_de_categorias() -> None:
    """Nenhuma página monta a lista de categorias na mão."""
    infratores = []
    for arquivo in PAGINAS:
        codigo = arquivo.read_text(encoding="utf-8")
        # Ex.: ["Namorada", "Amigos", ...] dentro de uma página.
        if re.search(r'\[\s*["\'](Namorada|Amigos|Viagem|Livre)["\']', codigo):
            infratores.append(arquivo.name)
    assert infratores == [], infratores


def test_servicos_nao_dependem_de_slug_para_regras() -> None:
    """As regras usam comportamento e flags, não o identificador estável."""
    for nome in ("budget_service.py", "investment_service.py"):
        codigo = (RAIZ / "core" / nome).read_text(encoding="utf-8")
        assert not re.search(r'slug\s*==\s*["\']', codigo), nome


# --------------------------------------------------------------------------
# Regra financeira mora nos services
# --------------------------------------------------------------------------
def test_paginas_nao_fazem_aritmetica_de_dinheiro() -> None:
    """Nenhuma página soma ou subtrai centavos por conta própria."""
    infratores = []
    padrao = re.compile(r"_cents\s*[-+*/]\s|\s[-+*/]\s*[a-z_]+_cents")
    for arquivo in PAGINAS:
        for numero, linha in enumerate(
            arquivo.read_text(encoding="utf-8").splitlines(), start=1
        ):
            texto = linha.strip()
            if texto.startswith("#"):
                continue
            if padrao.search(texto):
                infratores.append(f"{arquivo.name}:{numero}: {texto}")
    assert infratores == [], infratores


def test_paginas_nao_escrevem_sql() -> None:
    """Consultas ficam em ``core.repositories``."""
    infratores = [
        arquivo.name
        for arquivo in PAGINAS
        if re.search(r"(?<![\w.])(select|func\.sum)\s*\(", arquivo.read_text(encoding="utf-8"))
    ]
    assert infratores == [], infratores


def test_paginas_nao_recalculam_o_plano() -> None:
    """Distribuição e regra de meta só existem no ``budget_service``."""
    infratores = [
        arquivo.name
        for arquivo in PAGINAS
        if re.search(r"(?<![\w.])(distribuir|aplicar_regras_de_meta)\s*\(",
                     arquivo.read_text(encoding="utf-8"))
    ]
    assert infratores == [], infratores


def test_resumo_de_separacoes_tem_uma_implementacao() -> None:
    """Home e Separações chamam ``resumo_separacoes``; ninguém recalcula."""
    home = (RAIZ / "pages" / "dashboard.py").read_text(encoding="utf-8")
    separacoes = (RAIZ / "pages" / "separacoes.py").read_text(encoding="utf-8")

    assert "resumo_separacoes" in home
    assert "resumo_separacoes" in separacoes
    for codigo in (home, separacoes):
        assert "falta_cents=" not in codigo, "a página está montando o resumo na mão"


# --------------------------------------------------------------------------
# Navegação por período: as setas precisam realmente mudar o mês
# --------------------------------------------------------------------------
def test_seletor_de_periodo_recria_o_widget_a_cada_periodo() -> None:
    """A chave do seletor carrega o período vigente.

    Com uma chave fixa o Streamlit preserva o valor antigo do ``selectbox``,
    ignora o ``index`` e desfaz a navegação feita pelas setas — o seletor
    voltava sozinho para o mês anterior.
    """
    codigo = (RAIZ / "ui" / "shared.py").read_text(encoding="utf-8")
    trecho = codigo[codigo.index("def seletor_periodo("):]

    assert 'key=f"{chave}_sel_{periodo.mode.value}_{periodo.anchor:%Y%m}"' in trecho
    assert 'key=f"{chave}_modo_{periodo.mode.value}"' in trecho


def test_troca_de_periodo_e_anunciada() -> None:
    """Abrir o app e trocar de período avisam o usuário."""
    codigo = (RAIZ / "ui" / "shared.py").read_text(encoding="utf-8")

    assert "_anunciar_periodo" in codigo
    assert "Mostrando {periodo.label}" in codigo
    assert "Período alterado para {periodo.label}" in codigo


# --------------------------------------------------------------------------
# Senha percent-encoded não pode quebrar o Alembic
# --------------------------------------------------------------------------
def test_url_com_percent_sobrevive_ao_configparser_do_alembic() -> None:
    """Uma senha com ``%40`` precisa atravessar a configuração do Alembic.

    O configparser lê ``%`` como interpolação: sem escapar, o app não sobe
    quando a senha do banco tem caractere codificado.
    """
    import sys

    sys.path.insert(0, str(RAIZ))
    from alembic.config import Config

    from core.database import url_para_alembic

    url = "postgresql+psycopg://user:%40senha%23@host.exemplo.com:5432/postgres"
    config = Config()
    config.set_main_option("sqlalchemy.url", url_para_alembic(url))

    assert config.get_main_option("sqlalchemy.url") == url


def test_todos_os_pontos_que_configuram_o_alembic_escapam_a_url() -> None:
    """Nenhum lugar passa a URL crua para ``set_main_option``."""
    arquivos = [
        RAIZ / "core" / "database.py",
        RAIZ / "migrations" / "env.py",
        RAIZ / "migrar_para_nuvem.py",
    ]
    infratores = []
    for arquivo in arquivos:
        for numero, linha in enumerate(
            arquivo.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if 'set_main_option("sqlalchemy.url"' in linha:
                if "url_para_alembic" not in linha:
                    infratores.append(f"{arquivo.name}:{numero}")
    assert infratores == [], infratores


# --------------------------------------------------------------------------
# Desempenho: o Streamlit reexecuta a página inteira a cada clique
# --------------------------------------------------------------------------
def test_render_do_dashboard_nao_estoura_o_orcamento_de_consultas() -> None:
    """Um render completo precisa caber em poucas idas ao banco.

    Com o banco na nuvem cada consulta custa latência de rede. Este teste
    trava a regressão que fez o app demorar segundos por clique: chamadas
    1+N e recálculos repetidos do mesmo plano.
    """
    import sys
    from datetime import date

    sys.path.insert(0, str(RAIZ))
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session, sessionmaker

    from core import auth
    from core import budget_service as budget
    from core import investment_service as inv
    from core import repositories as repo
    from core.database import seed_defaults
    from core.models import Base
    from core.period import Period

    auth.definir_atual(
        auth.Usuario(id=uuid.uuid4(), email="arq@financeiro.local", login="arq")
    )
    engine = create_engine("sqlite://")  # em memória
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine)
    with fabrica() as preparo:
        seed_defaults(preparo)
        preparo.commit()

    consultas: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _contar(conn, cursor, stmt, params, ctx, many):  # type: ignore[no-untyped-def]
        consultas.append(stmt)

    periodo = Period.of_month(date(2026, 9, 1))
    with fabrica() as session:
        budget.get_resumo_periodo(session, periodo)
        inv.get_resumo(session, periodo)
        referencia = budget.mes_de_referencia(session, periodo)
        budget.get_month_plan(session, referencia)
        budget.resumo_separacoes(session, referencia)
        budget.envelopes(session, periodo)
        budget.metas(session, periodo)
        repo.known_months(session)

    engine.dispose()
    assert len(consultas) <= 25, (
        f"um render do dashboard fez {len(consultas)} consultas; "
        "algo voltou a consultar por categoria ou recalcular o plano"
    )


def test_custo_por_clique_nao_cresce_com_o_historico() -> None:
    """Dez anos de uso têm de custar o mesmo que o primeiro mês.

    O saldo de hoje depende de todos os meses anteriores. Se cada mês
    custasse uma ida ao banco, o app ficaria mais lento a cada mês usado —
    exatamente o problema que o histórico em memória existe para evitar.
    """
    import sys
    from datetime import date

    sys.path.insert(0, str(RAIZ))
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from core import auth
    from core import budget_service as budget
    from core import repositories as repo
    from core.database import seed_defaults
    from core.models import Base, IncomeType
    from core.period import Period
    from core.utils import add_months

    auth.definir_atual(
        auth.Usuario(id=uuid.uuid4(), email="arq@financeiro.local", login="arq")
    )

    def medir(meses: int) -> int:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        fabrica = sessionmaker(bind=engine)
        inicio = date(2026, 9, 1)
        with fabrica() as preparo:
            seed_defaults(preparo)
            for i in range(meses):
                repo.create_income(
                    preparo,
                    on=add_months(inicio, -i),
                    amount_cents=295775,
                    type_=IncomeType.SALARIO,
                    description="salário",
                )
            preparo.commit()

        consultas: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def _contar(conn, cursor, stmt, params, ctx, many):  # type: ignore[no-untyped-def]
            consultas.append(stmt)

        with fabrica() as session:
            budget.get_resumo_periodo(session, Period.of_month(inicio))
            budget.get_month_plan(session, inicio)

        engine.dispose()
        return len(consultas)

    um_mes = medir(1)
    dez_anos = medir(120)

    assert dez_anos == um_mes, (
        f"um mês de histórico custa {um_mes} consultas e dez anos custam "
        f"{dez_anos}: o cálculo voltou a perguntar ao banco mês a mês"
    )


def test_toda_pagina_exige_login_antes_de_qualquer_coisa() -> None:
    """``require_auth()`` tem de ser a primeira instrução executada.

    Não basta chamar em algum lugar: se qualquer código rodar antes, os
    valores podem chegar a ser lidos e desenhados, e o "sem login não
    aparece nada" vira "aparece por um instante".
    """
    import ast

    paginas = sorted((RAIZ / "pages").glob("*.py"))
    assert paginas, "nenhuma página encontrada"

    for caminho in paginas:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        executaveis = [
            no
            for no in arvore.body
            if not isinstance(no, (ast.Import, ast.ImportFrom))
            and not (isinstance(no, ast.Expr) and isinstance(no.value, ast.Constant))
        ]
        assert executaveis, f"{caminho.name} não executa nada"

        primeira = executaveis[0]
        chamou = (
            isinstance(primeira, ast.Assign)
            and isinstance(primeira.value, ast.Call)
            and getattr(primeira.value.func, "id", "") == "require_auth"
        )
        assert chamou, (
            f"{caminho.name}: a primeira instrução é "
            f"{ast.dump(primeira)[:60]}, e não require_auth()"
        )
