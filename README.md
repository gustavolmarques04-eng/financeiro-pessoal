# Financeiro pessoal

Aplicativo web privado para orçamento pessoal com renda variável. Roda no seu
computador, abre no navegador e funciona bem no iPhone.

Python + Streamlit + SQLAlchemy + SQLite + Plotly. Todo valor monetário é
guardado em **centavos inteiros** — nunca em `float`.

---

## Instalação

```bash
cd financeiro_app
python -m venv .venv
```

Ative a virtualenv:

```bash
.venv\Scripts\activate
```

No macOS/Linux: `source .venv/bin/activate`

Instale as dependências:

```bash
pip install -r requirements-dev.txt
```

`requirements.txt` tem só o que o aplicativo publicado precisa;
`requirements-dev.txt` acrescenta o `pytest`.

## Executar

```bash
streamlit run app.py
```

O navegador abre em `http://localhost:8501`. O banco é criado sozinho na
primeira execução, em `data/financeiro.db`.

## Acessar do iPhone (mesma rede Wi-Fi)

```bash
streamlit run app.py --server.address 0.0.0.0
```

Descubra o IP do computador (`ipconfig` no Windows, `ifconfig` no macOS) e
abra no Safari: `http://192.168.x.x:8501`. O computador precisa estar ligado e
na mesma rede. Se não conectar, libere a porta 8501 no firewall do Windows.

A interface já é responsiva: no celular os cartões empilham em uma coluna, os
botões são grandes e não é preciso dar zoom horizontal.

## Importar a planilha antiga

Uma vez só, para trazer o histórico de `Plano_Financeiro_Pessoal_v2.xlsx`:

```bash
python import_excel.py
```

O script mostra tudo que encontrou e pergunta antes de gravar. Rodar de novo
não duplica nada — ele avisa que já importou. Para importar de propósito
outra vez, use `--refazer`.

Os R$ 5,54 do "Saldo anterior Nubank" **não entram na base de distribuição**:
viram saldo inicial do envelope Compras pessoais. O estado importado fica
recebido R$ 2.475,94, base R$ 2.952,21 e envelope Compras com R$ 5,54.

> Se você já registrou esses valores à mão no aplicativo, **não importe** —
> as duas fontes se somariam.

---

## Período: mês ou ano

No topo de toda tela há o seletor de período. **Mensal** mostra um mês;
**Anual** agrega o ano: receitas, gastos e dividendos somam os doze meses.

