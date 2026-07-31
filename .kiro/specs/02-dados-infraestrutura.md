# Spec 02 — Dados, Modelos e Infraestrutura

## Status: Draft

---

## 1. Visão Geral

Esta spec define todos os arquivos de dados, seus schemas, valores de seed para desenvolvimento, as configurações centralizadas do sistema e as dependências Python necessárias.

---

## 2. Arquivos de Dados (CSV)

### 2.1 `data/clientes.csv`

Base de clientes para autenticação e consulta de dados financeiros.

**Schema:**

| Coluna | Tipo | Descrição | Exemplo |
|--------|------|-----------|---------|
| `cpf` | string | CPF sem formatação (somente dígitos) | `"12345678901"` |
| `nome` | string | Nome completo do cliente | `"João Silva"` |
| `data_nascimento` | string | Formato `DD/MM/AAAA` | `"15/03/1985"` |
| `score_credito` | integer | Score de 0 a 1000 | `650` |
| `limite_credito` | float | Limite de crédito atual em R$ | `5000.00` |

**Regras:**
- `cpf` é chave primária (único, sem duplicatas).
- `data_nascimento` deve ser validada no formato `DD/MM/AAAA` na leitura.
- `score_credito` deve estar no intervalo `[0, 1000]`.
- `limite_credito` deve ser `>= 0.0`.

**Dados de seed (5 clientes para testes):**

```csv
cpf,nome,data_nascimento,score_credito,limite_credito
12345678901,João Silva,15/03/1985,750,5000.00
98765432100,Maria Oliveira,22/07/1990,420,1500.00
11122233344,Carlos Mendes,08/11/1978,600,3000.00
55566677788,Ana Souza,30/01/1995,850,10000.00
99988877766,Pedro Costa,14/06/1982,300,500.00
```

---

### 2.2 `data/score_limite.csv`

Tabela de referência que define o score mínimo necessário para cada faixa de limite de crédito solicitado.

**Schema:**

| Coluna | Tipo | Descrição | Exemplo |
|--------|------|-----------|---------|
| `limite_maximo` | float | Teto da faixa de limite (R$) | `2000.00` |
| `score_minimo` | integer | Score mínimo exigido para esta faixa | `300` |

**Regras:**
- Registros ordenados em ordem crescente de `limite_maximo`.
- O último registro usa `limite_maximo` como valor máximo absoluto do sistema (ex: `999999.99`).
- Para checar um `novo_limite_solicitado`: encontrar a primeira linha onde `limite_maximo >= novo_limite_solicitado` e verificar se `score_cliente >= score_minimo`.

**Dados de seed:**

```csv
limite_maximo,score_minimo
1000.00,200
2000.00,300
5000.00,450
10000.00,600
20000.00,750
50000.00,850
999999.99,950
```

---

### 2.3 `data/solicitacoes_aumento_limite.csv`

Registro persistente de todas as solicitações de aumento de limite processadas pelo sistema.

**Schema:**

| Coluna | Tipo | Descrição | Exemplo |
|--------|------|-----------|---------|
| `cpf_cliente` | string | CPF do cliente solicitante | `"12345678901"` |
| `data_hora_solicitacao` | string | Timestamp ISO 8601 | `"2026-07-31T10:30:00"` |
| `limite_atual` | float | Limite atual antes da solicitação | `5000.00` |
| `novo_limite_solicitado` | float | Novo limite desejado pelo cliente | `10000.00` |
| `status_pedido` | string | `'pendente'`, `'aprovado'` ou `'rejeitado'` | `"aprovado"` |

**Regras:**
- Arquivo criado automaticamente na primeira solicitação se não existir.
- Sempre append (nunca sobrescrever registros anteriores).
- `data_hora_solicitacao` sempre em UTC, formato `datetime.utcnow().isoformat()`.
- `status_pedido` só aceita os valores: `'pendente'`, `'aprovado'`, `'rejeitado'`.

**Cabeçalho inicial (criado automaticamente):**

```csv
cpf_cliente,data_hora_solicitacao,limite_atual,novo_limite_solicitado,status_pedido
```

---

## 3. Configurações Centralizadas (`config.py`)

Arquivo Python com todas as constantes do sistema. Sem lógica, apenas valores configuráveis.

