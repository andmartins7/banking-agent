# Spec 07 — Orquestrador e Sistema de Ferramentas

## Status: Draft

---

## 1. Visão Geral

Esta spec cobre dois aspectos interdependentes:

1. **Orquestrador** (`orchestrator.py`): como o Google ADK é configurado para conectar os agentes, gerenciar sessão e processar o loop de mensagens.
2. **Sistema de ferramentas** (`tools/`): contrato completo de todas as funções-ferramenta expostas aos agentes, regras de implementação e convenções compartilhadas.

---

## 2. Hierarquia de Agentes no ADK

```
Runner (ADK)
└── agente_triagem  [RAIZ]
    ├── tools: [autenticar_cliente, encerrar_atendimento]
    └── sub_agents:
        ├── agente_credito
        │   ├── tools: [consultar_limite, registrar_solicitacao,
        │   │           checar_score_para_limite, atualizar_status_solicitacao,
        │   │           atualizar_limite_cliente, encerrar_atendimento]
        │   └── sub_agents:
        │       └── agente_entrevista_credito
        │           └── tools: [calcular_score, atualizar_score_cliente,
        │                       encerrar_atendimento]
        └── agente_cambio
            └── tools: [buscar_cotacao, encerrar_atendimento]
```

**Mecanismo de handoff ADK:**
- O ADK usa `transfer_to_agent` implicitamente quando o agente pai decide delegar.
- O agente filho tem acesso ao histórico completo da conversa via `session`.
- Quando o agente filho termina sua tarefa, o controle retorna ao pai automaticamente (exceto quando `encerrar_atendimento` é chamado).

---

## 3. Orquestrador (`orchestrator.py`)

### 3.1 Responsabilidades

- Inicializar o `Runner` do ADK com o `agente_triagem` como raiz.
- Configurar o `InMemorySessionService` para manter estado por sessão.
- Expor a função `processar_mensagem(session_id, mensagem_usuario)` para o Streamlit.
- Gerenciar o ciclo de vida da sessão (criar, usar, detectar encerramento).

### 3.2 Estrutura do `orchestrator.py`

```python
# orchestrator.py

import os
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.triagem import agente_triagem
from config import GOOGLE_API_KEY, GEMINI_MODEL

load_dotenv()

# ── Validação de ambiente ──────────────────────────────────────────────────
if not GOOGLE_API_KEY:
    raise EnvironmentError(
        "GOOGLE_API_KEY não encontrada. "
        "Crie um arquivo .env com GOOGLE_API_KEY=sua_chave. "
        "Veja .env.example para referência."
    )

os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY

# ── Serviço de sessão ──────────────────────────────────────────────────────
session_service = InMemorySessionService()

APP_NAME = "banco_agil"

# ── Runner ─────────────────────────────────────────────────────────────────
runner = Runner(
    agent=agente_triagem,
    app_name=APP_NAME,
    session_service=session_service,
)


def criar_sessao(session_id: str) -> None:
    """Cria uma nova sessão para o cliente."""
    session_service.create_session(
        app_name=APP_NAME,
        user_id=session_id,
        session_id=session_id,
    )


def processar_mensagem(session_id: str, mensagem_usuario: str) -> str:
    """
    Envia uma mensagem do usuário ao agente e retorna a resposta em texto.
    
    Args:
        session_id: Identificador único da sessão (gerado pelo Streamlit).
        mensagem_usuario: Texto digitado pelo usuário.
    
    Returns:
        str: Resposta do agente para exibição na UI.
    
    Raises:
        Exception: Erros de runtime são capturados e retornam mensagem amigável.
    """
    content = Content(role="user", parts=[Part(text=mensagem_usuario)])
    
    resposta_final = ""
    try:
        for event in runner.run(
            user_id=session_id,
            session_id=session_id,
            new_message=content,
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    resposta_final = event.content.parts[0].text
                break
    except Exception as e:
        resposta_final = (
            "Ocorreu um erro inesperado no sistema. "
            "Por favor, tente novamente ou entre em contato com o suporte."
        )
        # Log técnico (não exibido ao cliente)
        print(f"[ERRO] session={session_id} | {type(e).__name__}: {e}")
    
    return resposta_final


def sessao_encerrada(session_id: str) -> bool:
    """
    Verifica se a flag de encerramento foi ativada na sessão.
    Usada pelo Streamlit para desabilitar o input após encerramento.
    """
    try:
        session = session_service.get_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
        return session.state.get("conversation_ended", False)
    except Exception:
        return False
```

