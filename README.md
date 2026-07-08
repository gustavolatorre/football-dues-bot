<a id="english"></a>

# Football Dues Bot ⚽

**[English](#english)** · **[Português](#portugues)**

Telegram bot that automates the monthly dues of a football (soccer) group:
it registers players, charges on a fixed day of the month, **reads the PIX receipt
via OCR** (payer, receiver, amount and date), confirms the payment automatically when
everything matches and, when in doubt, forwards it to the admin to approve with one tap.

## Features

- **Registration** via chat (`/start`), with editing (`/editar`) and lookup (`/status`);
  `/ajuda` and Telegram's native command menu help the user find their way around.
- **Monthly charging** from a fixed day (`DIA_COBRANCA`), with a **daily reminder**
  to whoever is behind until they pay — idempotent (at most 1 reminder per day). A new
  player is already charged for the month they join (see "How charging works").
- **Receipt (photo/PDF)** → OCR + validation of **payer + receiver + amount + date +
  transaction**. It auto-approves only when all 5 match: payer ≈ registered payer name,
  receiver = the group, **amount between 25% of the dues and the ceiling**, **date within
  the open period** (from the oldest month owed up to today) and a **readable transaction
  ID** (without it there is no way to deduplicate — goes to the admin). **Anti-duplication**
  (the same transaction ID / file never counts twice).
- **Never loses money:** anything that is not auto-approved is **never discarded** — it
  becomes `pendente_admin` and goes to the admin. If OCR did not read the amount (or read
  an absurd one, above the ceiling), the admin **enters the amount** with a button and
  approves; any excess becomes credit. `/pendentes` re-lists everything still awaiting a
  decision.
- **Money-based running balance**: it sums the months in arrears, accepts **partial
  payments**, settles **several months at once** and generates **credit** when the player
  overpays — it charges only what is missing. Amounts are always shown explicitly in R$
  to both player and admin.
- **Admin panel**: `/relatorio` (totals, up-to-date × delinquent, collected, receivable +
  a list of debtors/credits) with a "charge everyone" button; `/cobrar`; `/pendentes`
  (queue of receipts to review); and player management (`/jogadores`, `/desativar`,
  `/reativar`, `/remover`).
- Resilient to crashes/redeploys (reconciliation on startup + every N hours).
- **Concurrency-ready:** it processes several users in parallel (`concurrent_updates`),
  with **atomic** receipt deduplication (UNIQUE index on `transacao`).

## Stack

Python 3.11 · python-telegram-bot v21 (async + JobQueue) · SQLAlchemy 2 ·
SQLite (dev) / Postgres (Railway) · Tesseract OCR + pdfplumber · rapidfuzz.

## Layout

```text
bot-telegram-fut/
├── main.py                     # Bootstrap: Application + handlers + JobQueue + concurrent_updates + post_init
├── config.py                   # Env vars (.env), fail-fast validar_config(), TZ applied to the process
├── database/
│   ├── __init__.py             # Engine/SessionLocal (SQLite dev · Postgres prod) + column migration (no Alembic)
│   ├── models.py               # ORM 2.0: Jogador, Pagamento (transacao UNIQUE for dedup, lazy="raise")
│   └── repo.py                 # Data access: running balance, atomic dedup, report without N+1, pause on reactivate
├── services/
│   ├── ocr.py                  # Extracts {amount,date,key,payer,receiver,transaction} from the PIX (regex + Tesseract/pdfplumber)
│   ├── validador.py            # 5 checks: amount in [25% dues, ceiling] · date in [floor,today] · payer≈nome_pix · receiver=key/name · transaction read
│   ├── cobranca.py             # Idempotent reconciliation + balance (owing/credit) + messages (frase_falta)
│   └── normalizacao.py         # Shared text normalization (no accents/case) — used by ocr and validador
├── handlers/
│   ├── registro.py             # /start and /editar — ConversationHandler (name -> pix name -> phone)
│   ├── comprovante.py          # photo/PDF -> OCR -> dedup -> validate -> auto-approve OR always forward to admin
│   └── admin.py                # /relatorio /cobrar /pendentes /jogadores /desativar/reativar/remover + "enter amount" button
├── jobs/
│   └── scheduler.py            # JobQueue: reconcile on startup + every RECONCILE_INTERVAL_HORAS (catch-up)
├── scripts/                    # validar_ocr_amostras.py · resetar_db.py · backdate_teste.py (utilities/testing)
├── tests/                      # pytest: conftest + test_ocr · test_validador · test_cobranca · test_validacao_admin · test_parse_valor · test_handler_{registro,comprovante,admin} (98 tests, fictitious data)
├── .github/workflows/ci.yml    # CI: ruff + pytest on every push/PR
├── Dockerfile                  # python:3.11-slim + tesseract-ocr(+por) + poppler-utils + tzdata
├── docker-compose.yml          # Local topology: bot + Postgres (mirrors Railway)
├── pyproject.toml              # Metadata + ruff config (line-length 100) and pytest
├── requirements.txt            # Production: PTB v21 · SQLAlchemy 2 · psycopg2 · pytesseract · pdfplumber · opencv · rapidfuzz
├── requirements-dev.txt        # Dev: pytest · pytest-cov · ruff (kept out of the Docker image)
├── .env.example                # Variable template (no secrets)
├── .dockerignore · .gitignore  # Ignore .venv · .env · samples/PII · caches · data/
└── README.md                   # This file
```

## Configuration

Copy `.env.example` to `.env` and fill it in:

| Variable | Example | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | `123:ABC...` | Token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | `12345678` | Admin IDs (comma-separated). Get yours from [@userinfobot](https://t.me/userinfobot) |
| `MENSALIDADE_VALOR` | `40.00` | Monthly dues amount |
| `DIA_COBRANCA` | `10` | Day of the month charging opens (1–28) |
| `PIX_DESTINO` | `+5511912345678` | PIX key that receives the dues (phone; other key types validate by name only) |
| `NOME_RECEBEDOR` | `Maria Exemplo Silva` | Receiver name (receiver fallback) |
| `DATABASE_URL` | `sqlite:///data/bot.db` | Postgres on Railway (injected) |
| `TZ` | `America/Sao_Paulo` | Timezone used by `DIA_COBRANCA` |
| `RECONCILE_INTERVAL_HORAS` | `3` | Reconciliation interval |
| `OCR_CONFIANCA_MIN` | `80` | Fuzzy name-match threshold |
| `MAX_MENSALIDADES_ADIANTADO` | `3` | Auto-approval ceiling = current debt + N months. Above it → admin |

> Changed something in `.env`? Restart the process/container (`docker compose up -d` or
> `restart bot`) for the change to take effect.

## How charging works

- Reconciliation runs on **startup** and **every `RECONCILE_INTERVAL_HORAS`** (default 3h).
- The bot works with a **money-based running balance**: `balance = total paid − total expected`
  (expected = number of due months × dues amount):
  - `balance < 0` → **owing** (daily reminder until settled);
  - `balance > 0` → **up to date with credit** (automatically offsets the next month);
  - `balance = 0` → **up to date**.
- **A new player is charged for the month they join** (before the due day → on the due day;
  on or after it → as soon as they join). Past months in arrears are **always** charged,
  even before the current month's due day.
- **Partial payments are accepted** and offset the balance; paying 2 months at once settles
  both; overpaying becomes **credit**. The bot charges **only what is missing**. Pre-paying
  is supported.
- **Anti-duplication:** the same receipt (same transaction ID or file) never counts twice —
  deduplication is **atomic** (UNIQUE index on `transacao`).
- Each player stores the **dues amount in force at signup** — changing the value in `.env`
  affects only new registrations (it does not recompute the past of existing players).
- **Deactivating pauses charging:** on `/reativar`, the period the player stayed inactive is
  **not charged** (the signup date advances by the paused time). The count is by **calendar
  month**: deactivating and reactivating within the same month waives nothing; crossing into
  the next month waives that month — if fine-tuning is needed, the admin corrects it with a
  manually approved payment. `/editar` does **not** reactivate anyone (only `/reativar` does).
- Every message (player and admin) shows the **amounts in R$** (owed/credit).
- `/cobrar` (or the button in `/relatorio`) **forces** charging right away, ignoring the day.

## How receipt validation works

The receipt goes through 5 checks. **It auto-approves only when all 5 pass**; anything else
goes to the admin (it is never discarded):

| Check | Rule |
|-------|------|
| **payer** | payer name ≈ player's `nome_pix` (fuzzy ≥ `OCR_CONFIANCA_MIN`) |
| **receiver** | PIX key = `PIX_DESTINO` **or** receiver name ≈ `NOME_RECEBEDOR` (the key parser recognizes phone `+55…`; CPF/e-mail/random keys fall back to the name) |
| **amount** | read and within **`25% of dues ≤ amount ≤ ceiling`** (`ceiling` = current debt + `MAX_MENSALIDADES_ADIANTADO` months; below the minimum it is suspected of a misread) |
| **date** | within **`[floor, today]`** (`floor` = 1st day of the oldest still-open month; the current month is **always** payable) |
| **transaction** | end-to-end (E2E) ID read on the receipt — required for deduplication; without it, re-sending the same photo would get another `file_id` and count twice |

- **`floor` (minimum date):** advances as months get settled, but never past the current
  month — so **early payment for the current month is always accepted** and receipts from
  already-paid/old periods go to the admin.
- **`ceiling` (maximum amount):** above it, the amount is treated as "absurd" (e.g. OCR read
  an extra digit) and goes to the admin. Since the ceiling includes
  `MAX_MENSALIDADES_ADIANTADO` months, you can **pre-pay** several months without landing in
  the queue.
- **Nothing is lost:** whatever fails any check is stored as `pendente_admin` and forwarded
  with buttons. If the **amount** is the problem (unread or above the ceiling), the admin uses
  **"✏️ Enter amount and approve"** — the entered amount offsets the balance and the excess
  becomes credit. If the amount was read and is within the ceiling, the admin sees
  **"✅ Approve (R$ X)"**, **"✏️ Fix amount"** and **"❌ Reject"**.
- **`/pendentes`** re-lists every receipt awaiting a decision (with the same buttons), so the
  admin never forgets one.

> **Note on anti-fraud:** the bot validates the receipt's **text** (OCR); it does not query
> the bank/PIX to confirm the money actually arrived. Against honest mistakes and duplicates
> it is strong (5 independent factors + human fallback); a deliberately forged image is the
> inherent limit of any OCR-based bot — mitigated here by the trusted-group context and by the
> admin seeing every report.

## Balance examples

Formula: **`balance = total paid − (due months × dues)`**
→ `balance < 0` owing · `balance > 0` credit · `balance = 0` up to date.

Examples with **dues R$ 40**, due day **10**, checked **after the 10th**
(values verified automatically by the tests):

| Situation | Expected | Paid | Balance | Result |
|-----------|---------:|-----:|--------:|--------|
| Joined this month, hasn't paid | R$ 40 | R$ 0 | −40 | owing **R$ 40** (1 month) |
| Joined 2 months ago, hasn't paid | R$ 80 | R$ 0 | −80 | owing **R$ 80** (2 months) |
| Joined 3 months ago, hasn't paid | R$ 120 | R$ 0 | −120 | owing **R$ 120** (3 months) |
| Owes 2 months, pays R$ 40 | R$ 80 | R$ 40 | −40 | owing **R$ 40** (partial) |
| Owes 2 months, pays R$ 80 | R$ 80 | R$ 80 | 0 | **up to date** ✅ |
| Owes 1 month, pays R$ 100 | R$ 40 | R$ 100 | +60 | **up to date** + credit **R$ 60** |

> Before the due day, the current month is **not yet** part of "expected" — whoever joined
> this month only starts owing when the day arrives. Past months always count.

### Credit offsets the next months (charges only what's missing)

Dues R$ 40, a player pays R$ 100 in a month they owed R$ 40:

| Month | Expected (cum.) | Paid (cum.) | Balance | What the bot does |
|-------|----------------:|------------:|--------:|-------------------|
| 1 | R$ 40 | R$ 100 | +60 | up to date, **credit R$ 60** |
| 2 | R$ 80 | R$ 100 | +20 | up to date (credit covers), **doesn't charge** |
| 3 | R$ 120 | R$ 100 | −20 | charges **only the R$ 20** missing |

## Run locally

Prerequisite: **Tesseract** (`tesseract-ocr` + `por` language) and **poppler** installed.

- Windows: install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and
  [poppler](https://github.com/oschwartz10612/poppler-windows/releases); make sure
  `tesseract` is on the PATH.
- Linux/Mac: `apt install tesseract-ocr tesseract-ocr-por poppler-utils` / `brew install tesseract poppler`.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt -r requirements-dev.txt   # dev adds pytest/ruff
cp .env.example .env          # then fill in BOT_TOKEN and ADMIN_IDS
python main.py
```

### Check OCR against your samples

```bash
python scripts/validar_ocr_amostras.py
```

### Tests

```bash
pytest -q
```

## Test with Docker (locally, before deploy)

The image already ships Tesseract + poppler, so it's the most faithful way to test before
Railway. A `docker-compose.yml` brings up **bot + Postgres** (mirrors Railway).

**1) Check OCR against the samples (no token needed):**

```bash
docker build -t mensalidade-bot .
docker run --rm -v "${PWD}/samples:/app/samples" mensalidade-bot \
  python scripts/validar_ocr_amostras.py
```

**2) Run the tests inside the image:**

```bash
docker run --rm -v "${PWD}/tests:/app/tests" mensalidade-bot \
  sh -c "pip install --user -q -r requirements-dev.txt && python -m pytest -q"
```

(`pytest` no longer ships in the production image — it's a dev dependency.)

**3) Bring up the full bot (needs `.env` with BOT_TOKEN and ADMIN_IDS):**

```bash
docker compose up --build
```

Create a separate **test bot** on @BotFather so you don't mix it with production.
The compose Postgres persists in a volume (`pgdata`); stop with `docker compose down`
(or `down -v` to wipe the data).

## Deploy on Railway

1. Push the repository; Railway detects the `Dockerfile` (already installs Tesseract + poppler).
2. Add the **PostgreSQL** plugin — it injects `DATABASE_URL` automatically.
3. Set the variables (`BOT_TOKEN`, `ADMIN_IDS`, `MENSALIDADE_VALOR`, `DIA_COBRANCA`,
   `PIX_DESTINO`, `NOME_RECEBEDOR`, `TZ=America/Sao_Paulo`).
4. Deploy. The bot comes up in long-polling and reconciliation runs on startup.

> It's a **worker** (long-polling), **not** a web service — it does not need an exposed
> port or an HTTP health check.

## Commands

| Command | Who | Action |
|---------|-----|--------|
| `/start` | player | Registration |
| `/editar` | player | Edit their own data |
| `/status` | player | Show their own data and status |
| `/ajuda` (`/help`) | player | List what can be done |
| send photo/PDF | player | Send the receipt (OCR + validation) |
| `/relatorio` | admin | Monthly report + "Charge delinquents now" button |
| `/cobrar` | admin | Charge all delinquents right away (forced, ignores the day) |
| `/pendentes` | admin | Re-list the receipts awaiting a decision (with the buttons) |
| `/jogadores` | admin | List players with IDs and status |
| `/desativar <id>` | admin | Stop charging someone (keeps the history) |
| `/reativar <id>` | admin | Resume charging |
| `/remover <id> sim` | admin | Delete the player and their payments permanently (without `sim` it only shows the confirmation warning) |

> Telegram's native menu (the "/" button) suggests `start`, `editar`, `status` and `ajuda`.
> The admin commands appear in `/ajuda` only for admins.

## End-to-end test script

Pre: bot running (compose or local) with `.env` filled in. Tip: for a sample receipt to be
**auto-approved**, register the `nome_pix` equal to the receipt's **payer** and set
`PIX_DESTINO`/`NOME_RECEBEDOR` equal to its **receiver**.

1. **Registration:** send `/start` and **go all the way** (name → pix name → phone/`/pular`)
   until you see *"Cadastro concluído ✅"*. If you stop midway, nothing is saved.
2. **Receipt:** send a photo/PDF from `samples/comprovantes/`.
   - If the 5 fields match (payer = your `nome_pix`, receiver = `PIX_DESTINO`/`NOME_RECEBEDOR`,
     amount between 25% of dues and the ceiling, date within the open period, and a readable
     transaction ID) → **auto-approved** (offsets the balance).
   - Otherwise → it goes to the **admin** (never discarded). If the amount wasn't read / came
     absurd, use **"✏️ Enter amount and approve"**; otherwise **Approve/Fix/Reject**.
   - `/pendentes` (admin) re-lists what's still to decide.
3. **Report:** send `/relatorio` (as admin).
4. **Charging:** send `/cobrar` (or the button in `/relatorio`). Since the player is already
   charged for the month they joined, you get the charge right away. Repeat `/cobrar` the same
   day: it doesn't arrive a 2nd time (idempotency).

### Test utilities

```bash
# delete ALL players/payments:
docker compose exec bot python scripts/resetar_db.py --sim
# simulate "owing since last month" (backdates a player's signup):
docker compose exec bot python scripts/backdate_teste.py <your_telegram_id>
```

> OCR calibration: 7 banks (Nubank, Bradesco, Santander, PicPay, C6, Itaú, BB) and
> 19 sample receipts — details in `samples/CALIBRACAO_OCR.md` (local, outside git).

---

<a id="portugues"></a>

# Bot de Mensalidade do Futebol ⚽

**[English](#english)** · **[Português](#portugues)**

Bot do Telegram que automatiza a cobrança da mensalidade de um grupo de futebol:
cadastra jogadores, cobra no dia fixo do mês, **lê o comprovante do PIX por OCR**
(origem, destino, valor e data), confirma o pagamento automaticamente quando tudo
bate e, em caso de dúvida, encaminha para o admin aprovar com um clique.

## Funcionalidades

- **Cadastro** pelo chat (`/start`), com edição (`/editar`) e consulta (`/status`);
  `/ajuda` e menu nativo de comandos do Telegram para o usuário se localizar.
- **Cobrança mensal** a partir do dia fixo (`DIA_COBRANCA`), com **lembrete diário**
  ao inadimplente até o pagamento — idempotente (no máximo 1 aviso por dia). Jogador
  novo já é cobrado no mês em que entra (ver "Como funciona a cobrança").
- **Comprovante (foto/PDF)** → OCR + validação de **origem + destino + valor + data +
  transação**. Auto-aprova só quando as 5 baterem: origem ≈ pagador, destino = grupo,
  **valor lido entre 25% da mensalidade e o teto**, **data no período em aberto** (do mês
  mais antigo devido até hoje) e **ID de transação legível** (sem ele não há como
  deduplicar — vai ao admin). **Anti-duplicidade** (mesmo ID de transação/arquivo não
  conta duas vezes).
- **Nunca perde dinheiro:** o que não auto-aprova **nunca é descartado** — vira
  `pendente_admin` e vai ao admin. Se o OCR não leu o valor (ou leu um valor absurdo,
  acima do teto), o admin **informa o valor** por um botão e aprova; o excedente vira
  crédito. `/pendentes` relista tudo que ainda aguarda decisão.
- **Saldo acumulado em dinheiro**: soma meses em atraso, aceita **pagamento parcial**,
  quita **vários meses de uma vez** e gera **crédito** quando paga a mais — cobra só o
  que falta. Valores sempre explícitos em R$ para jogador e admin.
- **Painel do admin**: `/relatorio` (total, em dia × inadimplentes, arrecadado, a receber
  + lista de devedores/créditos) com botão de cobrar todos; `/cobrar`; `/pendentes`
  (fila de comprovantes a revisar); e gestão de jogadores (`/jogadores`, `/desativar`,
  `/reativar`, `/remover`).
- Resiliente a quedas/redeploys (reconciliação no startup + a cada N horas).
- **Pronto para concorrência:** processa vários usuários em paralelo (`concurrent_updates`),
  com deduplicação **atômica** de comprovante (índice UNIQUE em `transacao`).

## Stack

Python 3.11 · python-telegram-bot v21 (async + JobQueue) · SQLAlchemy 2 ·
SQLite (dev) / Postgres (Railway) · Tesseract OCR + pdfplumber · rapidfuzz.

## Estrutura

```text
bot-telegram-fut/
├── main.py                     # Bootstrap: Application + handlers + JobQueue + concurrent_updates + post_init
├── config.py                   # Variaveis de ambiente (.env), validar_config() fail-fast, TZ no processo
├── database/
│   ├── __init__.py             # Engine/SessionLocal (SQLite dev · Postgres prod) + migracao de colunas (sem Alembic)
│   ├── models.py               # ORM 2.0: Jogador, Pagamento (transacao UNIQUE p/ dedup, lazy="raise")
│   └── repo.py                 # Acesso a dados: saldo acumulado, dedup atomico, relatorio sem N+1, pausa ao reativar
├── services/
│   ├── ocr.py                  # Extrai {valor,data,chave,origem,destino,transacao} do PIX (regex + Tesseract/pdfplumber)
│   ├── validador.py            # 5 checagens: valor em [25% mens, teto] · data em [piso,hoje] · origem≈nome_pix · destino=chave/nome · transacao lida
│   ├── cobranca.py             # Reconciliacao idempotente + saldo (devendo/credito) + mensagens (frase_falta)
│   └── normalizacao.py         # Normalizacao de texto compartilhada (sem acento/caixa) — usada por ocr e validador
├── handlers/
│   ├── registro.py             # /start e /editar — ConversationHandler (nome -> nome_pix -> telefone)
│   ├── comprovante.py          # foto/PDF -> OCR -> dedup -> valida -> auto-aprova OU sempre encaminha ao admin
│   └── admin.py                # /relatorio /cobrar /pendentes /jogadores /desativar/reativar/remover + botao "informar valor"
├── jobs/
│   └── scheduler.py            # JobQueue: reconciliar no startup + a cada RECONCILE_INTERVAL_HORAS (catch-up)
├── scripts/                    # validar_ocr_amostras.py · resetar_db.py · backdate_teste.py (utilitarios/teste)
├── tests/                      # pytest: conftest + test_ocr · test_validador · test_cobranca · test_validacao_admin · test_parse_valor · test_handler_{registro,comprovante,admin} (98 testes, dados fictícios)
├── .github/workflows/ci.yml    # CI: ruff + pytest a cada push/PR
├── Dockerfile                  # python:3.11-slim + tesseract-ocr(+por) + poppler-utils + tzdata
├── docker-compose.yml          # Topologia local: bot + Postgres (espelha o Railway)
├── pyproject.toml              # Metadata + config do ruff (line-length 100) e pytest
├── requirements.txt            # Producao: PTB v21 · SQLAlchemy 2 · psycopg2 · pytesseract · pdfplumber · opencv · rapidfuzz
├── requirements-dev.txt        # Dev: pytest · pytest-cov · ruff (fora da imagem Docker)
├── .env.example                # Modelo de variaveis (sem segredos)
├── .dockerignore · .gitignore  # Ignora .venv · .env · samples/PII · caches · data/
└── README.md                   # Este arquivo
```

## Configuração

Copie `.env.example` para `.env` e preencha:

| Variável | Exemplo | Descrição |
|----------|---------|-----------|
| `BOT_TOKEN` | `123:ABC...` | Token do [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | `12345678` | IDs de admin (vírgula). Veja no [@userinfobot](https://t.me/userinfobot) |
| `MENSALIDADE_VALOR` | `40.00` | Valor da mensalidade |
| `DIA_COBRANCA` | `10` | Dia do mês que abre a cobrança (1–28) |
| `PIX_DESTINO` | `+5511912345678` | Chave PIX que recebe a mensalidade (telefone; outros tipos de chave validam só pelo nome) |
| `NOME_RECEBEDOR` | `Maria Exemplo Silva` | Nome do recebedor (fallback do destino) |
| `DATABASE_URL` | `sqlite:///data/bot.db` | Postgres no Railway (injetado) |
| `TZ` | `America/Sao_Paulo` | Fuso usado no `DIA_COBRANCA` |
| `RECONCILE_INTERVAL_HORAS` | `3` | Intervalo da reconciliação |
| `OCR_CONFIANCA_MIN` | `80` | Limiar do fuzzy match de nomes |
| `MAX_MENSALIDADES_ADIANTADO` | `3` | Teto de auto-aprovação = dívida + N mensalidades. Acima disso → admin |

> Mudou algo no `.env`? Reinicie o processo/container (`docker compose up -d` ou
> `restart bot`) para a alteração valer.

## Como funciona a cobrança

- A reconciliação roda no **startup** e **a cada `RECONCILE_INTERVAL_HORAS`** (padrão 3h).
- O bot trabalha com **saldo acumulado em dinheiro**: `saldo = total pago − total esperado`
  (esperado = nº de mensalidades vencidas × valor):
  - `saldo < 0` → **devendo** (lembrete diário até quitar);
  - `saldo > 0` → **em dia com crédito** (abate automaticamente o próximo mês);
  - `saldo = 0` → **em dia**.
- **Jogador novo é cobrado no mês em que entra** (antes do vencimento → no dia do
  vencimento; no dia ou depois → assim que entra). Meses passados em atraso **sempre**
  são cobrados, mesmo antes do vencimento do mês corrente.
- **Pagamento parcial é aceito** e abate do saldo; pagar 2 meses de uma vez quita os 2;
  pagar a mais vira **crédito**. O bot cobra **só o que falta**. Dá para **pré-pagar**.
- **Anti-duplicidade:** o mesmo comprovante (mesmo ID de transação ou arquivo) não conta
  duas vezes — a deduplicação é **atômica** (índice UNIQUE em `transacao`).
- Cada jogador guarda o **valor da mensalidade vigente na adesão** — mudar o valor no
  `.env` afeta só novos cadastros (não recalcula o passado de quem já joga).
- **Desativar pausa a cobrança:** ao `/reativar`, o período em que o jogador ficou
  inativo **não é cobrado** (a data de adesão avança pelo tempo parado). A conta é por
  **mês-calendário**: desativar e reativar dentro do mesmo mês não isenta nada; virar o
  mês desativado isenta o mês — se precisar de ajuste fino, o admin corrige com um
  pagamento aprovado manualmente. `/editar` **não** reativa ninguém (só `/reativar`).
- Todas as mensagens (jogador e admin) mostram os **valores em R$** (devido/crédito).
- `/cobrar` (ou o botão no `/relatorio`) **força** a cobrança na hora, ignorando o dia.

## Como funciona a validação do comprovante

O comprovante passa por 5 checagens. **Auto-aprova só quando as 5 batem**; qualquer
outra coisa vai ao admin (nunca é descartada):

| Checagem | Regra |
|----------|-------|
| **origem** | nome do pagador ≈ `nome_pix` do jogador (fuzzy ≥ `OCR_CONFIANCA_MIN`) |
| **destino** | chave PIX = `PIX_DESTINO` **ou** nome do recebedor ≈ `NOME_RECEBEDOR` (o parser de chave reconhece telefone `+55…`; CPF/e-mail/aleatória caem no nome) |
| **valor** | lido e no intervalo **`25% da mensalidade ≤ valor ≤ teto`** (`teto` = dívida atual + `MAX_MENSALIDADES_ADIANTADO` mensalidades; abaixo do mínimo é suspeita de leitura errada) |
| **data** | no intervalo **`[piso, hoje]`** (`piso` = 1º dia do mês mais antigo ainda em aberto; o mês corrente é **sempre** pagável) |
| **transação** | ID fim-a-fim (E2E) lido no comprovante — obrigatório para a deduplicação; sem ele, reenviar a mesma foto geraria outro `file_id` e contaria 2x |

- **`piso` (data mínima):** avança conforme os meses já quitados, mas nunca passa do mês
  atual — assim, **pagamento antecipado do mês corrente sempre é aceito** e comprovantes
  de períodos já pagos/antigos vão ao admin.
- **`teto` (valor máximo):** acima dele o valor é tratado como "absurdo" (ex.: OCR leu um
  dígito a mais) e vai ao admin. Como o teto inclui `MAX_MENSALIDADES_ADIANTADO`
  mensalidades, dá para **pré-pagar** vários meses sem cair na fila.
- **Nada se perde:** o que falhar em qualquer checagem é gravado como `pendente_admin` e
  encaminhado com botões. Se o **valor** for o problema (não lido ou acima do teto), o
  admin usa **"✏️ Informar valor e aprovar"** — o valor informado abate o saldo e o
  excedente vira crédito. Se o valor foi lido e está dentro do teto, o admin vê
  **"✅ Aprovar (R$ X)"**, **"✏️ Corrigir valor"** e **"❌ Rejeitar"**.
- **`/pendentes`** relista todos os comprovantes aguardando decisão (com os mesmos
  botões), para o admin nunca esquecer nenhum.

> **Sobre anti-fraude:** o bot valida o **texto** do comprovante (OCR); ele não consulta o
> banco/PIX para confirmar que o dinheiro caiu. Contra erro honesto e duplicidade é forte
> (5 fatores independentes + fallback humano); uma imagem deliberadamente forjada é o limite
> inerente de qualquer bot baseado em OCR — mitigado aqui pelo contexto (grupo de amigos) e
> pela visibilidade do admin em todo relatório.

## Exemplos de saldo

Fórmula: **`saldo = total pago − (meses vencidos × mensalidade)`**
→ `saldo < 0` devendo · `saldo > 0` crédito · `saldo = 0` em dia.

Exemplos com **mensalidade R$ 40**, vencimento **dia 10**, consultando **após o dia 10**
(valores conferidos automaticamente nos testes):

| Situação | Esperado | Pago | Saldo | Resultado |
|----------|---------:|-----:|------:|-----------|
| Entrou este mês, não pagou | R$ 40 | R$ 0 | −40 | devendo **R$ 40** (1 mês) |
| Entrou há 2 meses, não pagou | R$ 80 | R$ 0 | −80 | devendo **R$ 80** (2 meses) |
| Entrou há 3 meses, não pagou | R$ 120 | R$ 0 | −120 | devendo **R$ 120** (3 meses) |
| Deve 2 meses, paga R$ 40 | R$ 80 | R$ 40 | −40 | devendo **R$ 40** (parcial) |
| Deve 2 meses, paga R$ 80 | R$ 80 | R$ 80 | 0 | **em dia** ✅ |
| Deve 1 mês, paga R$ 100 | R$ 40 | R$ 100 | +60 | **em dia** + crédito **R$ 60** |

> Antes do dia de vencimento, o mês corrente ainda **não entra** no "esperado" — quem
> entrou neste mês só passa a dever quando o dia chega. Meses passados contam sempre.

### O crédito abate os próximos meses (cobra só o que falta)

Mensalidade R$ 40, jogador paga R$ 100 num mês em que devia R$ 40:

| Mês | Esperado (acum.) | Pago (acum.) | Saldo | O bot faz |
|-----|-----------------:|-------------:|------:|-----------|
| 1 | R$ 40 | R$ 100 | +60 | em dia, **crédito R$ 60** |
| 2 | R$ 80 | R$ 100 | +20 | em dia (crédito cobre), **não cobra** |
| 3 | R$ 120 | R$ 100 | −20 | cobra **só os R$ 20** que faltam |

## Rodar localmente

Pré-requisito: **Tesseract** (`tesseract-ocr` + idioma `por`) e **poppler** instalados.

- Windows: instale o [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) e o
  [poppler](https://github.com/oschwartz10612/poppler-windows/releases); garanta que
  `tesseract` está no PATH.
- Linux/Mac: `apt install tesseract-ocr tesseract-ocr-por poppler-utils` / `brew install tesseract poppler`.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt -r requirements-dev.txt   # dev inclui pytest/ruff
cp .env.example .env          # e preencha BOT_TOKEN e ADMIN_IDS
python main.py
```

### Conferir o OCR nas suas amostras

```bash
python scripts/validar_ocr_amostras.py
```

### Testes

```bash
pytest -q
```

## Testar no Docker (local, antes do deploy)

A imagem já traz Tesseract + poppler, então é a forma mais fiel de testar antes do
Railway. Há um `docker-compose.yml` que sobe **bot + Postgres** (espelha o Railway).

**1) Conferir o OCR nas amostras (não precisa de token):**

```bash
docker build -t mensalidade-bot .
docker run --rm -v "${PWD}/samples:/app/samples" mensalidade-bot \
  python scripts/validar_ocr_amostras.py
```

**2) Rodar os testes dentro da imagem:**

```bash
docker run --rm -v "${PWD}/tests:/app/tests" mensalidade-bot \
  sh -c "pip install --user -q -r requirements-dev.txt && python -m pytest -q"
```

(O `pytest` não vai mais na imagem de produção — é dependência de dev.)

**3) Subir o bot completo (precisa do `.env` com BOT_TOKEN e ADMIN_IDS):**

```bash
docker compose up --build
```

Crie um **bot de teste** separado no @BotFather para não misturar com produção.
O Postgres do compose persiste em um volume (`pgdata`); pare com `docker compose down`
(ou `down -v` para apagar os dados).

## Deploy no Railway

1. Suba o repositório; o Railway detecta o `Dockerfile` (já instala Tesseract + poppler).
2. Adicione o plugin **PostgreSQL** — ele injeta `DATABASE_URL` automaticamente.
3. Configure as variáveis (`BOT_TOKEN`, `ADMIN_IDS`, `MENSALIDADE_VALOR`,
   `DIA_COBRANCA`, `PIX_DESTINO`, `NOME_RECEBEDOR`, `TZ=America/Sao_Paulo`).
4. Deploy. O bot sobe em long-polling e a reconciliação roda no startup.

> É um **worker** (long-polling), **não** um web service — não precisa expor porta nem
> health check HTTP.

## Comandos

| Comando | Quem | Ação |
|---------|------|------|
| `/start` | jogador | Cadastro |
| `/editar` | jogador | Corrige os próprios dados |
| `/status` | jogador | Mostra os próprios dados e situação |
| `/ajuda` (`/help`) | jogador | Lista o que dá para fazer |
| enviar foto/PDF | jogador | Envia o comprovante (OCR + validação) |
| `/relatorio` | admin | Relatório do mês + botão "Cobrar inadimplentes agora" |
| `/cobrar` | admin | Cobra todos os inadimplentes na hora (força, ignora o dia) |
| `/pendentes` | admin | Relista os comprovantes aguardando decisão (com os botões) |
| `/jogadores` | admin | Lista jogadores com IDs e status |
| `/desativar <id>` | admin | Para de cobrar alguém (mantém o histórico) |
| `/reativar <id>` | admin | Volta a cobrar |
| `/remover <id> sim` | admin | Exclui o jogador e seus pagamentos definitivamente (sem o `sim`, só mostra o aviso de confirmação) |

> O menu nativo do Telegram (botão "/") sugere `start`, `editar`, `status` e `ajuda`.
> Os comandos de admin aparecem no `/ajuda` apenas para quem é admin.

## Roteiro de teste end-to-end

Pré: bot rodando (compose ou local) com `.env` preenchido. Dica: para um
comprovante de amostra ser **auto-aprovado**, cadastre o `nome_pix` igual ao
**pagador** do comprovante e configure `PIX_DESTINO`/`NOME_RECEBEDOR` igual ao
**recebedor** dele.

1. **Cadastro:** mande `/start` e **vá até o fim** (nome → nome no PIX → telefone/
   `/pular`) até ver *"Cadastro concluído ✅"*. Se parar no meio, nada é salvo.
2. **Comprovante:** envie uma foto/PDF de `samples/comprovantes/`.
   - Se os 5 campos baterem (origem = seu `nome_pix`, destino = `PIX_DESTINO`/`NOME_RECEBEDOR`,
     valor entre 25% da mensalidade e o teto, data no período em aberto e ID de
     transação legível) → **auto-aprovado** (abate do saldo).
   - Senão → vai para o **admin** (nunca é descartado). Se o valor não foi lido/veio absurdo,
     use **"✏️ Informar valor e aprovar"**; senão, **Aprovar/Corrigir/Rejeitar**.
   - `/pendentes` (admin) relista o que ainda falta decidir.
3. **Relatório:** mande `/relatorio` (como admin).
4. **Cobrança:** mande `/cobrar` (ou o botão no `/relatorio`). Como o jogador já é
   cobrado no mês de entrada, você recebe a cobrança na hora. Repita `/cobrar` no
   mesmo dia: não chega 2ª vez (idempotência).

### Utilitários de teste

```bash
# apagar TODOS os jogadores/pagamentos:
docker compose exec bot python scripts/resetar_db.py --sim
# simular "deve desde o mês passado" (backdata a adesão de um id):
docker compose exec bot python scripts/backdate_teste.py <seu_telegram_id>
```

> Calibração do OCR: 7 bancos (Nubank, Bradesco, Santander, PicPay, C6, Itaú, BB) e
> 19 comprovantes de amostra — detalhes em `samples/CALIBRACAO_OCR.md` (local, fora do git).
