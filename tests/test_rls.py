"""O isolamento garantido pelo próprio PostgreSQL.

Os testes de ``test_isolamento.py`` provam que a **aplicação** filtra certo.
Estes provam que, mesmo que ela não filtrasse, o **banco** recusaria — que
é a diferença entre um filtro esquecido ser um bug e ser um vazamento.

Rodam só quando ``TEST_DATABASE_URL`` aponta para um PostgreSQL, porque
RLS não existe no SQLite. Trabalham num **schema descartável**, criado e
derrubado pelo próprio teste, com ids inventados: não encostam em tabela
real nem em conta real.

Para rodar::

    TEST_DATABASE_URL="postgresql+psycopg://..." pytest tests/test_rls.py
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not URL.startswith("postgresql"),
    reason="RLS exige PostgreSQL; defina TEST_DATABASE_URL",
)

#: Schema próprio deste teste. O nome deixa claro que é descartável.
SCHEMA = "teste_rls"

#: Dois donos inventados. Não correspondem a nenhuma conta real.
DONO_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DONO_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@pytest.fixture()
def banco():
    """Schema temporário com uma tabela protegida igual às de produção."""
    engine = create_engine(URL, connect_args={"prepare_threshold": None})
    with engine.begin() as conexao:
        conexao.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conexao.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        conexao.execute(
            text(
                f"""
                CREATE TABLE {SCHEMA}.cofre (
                    id serial PRIMARY KEY,
                    user_id uuid NOT NULL,
                    descricao text NOT NULL,
                    valor_cents integer NOT NULL
                )
                """
            )
        )
        conexao.execute(text(f"ALTER TABLE {SCHEMA}.cofre ENABLE ROW LEVEL SECURITY"))
        # Sem FORCE, o dono da tabela continua enxergando tudo — e o dono é
        # justamente quem roda as migrações.
        conexao.execute(text(f"ALTER TABLE {SCHEMA}.cofre FORCE ROW LEVEL SECURITY"))
        conexao.execute(
            text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO authenticated")
        )
        conexao.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.cofre "
                "TO authenticated"
            )
        )
        conexao.execute(
            text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO authenticated")
        )
        for acao, clausula in (
            ("SELECT", "USING (user_id = auth.uid())"),
            ("INSERT", "WITH CHECK (user_id = auth.uid())"),
            ("UPDATE", "USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())"),
            ("DELETE", "USING (user_id = auth.uid())"),
        ):
            conexao.execute(
                text(
                    f"CREATE POLICY cofre_{acao.lower()} ON {SCHEMA}.cofre "
                    f"FOR {acao} TO authenticated {clausula}"
                )
            )
    try:
        yield engine
    finally:
        with engine.begin() as conexao:
            conexao.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


def _como(conexao, quem: uuid.UUID | None) -> None:
    """Declara ao banco quem está falando, como o aplicativo faz."""
    conexao.execute(text("SET LOCAL ROLE authenticated"))
    claims = (
        f'{{"sub":"{quem}","role":"authenticated"}}' if quem else ""
    )
    conexao.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": claims}
    )


def _guardar(engine, quem: uuid.UUID, descricao: str, valor: int) -> None:
    with engine.begin() as conexao:
        _como(conexao, quem)
        conexao.execute(
            text(
                f"INSERT INTO {SCHEMA}.cofre (user_id, descricao, valor_cents) "
                "VALUES (:u, :d, :v)"
            ),
            {"u": quem, "d": descricao, "v": valor},
        )


def test_cada_usuario_le_somente_o_proprio_dinheiro(banco) -> None:
    """A linha do outro não aparece nem na listagem nem na soma."""
    _guardar(banco, DONO_A, "salário do A", 300000)
    _guardar(banco, DONO_B, "salário do B", 500000)

    with banco.begin() as conexao:
        _como(conexao, DONO_A)
        vistos = [r[0] for r in conexao.execute(text(f"SELECT descricao FROM {SCHEMA}.cofre"))]
        soma = conexao.execute(
            text(f"SELECT coalesce(sum(valor_cents), 0) FROM {SCHEMA}.cofre")
        ).scalar_one()

    assert vistos == ["salário do A"]
    assert soma == 300000, "a soma não pode incluir o dinheiro do outro"


def test_update_no_dado_alheio_nao_afeta_nenhuma_linha(banco) -> None:
    """Saber o id do outro não dá poder sobre ele."""
    _guardar(banco, DONO_B, "reserva do B", 100000)

    with banco.begin() as conexao:
        _como(conexao, DONO_A)
        resultado = conexao.execute(
            text(f"UPDATE {SCHEMA}.cofre SET valor_cents = 1 WHERE valor_cents = 100000")
        )
        assert resultado.rowcount == 0

    with banco.begin() as conexao:
        _como(conexao, DONO_B)
        valor = conexao.execute(
            text(f"SELECT valor_cents FROM {SCHEMA}.cofre")
        ).scalar_one()
    assert valor == 100000, "o dinheiro do dono continua intacto"


def test_delete_no_dado_alheio_nao_apaga_nada(banco) -> None:
    """Nem apagar."""
    _guardar(banco, DONO_B, "reserva do B", 100000)

    with banco.begin() as conexao:
        _como(conexao, DONO_A)
        resultado = conexao.execute(text(f"DELETE FROM {SCHEMA}.cofre"))
        assert resultado.rowcount == 0

    with banco.begin() as conexao:
        _como(conexao, DONO_B)
        quantas = conexao.execute(
            text(f"SELECT count(*) FROM {SCHEMA}.cofre")
        ).scalar_one()
    assert quantas == 1


def test_nao_da_para_gravar_em_nome_de_outro(banco) -> None:
    """Forjar o ``user_id`` na inserção é recusado pela política."""
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with banco.begin() as conexao:
            _como(conexao, DONO_A)
            conexao.execute(
                text(
                    f"INSERT INTO {SCHEMA}.cofre (user_id, descricao, valor_cents) "
                    "VALUES (:u, 'forjada', 1)"
                ),
                {"u": DONO_B},
            )


def test_update_nao_pode_transferir_a_linha_para_outro_dono(banco) -> None:
    """``WITH CHECK`` impede doar a própria linha para outra pessoa."""
    from sqlalchemy.exc import DBAPIError

    _guardar(banco, DONO_A, "minha", 1000)

    with pytest.raises(DBAPIError):
        with banco.begin() as conexao:
            _como(conexao, DONO_A)
            conexao.execute(
                text(f"UPDATE {SCHEMA}.cofre SET user_id = :u"), {"u": DONO_B}
            )


def test_consulta_sem_usuario_nao_devolve_nada(banco) -> None:
    """Falha fechada: esquecer de dizer quem é não abre as portas.

    É a diferença entre um esquecimento virar tela vazia e virar vazamento.
    """
    _guardar(banco, DONO_A, "salário do A", 300000)
    _guardar(banco, DONO_B, "salário do B", 500000)

    with banco.begin() as conexao:
        _como(conexao, None)
        vistos = conexao.execute(
            text(f"SELECT count(*) FROM {SCHEMA}.cofre")
        ).scalar_one()

    assert vistos == 0


def test_apagar_categoria_nao_zera_o_dono_do_perfil() -> None:
    """A chave composta do perfil não pode derrubar o ``user_id``.

    ``ON DELETE SET NULL`` numa chave composta zera **todas** as colunas
    dela — inclusive ``user_id``, que é ``NOT NULL``. Isso fazia apagar
    qualquer categoria derrubar a operação inteira, e só apareceu ao zerar
    os dados. A correção diz ao PostgreSQL qual coluna zerar.
    """
    import uuid as _uuid

    from sqlalchemy import text as _text

    engine = create_engine(URL, connect_args={"prepare_threshold": None})
    dono = _uuid.uuid4()
    try:
        with engine.begin() as conexao:
            conexao.execute(
                _text(
                    "INSERT INTO categories (user_id, slug, created_at) "
                    "VALUES (:u, :s, now())"
                ),
                {"u": dono, "s": f"teste_{dono.hex[:8]}"},
            )
            categoria = conexao.execute(
                _text("SELECT id FROM categories WHERE user_id = :u"), {"u": dono}
            ).scalar_one()
            conexao.execute(
                _text(
                    "INSERT INTO profiles "
                    "(user_id, onboarding_completed, investment_category_id, "
                    " created_at, updated_at) "
                    "VALUES (:u, false, :c, now(), now())"
                ),
                {"u": dono, "c": categoria},
            )

        # O passo que quebrava.
        with engine.begin() as conexao:
            conexao.execute(
                _text("DELETE FROM categories WHERE user_id = :u"), {"u": dono}
            )

        with engine.begin() as conexao:
            linha = conexao.execute(
                _text(
                    "SELECT user_id, investment_category_id FROM profiles "
                    "WHERE user_id = :u"
                ),
                {"u": dono},
            ).one()
        assert linha[0] is not None, "o dono do perfil tem de sobreviver"
        assert linha[1] is None, "a referência à categoria apagada some"
    finally:
        with engine.begin() as conexao:
            conexao.execute(_text("DELETE FROM profiles WHERE user_id = :u"), {"u": dono})
            conexao.execute(
                _text("DELETE FROM categories WHERE user_id = :u"), {"u": dono}
            )
        engine.dispose()