---

## 4. Convenções Gerais das Ferramentas

Todas as ferramentas (`tools/*.py`) seguem estas convenções:

### 4.1 Assinatura e tipagem
- Todos os parâmetros são tipados com type hints Python.
- Todas as funções retornam `dict` com campos claramente definidos.
- Sempre inclui campo `erro: str | None` para reportar falhas sem exceções.

### 4.2 Padrão de retorno
```python
# Sucesso
{"campo_resultado": valor, ..., "erro": None}

# Falha controlada
{"campo_resultado": valor_padrão, ..., "erro": "Descrição legível do problema"}
```

### 4.3 Exceções
- Ferramentas **nunca propagam exceções** ao agente.
- Todo `try/except` interno retorna `{"erro": "mensagem"}` ao invés de lançar.
- Erros técnicos são logados em `print(f"[TOOL ERROR] ...")` para diagnóstico.

### 4.4 Operações CSV
- Toda leitura/escrita usa `pandas` com `dtype=str` na leitura (evita coerção).
- Colunas numéricas convertidas explicitamente após leitura.
- Escrita sempre com `index=False`.
- Operações de atualização: ler → modificar em memória → reescrever completo.
- Append: usar `mode='a'`, `header=False` se arquivo existe.

### 4.5 Thread safety
- Sem lock explícito no MVP (sessão única por vez via Streamlit).
- Documentado como limitação conhecida para escala futura.

---

## 5. Mapa Completo de Ferramentas

### `tools/auth_tools.py`

| Função | Agentes que usam | Descrição resumida |
|--------|-----------------|-------------------|
| `autenticar_cliente(cpf, data_nascimento)` | Triagem | Autentica contra clientes.csv |
| `encerrar_atendimento()` | Todos | Sinaliza fim de sessão |

### `tools/credito_tools.py`

| Função | Agentes que usam | Descrição resumida |
|--------|-----------------|-------------------|
| `consultar_limite(cpf)` | Crédito | Retorna limite e score atuais |
| `registrar_solicitacao(cpf, limite_atual, novo_limite, status)` | Crédito | Append em solicitacoes CSV |
| `checar_score_para_limite(score_cliente, novo_limite)` | Crédito | Verifica aprovação via score_limite.csv |
| `atualizar_status_solicitacao(cpf, data_hora, novo_status)` | Crédito | Atualiza status no CSV |
| `atualizar_limite_cliente(cpf, novo_limite)` | Crédito | Atualiza limite em clientes.csv |

### `tools/score_tools.py`

| Função | Agentes que usam | Descrição resumida |
|--------|-----------------|-------------------|
| `calcular_score(renda, tipo_emprego, despesas, dependentes, tem_dividas)` | Entrevista | Aplica fórmula ponderada |
| `atualizar_score_cliente(cpf, novo_score)` | Entrevista | Persiste score em clientes.csv |

### `tools/cambio_tools.py`

| Função | Agentes que usam | Descrição resumida |
|--------|-----------------|-------------------|
| `buscar_cotacao(codigo_moeda)` | Câmbio | Consulta AwesomeAPI e retorna cotação estruturada |

---

## 6. Detalhamento: `tools/auth_tools.py`

