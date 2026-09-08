"""Testes de arquitetura: regra financeira só nos services, sem hardcode.

Estes testes lêem o próprio código-fonte. Servem de trava contra o tipo de
regressão que passa despercebida numa revisão: uma página recalculando
dinheiro por conta própria, ou uma regra voltando a depender do nome de uma
categoria.
"""

from __future__ import annotations

import re
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