Abaixo do seletor uma linha diz onde você está (*"setembro/2026 — mês
atual"*, *"agosto/2026 — mês passado"*) e, quando você sai do mês corrente,
aparece o botão **Voltar para hoje**. Cada troca também mostra um aviso
rápido no canto.

Patrimônio é a exceção e **não soma**: no modo anual ele mostra a posição
mais recente informada naquele ano, com o mês da foto ao lado
(*"posição de setembro/2026"*). Somar patrimônios mensais contaria o mesmo
dinheiro várias vezes.

## Modo privacidade

O botão 👁/🙈 no cabeçalho vale para todas as telas. Com ele ligado, nenhum
valor é **renderizado** — a máscara substitui o número antes de virar HTML,
e os gráficos monetários dão lugar a "Valores ocultos". Não é CSS por cima
do texto: inspecionar a página não revela nada. Percentuais continuam
visíveis, porque não expõem saldo.

O estado fica na sessão do navegador e não é gravado no banco.

## Categorias configuráveis

As categorias não são fixas no código. Cada uma tem identidade estável e
propriedades **versionadas por mês**, e é o *comportamento* — não o nome —
que decide as regras:

| Comportamento | O que faz |
|---|---|
| Aporte de longo prazo | Exige separação; alimenta o capital investido |
| Meta com valor-alvo | Exige separação; a sobra vai para outra categoria |
| Envelope acumulativo | Exige separação; o saldo passa de mês para mês |
| Orçamento mensal | Gastos reduzem o disponível; não acumula |
| Somente acompanhamento | Recebe gastos, mas não participa do rateio |

Em **⚙️ Configurações** dá para criar, renomear, trocar emoji, reordenar,
mudar percentual e desativar. Toda edição pergunta *a partir de qual mês*
vale — meses anteriores continuam exatamente como estavam. Categorias com
histórico não podem trocar de comportamento (isso mudaria o significado do
que já foi registrado); o caminho é desativar e criar outra.

O total dos percentuais precisa fechar **exatamente 100%** (10.000
pontos-base). Fora disso, o botão de salvar fica desabilitado e a tela diz
quanto falta ou sobra.

### Metas

Qualquer categoria pode ter uma meta, e todas aparecem no bloco **Metas** da
tela inicial com barra de progresso. Há duas naturezas:

- **Meta com valor-alvo** (comportamento `ALLOCATION_GOAL`, como a Reserva):
  ao chegar perto do alvo, o valor planejado é cortado e a sobra vai para a
  categoria de destino.
- **Meta de acompanhamento** (qualquer outro comportamento, como
  Independência financeira): mostra o progresso e **não** mexe no rateio —
  continuar aportando depois de bater a meta é o esperado.

## Como o aplicativo pensa

**Recebido no mês** é a renda que entrou (exclui lançamentos do tipo *Saldo
inicial*). **Base de distribuição** é o que os percentuais rateiam — pode
incluir um saldo anterior que você queira distribuir. Os dois números aparecem
lado a lado justamente porque não são a mesma coisa.

**Separação** guarda o *valor* já reservado, não um sim/não. Se você separar
R$ 1.470 para Independência e depois receber mais dinheiro, o plano sobe, a
categoria volta para "pendente" e o app continua sabendo que R$ 1.470 já
foram separados — mostrando só quanto falta.

Uma **categoria-meta** recebe no máximo o que falta para o alvo. O que
sobrar do percentual vai para a categoria de destino declarada nela — a
regra não conhece "Reserva" nem "Independência" pelo nome.

**Envelopes** acumulam entre meses (separações confirmadas menos gastos da
categoria). **Orçamentos mensais** recomeçam do zero.

**Reserva e Investimentos** funcionam com *checkpoint*: o valor informado no
fechamento é a última posição conferida, e tudo que você separa **depois**
dele é somado por cima. Assim "já separei R$ 502,82 para a Reserva" aparece
somado ao que você já tinha, sem precisar voltar ao fechamento. Quando você
informa o saldo de novo, o número digitado vira a nova verdade e o acúmulo
recomeça — nunca conta duas vezes.

Para a regra da meta o app usa o saldo *antes* das separações do mês em
curso, para o valor planejado não mudar enquanto você confirma.

**Patrimônio total** soma automaticamente todas as categorias marcadas com
*"saldo entra no patrimônio"*. Criar um envelope novo já o inclui, sem
mexer em código.

**Capital investido** vem das categorias marcadas com *"separações formam
capital investido"* — também sem depender de nome.

Todas as regras vivem em `core/budget_service.py`. As telas só apresentam,
e há testes de arquitetura que falham se alguma página voltar a calcular
dinheiro por conta própria.

---

## Backup

Em **⚙️ Configurações → Fazer backup dos meus dados**:

- **Baixar banco (.db)** — cópia fiel, é o que restaura tudo.
- **Baixar tudo (.json)** — legível, para conferir ou migrar.

Para restaurar, envie o `.db` no mesmo painel. Antes de substituir, o app
guarda sozinho uma cópia do banco atual em `data/backups/`.

Backup manual: copie o arquivo `data/financeiro.db`.

---

## Publicar no Streamlit Community Cloud

O aplicativo já está pronto para a nuvem: os segredos vêm de
`st.secrets` ou do ambiente, e o banco é escolhido por `DATABASE_URL`.

### 1. Criar o banco PostgreSQL

Crie um projeto no [Supabase](https://supabase.com) ou no
[Neon](https://neon.tech) (ambos têm plano gratuito) e copie a *connection
string*. Adapte o começo dela para o driver usado aqui:

```
postgresql+psycopg://usuario:senha@host:5432/postgres
```

No Supabase, use a porta do **Connection pooler** (6543) se a conexão
direta falhar.

### 2. Levar os dados para lá

```bash
python migrar_para_nuvem.py --destino "postgresql+psycopg://..."
```

O script mostra quantas linhas existem de cada lado, cria o esquema pelas
migrações, copia tudo numa única transação e confere as contagens no fim.
O banco local **não é alterado** — ele continua servindo de backup.

### 3. Subir o código para o GitHub

O Streamlit Community Cloud exige repositório público. Nada sensível vai
junto: `.gitignore` bloqueia o banco, os backups, o `.env` e o
`secrets.toml`.

```bash
git remote add origin https://github.com/SEU_USUARIO/financeiro-pessoal.git
git branch -M main
git push -u origin main
```

### 4. Publicar

Em [share.streamlit.io](https://share.streamlit.io): **New app**, escolha o
repositório, branch `main` e arquivo `app.py`. Em **Advanced settings**,
escolha o Python 3.12 e cole os segredos:

```toml
DATABASE_URL = "postgresql+psycopg://usuario:senha@host:5432/postgres"
APP_PASSWORD = "uma-senha-forte"
```

Sem `APP_PASSWORD` o aplicativo abre para qualquer pessoa com o link —
defina sempre.

### 5. Usar no iPhone

Abra o endereço no Safari, toque em Compartilhar → **Adicionar à Tela de
Início**. Fica com ícone próprio e abre em tela cheia, como um app.

### Voltar a rodar local

Basta não definir `DATABASE_URL`: o app volta a usar `data/financeiro.db`.
Para rodar local já apontando para a nuvem, ponha a URL no `.env`.

## Estrutura

```
financeiro_app/
├── app.py                    # entrada: autenticação + navegação
├── pages/                    # uma tela por arquivo (só apresentação)
│   ├── dashboard.py          # análise rápida + atalho de gasto
│   ├── separacoes.py         # confirmação mensal / resumo anual
│   ├── receitas.py
│   ├── gastos.py
│   ├── fechamento.py
│   └── configuracoes.py      # categorias, percentuais, backup
├── core/
│   ├── database.py           # conexão, migrações, transações
│   ├── models.py             # tabelas e sementes
│   ├── categories.py         # resolução por mês, edição versionada
│   ├── period.py             # Period: mês ou ano
│   ├── repositories.py       # consultas e escritas (sem regra de negócio)
│   ├── budget_service.py     # ← todas as regras financeiras
│   ├── investment_service.py # investimentos e dividendos
│   ├── backup_service.py     # exportar e restaurar
│   └── utils.py              # centavos, meses, formatação
├── ui/
│   ├── shared.py             # período, privacidade, cartões, estilo
│   ├── money.py              # ← única saída de valor monetário
│   └── forms.py              # formulário de gasto (Home e Gastos)
├── migrations/               # Alembic
├── tests/                    # pytest
├── data/financeiro.db        # seu banco (não versionado)
└── import_excel.py           # importação única da planilha
```

## Migrações do banco

O esquema é versionado com Alembic. Ao abrir o aplicativo, as migrações
pendentes são aplicadas sozinhas, sempre depois de uma cópia automática do
banco em `data/backups/pre_migracao_*.db`. Nada é apagado e recriado.

Para rodar à mão:

```bash
alembic upgrade head
```

## Testes

```bash
pytest
```

Cobrem o rateio sem perda de centavos, a regra da meta com redirecionamento
da sobra, a invalidação das separações quando entra renda nova, o
parcelamento exato (R$ 100 em 3x = 33,33 + 33,33 + 33,34), a exclusão em
cascata das parcelas, o versionamento das categorias por mês, a agregação
anual, o patrimônio sem dupla contagem, o mascaramento do modo privacidade
e a arquitetura (nenhuma página calculando dinheiro ou dependendo de nomes
de categoria).

## Observação sobre o OneDrive

A pasta está dentro do OneDrive, então seus dados são sincronizados — o que
serve de backup extra. Em compensação, evite abrir o app em dois computadores
ao mesmo tempo: o SQLite não gosta de dois escritores simultâneos. Se isso for
acontecer, migre para PostgreSQL.