```python
# tools/auth_tools.py

import pandas as pd
from config import CSV_CLIENTES


def autenticar_cliente(cpf: str, data_nascimento: str) -> dict:
    """
    Autentica um cliente verificando CPF e data de nascimento.
    Retorna dados do cliente se autenticado, ou erro/falha caso contrário.
    """
    try:
        # Normalizar CPF
        cpf_normalizado = "".join(filter(str.isdigit, cpf))
        if len(cpf_normalizado) != 11:
            return {
                "autenticado": False,
                "cliente": None,
                "erro": None,
                "motivo": "CPF deve conter 11 dígitos numéricos."
            }

        # Ler base de clientes
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()

        # Buscar cliente
        cliente_row = df[df["cpf"].str.strip() == cpf_normalizado]
        if cliente_row.empty:
            return {"autenticado": False, "cliente": None, "erro": None}

        # Validar data de nascimento
        data_base = cliente_row.iloc[0]["data_nascimento"].strip()
        data_input = data_nascimento.strip()

        if data_base != data_input:
            return {"autenticado": False, "cliente": None, "erro": None}

        # Autenticado
        row = cliente_row.iloc[0]
        return {
            "autenticado": True,
            "cliente": {
                "cpf": row["cpf"].strip(),
                "nome": row["nome"].strip(),
                "score_credito": int(row["score_credito"]),
                "limite_credito": float(row["limite_credito"]),
            },
            "erro": None,
        }

    except FileNotFoundError:
        return {
            "autenticado": False,
            "cliente": None,
            "erro": "Base de clientes não encontrada. Contate o suporte.",
        }
    except Exception as e:
        print(f"[TOOL ERROR] autenticar_cliente: {e}")
        return {
            "autenticado": False,
            "cliente": None,
            "erro": "Erro interno na autenticação. Tente novamente.",
        }


def encerrar_atendimento() -> dict:
    """
    Sinaliza o encerramento do atendimento.
    O Streamlit usa este sinal para desabilitar o input.
    """
    return {
        "encerrado": True,
        "mensagem": "Atendimento encerrado com sucesso.",
    }
```

---

## 7. Detalhamento: `tools/credito_tools.py`

```python
# tools/credito_tools.py — estrutura de cada função

# consultar_limite: lê clientes.csv → retorna limite_credito + score_credito
# registrar_solicitacao: cria/append solicitacoes CSV → retorna data_hora gerada
# checar_score_para_limite: lê score_limite.csv ordenado → compara score vs faixa
# atualizar_status_solicitacao: lê CSV → localiza por cpf+data_hora → reescreve
# atualizar_limite_cliente: lê clientes.csv → atualiza limite_credito → reescreve

# Todas seguem: try/except → retornam {"erro": msg} em falhas
# Todas usam pd.read_csv(path, dtype=str) + conversão explícita de numéricos
```

> Implementação completa na Spec de implementação (tarefas de código).

---

## 8. Detalhamento: `tools/score_tools.py`

```python
# tools/score_tools.py — estrutura

# calcular_score:
#   - Normaliza tipo_emprego → chave do dicionário PESO_EMPREGO
#   - Normaliza tem_dividas → 'sim'/'nao'
#   - num_dependentes → min(n, 3) para lookup
#   - Aplica fórmula: (renda/(despesas+1))*PESO_RENDA + peso_emp + peso_dep + peso_div
#   - max(0, min(1000, round(score_raw)))
#   - Retorna {"score": int, "detalhes": {...}, "erro": None}

# atualizar_score_cliente:
#   - Lê clientes.csv
#   - Localiza por cpf
#   - Guarda score_anterior
#   - Atualiza score_credito
#   - Reescreve CSV
#   - Retorna {"atualizado": True, "score_anterior": int, "score_novo": int}
```

---

## 9. Detalhamento: `tools/cambio_tools.py`

```python
# tools/cambio_tools.py — estrutura

import httpx
from config import CAMBIO_API_BASE_URL, MOEDAS_SUPORTADAS

# buscar_cotacao:
#   - Normaliza codigo_moeda para MAIÚSCULAS
#   - Verifica se está em MOEDAS_SUPORTADAS
#   - GET {CAMBIO_API_BASE_URL}/{codigo}-BRL (timeout=5.0)
#   - Parseia JSON → extrai bid, ask, pctChange, name, create_date
#   - Formata create_date para "DD/MM/AAAA HH:MM"
#   - Retorna dict estruturado ou {"sucesso": False, "erro": msg}

# Timeout: httpx.get(url, timeout=5.0)
# Tratamento: Timeout, HTTPStatusError, JSONDecodeError, KeyError
```

---

## 10. `config.py` — Detalhamento Final