```python
# config.py

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

CSV_CLIENTES          = DATA_DIR / "clientes.csv"
CSV_SCORE_LIMITE      = DATA_DIR / "score_limite.csv"
CSV_SOLICITACOES      = DATA_DIR / "solicitacoes_aumento_limite.csv"

# ── LLM ───────────────────────────────────────────────────────────────────
GEMINI_MODEL          = "gemini-2.0-flash"
GOOGLE_API_KEY        = os.getenv("GOOGLE_API_KEY", "")

# ── Autenticação ──────────────────────────────────────────────────────────
MAX_AUTH_ATTEMPTS     = 3

# ── Pesos do Score de Crédito ─────────────────────────────────────────────
PESO_RENDA            = 30

PESO_EMPREGO = {
    "formal":        300,
    "autonomo":      200,
    "desempregado":    0,
}

PESO_DEPENDENTES = {
    0: 100,
    1:  80,
    2:  60,
    3:  30,   # 3+ dependentes
}

PESO_DIVIDAS = {
    "sim": -100,
    "nao":  100,
}

SCORE_MIN = 0
SCORE_MAX = 1000

# ── Câmbio ─────────────────────────────────────────────────────────────────
# AwesomeAPI: gratuita, sem autenticação
# Endpoint: https://economia.awesomeapi.com.br/last/{moeda}-BRL
CAMBIO_API_BASE_URL   = "https://economia.awesomeapi.com.br/last"

MOEDAS_SUPORTADAS = {
    "dolar":  "USD",
    "euro":   "EUR",
    "libra":  "GBP",
    "iene":   "JPY",
    "bitcoin":"BTC",
    "USD":    "USD",
    "EUR":    "EUR",
    "GBP":    "GBP",
    "JPY":    "JPY",
    "BTC":    "BTC",
}
```

---

## 4. Variáveis de Ambiente (`.env`)

```env
# .env.example — copie para .env e preencha com suas chaves

# Obrigatório: Chave da API do Google Gemini
# Obtenha em: https://aistudio.google.com/app/apikey
GOOGLE_API_KEY=sua_chave_aqui
```

**Regras:**
- `.env` nunca é commitado (incluído no `.gitignore`).
- `.env.example` é commitado como template.
- A aplicação falha com mensagem clara se `GOOGLE_API_KEY` estiver ausente.

---

## 5. Dependências Python (`requirements.txt`)

```txt
# Framework de agentes
google-adk==1.3.0

# LLM (já incluso no google-adk, listado para clareza)
google-generativeai>=0.8.0

# Interface
streamlit==1.40.0

# Dados
pandas==2.2.3

# HTTP (para API de câmbio)
httpx==0.27.2

# Variáveis de ambiente
python-dotenv==1.0.1
```

**Justificativas de versão:**
- `google-adk==1.3.0`: versão estável com suporte a `sub_agents` e `InMemorySessionService`.
- `streamlit==1.40.0`: versão LTS atual com `st.chat_message` e `st.chat_input`.
- `pandas==2.2.3`: leitura/escrita CSV com tipagem correta.
- `httpx==0.27.2`: cliente HTTP assíncrono para a API de câmbio.

---

## 6. Script de Inicialização de Dados (`data/seed.py`)

Script utilitário para criar/recriar os arquivos CSV de dados iniciais. Executado uma vez no setup.

**Responsabilidades:**
- Criar `data/` se não existir.
- Gerar `clientes.csv` com os 5 clientes de seed.
- Gerar `score_limite.csv` com as faixas de limite.
- Criar `solicitacoes_aumento_limite.csv` vazio com apenas o cabeçalho.
- Idempotente: não sobrescreve se `--force` não for passado.

---

## 7. Critérios de Aceitação

- [ ] `data/clientes.csv` existe com os 5 clientes de seed e schema correto.
- [ ] `data/score_limite.csv` existe com as 7 faixas e schema correto.
- [ ] `data/solicitacoes_aumento_limite.csv` é criado automaticamente com cabeçalho na primeira execução.
- [ ] `config.py` carrega `GOOGLE_API_KEY` do ambiente sem hardcode.
- [ ] `requirements.txt` instalável via `pip install -r requirements.txt` sem conflitos.
- [ ] `.env.example` presente no repositório; `.env` no `.gitignore`.
- [ ] `data/seed.py` cria todos os CSVs corretamente e é idempotente.
- [ ] Ausência de `GOOGLE_API_KEY` gera erro descritivo na inicialização do app.
