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
pip install -r requirements.txt
```

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

O script mostra tudo que encontrou, aponta divergências e pergunta antes de
gravar. Rodar de novo não duplica nada — ele avisa que já importou. Para
importar de propósito outra vez, use `--refazer`.

---

## Como o aplicativo pensa

**Recebido no mês** é a renda que entrou (exclui lançamentos do tipo *Saldo
inicial*). **Base de distribuição** é o que os percentuais rateiam — pode
incluir um saldo anterior que você queira distribuir. Os dois números aparecem
lado a lado justamente porque não são a mesma coisa.

**Separação** guarda o *valor* já reservado, não um sim/não. Se você separar
R$ 1.470 para Independência e depois receber mais dinheiro, o plano sobe, a
categoria volta para "pendente" e o app continua sabendo que R$ 1.470 já
foram separados — mostrando só quanto falta.

**Reserva** recebe no máximo o que falta para a meta. O que sobrar do
percentual vai automaticamente para Independência financeira.

**Viagem e Compras** são envelopes: acumulam entre meses (separações
confirmadas menos gastos da categoria). **Namorada, Amigos e Livre** são
orçamento do mês e recomeçam do zero.

**Configurações são versionadas por mês.** Mudar os percentuais hoje não
reescreve o plano de setembro — cada mês usa a versão vigente naquele mês.

Todas as regras vivem em `core/budget_service.py`. As telas só apresentam.

---

## Backup

Em **⚙️ Configurações → Fazer backup dos meus dados**:

- **Baixar banco (.db)** — cópia fiel, é o que restaura tudo.
- **Baixar tudo (.json)** — legível, para conferir ou migrar.

Para restaurar, envie o `.db` no mesmo painel. Antes de substituir, o app
guarda sozinho uma cópia do banco atual em `data/backups/`.

Backup manual: copie o arquivo `data/financeiro.db`.

---

## Trocar SQLite por PostgreSQL/Supabase

Nenhum código muda. Copie `.env.example` para `.env` e ajuste:

```
DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/banco
```

```bash
pip install "psycopg[binary]"
streamlit run app.py
```

As tabelas são criadas na primeira execução. Para levar os dados junto,
exporte o JSON antes de trocar.

## Publicar como site privado

1. Defina uma senha no `.env` do servidor:

   ```
   APP_PASSWORD=uma-senha-forte
   ```

   Com a variável definida, o app pede senha antes de abrir. Sem ela, roda
   direto — que é o caso no seu computador. A senha nunca fica no código.

2. Use PostgreSQL em vez de SQLite (veja acima): serviços de hospedagem
   costumam apagar o disco a cada deploy.

3. Publique em qualquer lugar que rode Python — Streamlit Community Cloud,
   Railway, Render, Fly.io ou um VPS. O comando é o mesmo:

   ```bash
   streamlit run app.py --server.port $PORT --server.address 0.0.0.0
   ```

4. Sirva sempre por HTTPS e não versione o `.env`.

---

## Estrutura

```
financeiro_app/
├── app.py                    # entrada: autenticação + navegação
├── pages/                    # uma tela por arquivo (só apresentação)
│   ├── dashboard.py
│   ├── receitas.py
│   ├── gastos.py
│   ├── fechamento.py
│   └── configuracoes.py
├── core/
│   ├── database.py           # conexão, criação do esquema, transações
│   ├── models.py             # tabelas
│   ├── repositories.py       # consultas e escritas (sem regra de negócio)
│   ├── budget_service.py     # ← todas as regras financeiras
│   ├── investment_service.py # investimentos e dividendos
│   ├── backup_service.py     # exportar e restaurar
│   └── utils.py              # centavos, meses, formatação
├── ui/shared.py              # cartões, seletor de mês, estilo
├── tests/                    # pytest
├── data/financeiro.db        # seu banco (não versionado)
└── import_excel.py           # importação única da planilha
```

## Testes

```bash
pytest
```

Cobrem o rateio sem perda de centavos, a regra da meta da reserva, a
invalidação das separações quando entra renda nova, o parcelamento exato
(R$ 100 em 3x = 33,33 + 33,33 + 33,34), a exclusão em cascata das parcelas, o
versionamento das configurações por mês e o patrimônio sem dupla contagem.

## Observação sobre o OneDrive

A pasta está dentro do OneDrive, então seus dados são sincronizados — o que
serve de backup extra. Em compensação, evite abrir o app em dois computadores
ao mesmo tempo: o SQLite não gosta de dois escritores simultâneos. Se isso for
acontecer, migre para PostgreSQL.