```python
# config.py — versão completa com todos os campos necessários

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# Arquivos CSV
CSV_CLIENTES         = DATA_DIR / "clientes.csv"
CSV_SCORE_LIMITE     = DATA_DIR / "score_limite.csv"
CSV_SOLICITACOES     = DATA_DIR / "solicitacoes_aumento_limite.csv"

# LLM
GEMINI_MODEL         = "gemini-2.0-flash"
GOOGLE_API_KEY       = os.getenv("GOOGLE_API_KEY", "")

# Autenticação
MAX_AUTH_ATTEMPTS    = 3

# Pesos do score
PESO_RENDA           = 30
PESO_EMPREGO         = {"formal": 300, "autonomo": 200, "desempregado": 0}
PESO_DEPENDENTES     = {0: 100, 1: 80, 2: 60, 3: 30}
PESO_DIVIDAS         = {"sim": -100, "nao": 100}
SCORE_MIN            = 0
SCORE_MAX            = 1000

# Câmbio
CAMBIO_API_BASE_URL  = "https://economia.awesomeapi.com.br/last"
MOEDAS_SUPORTADAS    = {
    "USD": "USD", "EUR": "EUR", "GBP": "GBP", "JPY": "JPY",
    "BTC": "BTC", "CAD": "CAD", "AUD": "AUD", "CHF": "CHF", "ARS": "ARS",
}
```

---

## 11. `data/seed.py` — Script de Dados Iniciais

```python
# data/seed.py

import pandas as pd
from pathlib import Path
import sys

DATA_DIR = Path(__file__).parent

def criar_clientes():
    path = DATA_DIR / "clientes.csv"
    dados = {
        "cpf":            ["12345678901","98765432100","11122233344","55566677788","99988877766"],
        "nome":           ["João Silva","Maria Oliveira","Carlos Mendes","Ana Souza","Pedro Costa"],
        "data_nascimento":["15/03/1985","22/07/1990","08/11/1978","30/01/1995","14/06/1982"],
        "score_credito":  [750, 420, 600, 850, 300],
        "limite_credito": [5000.00, 1500.00, 3000.00, 10000.00, 500.00],
    }
    pd.DataFrame(dados).to_csv(path, index=False)
    print(f"[seed] {path} criado com {len(dados['cpf'])} clientes.")

def criar_score_limite():
    path = DATA_DIR / "score_limite.csv"
    dados = {
        "limite_maximo": [1000.00, 2000.00, 5000.00, 10000.00, 20000.00, 50000.00, 999999.99],
        "score_minimo":  [200,      300,      450,     600,      750,      850,      950],
    }
    pd.DataFrame(dados).to_csv(path, index=False)
    print(f"[seed] {path} criado com {len(dados['limite_maximo'])} faixas.")

def criar_solicitacoes():
    path = DATA_DIR / "solicitacoes_aumento_limite.csv"
    colunas = ["cpf_cliente","data_hora_solicitacao","limite_atual","novo_limite_solicitado","status_pedido"]
    pd.DataFrame(columns=colunas).to_csv(path, index=False)
    print(f"[seed] {path} criado (vazio, apenas cabeçalho).")

if __name__ == "__main__":
    force = "--force" in sys.argv
    for fn, name in [(criar_clientes, "clientes.csv"),
                     (criar_score_limite, "score_limite.csv"),
                     (criar_solicitacoes, "solicitacoes_aumento_limite.csv")]:
        p = DATA_DIR / name
        if p.exists() and not force:
            print(f"[seed] {name} já existe. Use --force para recriar.")
        else:
            fn()
```

---

## 12. Critérios de Aceitação

- [ ] `orchestrator.py` inicializa sem erros com `GOOGLE_API_KEY` válida.
- [ ] `processar_mensagem()` retorna string de resposta do agente.
- [ ] `processar_mensagem()` captura exceções e retorna mensagem amigável (nunca propaga).
- [ ] `sessao_encerrada()` retorna `True` após `encerrar_atendimento` ser chamado.
- [ ] Hierarquia ADK: `agente_triagem` → `agente_credito` → `agente_entrevista_credito` configurada corretamente.
- [ ] `agente_cambio` como sub-agente direto de `agente_triagem`.
- [ ] Todas as ferramentas retornam `dict` com campo `erro`.
- [ ] Nenhuma ferramenta propaga exceção ao agente.
- [ ] Leitura de CSV usa `dtype=str` + conversão explícita de campos numéricos.
- [ ] `data/seed.py --force` recria todos os CSVs; sem `--force` não sobrescreve.
- [ ] Ausência de `GOOGLE_API_KEY` lança `EnvironmentError` com mensagem clara.
- [ ] `encerrar_atendimento` retorna `{"encerrado": True}` — sem efeitos colaterais.
