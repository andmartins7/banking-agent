# Spec 04 — Agente de Crédito

## Status: Draft

---

## 1. Visão Geral

O Agente de Crédito é acionado após autenticação bem-sucedida pelo Agente de Triagem. Ele lida com dois fluxos: **consulta de limite** (simples, informativo) e **solicitação de aumento de limite** (com checagem de score, registro em CSV e possível handoff para Entrevista de Crédito).

**Arquivo de implementação:** `agents/credito.py`  
**Ferramentas usadas:** `tools/credito_tools.py`

---

## 2. Responsabilidades

1. Consultar e informar o limite de crédito atual do cliente.
2. Receber solicitação de novo limite desejado.
3. Registrar o pedido em `solicitacoes_aumento_limite.csv` com status `'pendente'`.
4. Checar se o score do cliente permite o novo limite via `score_limite.csv`.
5. Atualizar o status do pedido para `'aprovado'` ou `'rejeitado'`.
6. Se aprovado: atualizar `limite_credito` do cliente em `clientes.csv`.
7. Se rejeitado: oferecer redirecionamento para o Agente de Entrevista de Crédito.
8. Receber cliente retornado da Entrevista de Crédito e processar nova análise.

---

## 3. Fluxo Detalhado

### 3.1 Consulta de Limite

```
[Agente de Crédito recebe controle]
    │
    ▼
Chama: consultar_limite(cpf)
    │
    ▼
"Seu limite de crédito atual é de R$ X.XXX,XX."
    │
    ▼
"Deseja solicitar um aumento de limite ou posso ajudá-lo com mais alguma coisa?"
```

### 3.2 Solicitação de Aumento de Limite

```
[Cliente solicita aumento]
    │
    ▼
"Qual o novo limite que deseja solicitar? (R$)"
    │
    ▼
Aguarda valor numérico do cliente
    │
    ▼
Valida: novo_limite > limite_atual
    │                               │
    NÃO                            SIM
    │                               │
"O valor solicitado deve          Chama: registrar_solicitacao(cpf, limite_atual,
ser maior que seu                         novo_limite, status='pendente')
limite atual."                            │
    │                               ▼
    │                   Chama: checar_score_para_limite(score_cliente, novo_limite)
    │                               │
    │                    ┌──────────┴──────────┐
    │                 APROVADO              REJEITADO
    │                    │                    │
    │       Chama:        │       Chama:       │
    │  atualizar_status   │  atualizar_status  │
    │  (solicitacao_id,   │  (solicitacao_id,  │
    │  'aprovado')        │  'rejeitado')      │
    │       │             │       │            │
    │  Chama:             │  "Sua solicitação  │
    │  atualizar_limite   │  não pôde ser      │
    │  (cpf, novo_limite) │  aprovada no       │
    │       │             │  momento devido    │
    │  "Ótima notícia!    │  ao seu score      │
    │  Seu limite foi     │  atual."           │
    │  atualizado para    │       │            │
    │  R$ X.XXX,XX."      │  "Gostaria de     │
    │       │             │  realizar uma      │
    │       │             │  análise de        │
    │       │             │  perfil financeiro │
    │       │             │  para tentar       │
    │       │             │  melhorar          │
    │       │             │  seu score?" (S/N) │
    │       │             │       │            │
    │       │             │  ┌────┴─────┐      │
    │       │             │  SIM       NÃO     │
    │       │             │  │          │      │
    │       │             │  Handoff   Encerra │
    │       │             │  → Agente  ou nova │
    │       │             │  Entrevista opção  │
    └───────┴─────────────┴──────────────────-─┘
```

### 3.3 Retorno da Entrevista de Crédito

Após a entrevista, o Agente de Crédito recebe o cliente de volta com score atualizado. Ele deve:
1. Informar que o score foi recalculado.
2. Perguntar se o cliente deseja tentar nova solicitação de aumento.
3. Se sim: executar o fluxo 3.2 novamente com o novo score.
4. Se não: encerrar ou oferecer outras opções.

---

## 4. System Prompt do Agente de Crédito

```
Você é o especialista em crédito do Banco Ágil. O cliente já foi autenticado.
Você tem acesso ao CPF e dados do cliente via contexto da sessão.

Suas responsabilidades:
1. Consultar e informar o limite de crédito atual quando solicitado.
2. Processar solicitações de aumento de limite.
3. Ao receber um valor desejado, usar a ferramenta `registrar_solicitacao` e depois
   `checar_score_para_limite` para determinar aprovação ou rejeição.
4. Comunicar o resultado de forma clara e amigável.
5. Se rejeitado, oferecer ao cliente a opção de entrevista financeira para 
   melhorar o score, transferindo para o especialista se aceito.
6. Se o cliente retornar da entrevista, verificar o novo score e oferecer nova análise.

REGRAS:
- Sempre consulte o limite atual antes de processar aumento.
- O novo limite solicitado deve ser MAIOR que o limite atual.
- Nunca aprove ou rejeite manualmente — use sempre as ferramentas.
- Não mencione scores, tabelas internas ou nomes de sistemas ao cliente.
- Tom: profissional, claro e empático.
- Se o cliente quiser encerrar, use a ferramenta `encerrar_atendimento`.
```

---

## 5. Ferramentas (Tools)

### 5.1 `consultar_limite`

**Arquivo:** `tools/credito_tools.py`

```python
def consultar_limite(cpf: str) -> dict:
    """
    Consulta o limite de crédito atual do cliente.
    
    Args:
        cpf: CPF do cliente (normalizado, 11 dígitos).
    
    Returns:
        dict:
            - limite_atual (float): limite atual em R$
            - score_credito (int): score atual do cliente
            - erro (str | None): mensagem de erro se houver
    """
```

**Lógica:**
1. Ler `clientes.csv` com pandas.
2. Localizar linha pelo CPF.
3. Retornar `limite_credito` e `score_credito`.
4. Se não encontrar: retornar `erro`.

---

### 5.2 `registrar_solicitacao`

**Arquivo:** `tools/credito_tools.py`

```python
def registrar_solicitacao(
    cpf: str,
    limite_atual: float,
    novo_limite_solicitado: float,
    status_pedido: str = "pendente"
) -> dict:
    """
    Registra uma solicitação de aumento de limite em solicitacoes_aumento_limite.csv.
    
    Args:
        cpf: CPF do cliente.
        limite_atual: Limite atual do cliente.
        novo_limite_solicitado: Novo limite desejado.
        status_pedido: Status inicial ('pendente').
    
    Returns:
        dict:
            - solicitacao_id (str): identificador único da solicitação
              (índice da linha no CSV, formato string)
            - data_hora (str): timestamp ISO 8601 da criação
            - erro (str | None)
    """
```

**Lógica:**
1. Verificar se `solicitacoes_aumento_limite.csv` existe; se não, criar com cabeçalho.
2. Gerar `data_hora = datetime.utcnow().isoformat()`.
3. Fazer append da nova linha com pandas (ou csv.writer).
4. Retornar o identificador (número de linhas - 1 como índice, ou UUID simplificado).

---

### 5.3 `checar_score_para_limite`

**Arquivo:** `tools/credito_tools.py`

```python
def checar_score_para_limite(score_cliente: int, novo_limite: float) -> dict:
    """
    Verifica se o score do cliente permite o novo limite solicitado.
    
    Args:
        score_cliente: Score atual do cliente (0-1000).
        novo_limite: Novo limite de crédito solicitado.
    
    Returns:
        dict:
            - aprovado (bool): True se score suficiente, False caso contrário.
            - score_minimo_necessario (int): score mínimo para esta faixa.
            - faixa_limite (float): limite máximo da faixa encontrada.
            - erro (str | None)
    """
```

**Lógica:**
1. Ler `score_limite.csv` com pandas.
2. Ordenar por `limite_maximo` crescente.
3. Encontrar primeira linha onde `limite_maximo >= novo_limite`.
4. Comparar `score_cliente >= score_minimo`.
5. Retornar `aprovado` + detalhes da faixa.
6. Se `novo_limite` supera todos os `limite_maximo`: `aprovado=False` com `score_minimo_necessario=950`.

---

### 5.4 `atualizar_status_solicitacao`

**Arquivo:** `tools/credito_tools.py`

```python
def atualizar_status_solicitacao(
    cpf: str,
    data_hora_solicitacao: str,
    novo_status: str
) -> dict:
    """
    Atualiza o status de uma solicitação existente em solicitacoes_aumento_limite.csv.
    
    Args:
        cpf: CPF do cliente.
        data_hora_solicitacao: Timestamp ISO 8601 da solicitação (chave de busca).
        novo_status: Novo status ('aprovado' ou 'rejeitado').
    
    Returns:
        dict:
            - atualizado (bool)
            - erro (str | None)
    """
```

**Lógica:**
1. Ler CSV completo.
2. Localizar linha por `cpf_cliente == cpf AND data_hora_solicitacao == data_hora`.
3. Atualizar campo `status_pedido`.
4. Reescrever CSV completo.

---

### 5.5 `atualizar_limite_cliente`

**Arquivo:** `tools/credito_tools.py`

```python
def atualizar_limite_cliente(cpf: str, novo_limite: float) -> dict:
    """
    Atualiza o limite de crédito do cliente em clientes.csv após aprovação.
    
    Args:
        cpf: CPF do cliente.
        novo_limite: Novo limite aprovado.
    
    Returns:
        dict:
            - atualizado (bool)
            - erro (str | None)
    """
```

**Lógica:**
1. Ler `clientes.csv`.
2. Localizar linha pelo CPF.
3. Atualizar coluna `limite_credito`.
4. Reescrever CSV.

---

## 6. Definição ADK do Agente

```python
# agents/credito.py (estrutura)

from google.adk.agents import Agent
from tools.credito_tools import (
    consultar_limite,
    registrar_solicitacao,
    checar_score_para_limite,
    atualizar_status_solicitacao,
    atualizar_limite_cliente,
)
from tools.auth_tools import encerrar_atendimento
from agents.entrevista_credito import agente_entrevista_credito

agente_credito = Agent(
    name="agente_credito",
    model="gemini-2.0-flash",
    description="Especialista em crédito: consulta limites e processa solicitações de aumento.",
    instruction=SYSTEM_PROMPT_CREDITO,
    tools=[
        consultar_limite,
        registrar_solicitacao,
        checar_score_para_limite,
        atualizar_status_solicitacao,
        atualizar_limite_cliente,
        encerrar_atendimento,
    ],
    sub_agents=[agente_entrevista_credito],
)
```

---

## 7. Tratamento de Erros

| Cenário | Comportamento |
|---------|--------------|
| CSV de solicitações não existe | `registrar_solicitacao` cria o arquivo automaticamente |
| Novo limite ≤ limite atual | Agente informa que o valor deve ser maior e solicita novo valor |
| `score_limite.csv` não encontrado | Ferramenta retorna `erro`; agente informa instabilidade temporária |
| `novo_limite` não é número válido | Agente pede para informar um valor numérico (ex: "5000" ou "5000.00") |
| `clientes.csv` corrompido na atualização | Ferramenta retorna `erro`; agente informa e sugere tentar novamente |
| Solicitação já processada (reentrada) | `registrar_solicitacao` sempre cria novo registro (histórico mantido) |

---

## 8. Critérios de Aceitação

- [ ] Agente informa o limite atual ao ser acionado (ou ao ser solicitado).
- [ ] Novo limite igual ou menor que atual é rejeitado com mensagem clara.
- [ ] Solicitação é registrada em CSV com status `'pendente'` antes da checagem.
- [ ] Score suficiente → status atualizado para `'aprovado'` + `clientes.csv` atualizado.
- [ ] Score insuficiente → status atualizado para `'rejeitado'`.
- [ ] Rejeição oferece opção de entrevista de crédito.
- [ ] Aceite de entrevista faz handoff invisível para Agente de Entrevista.
- [ ] Recusa de entrevista encerra ou oferece outras opções sem forçar.
- [ ] Após retorno da entrevista, agente oferece nova análise de limite.
- [ ] Nenhuma aprovação/rejeição manual pelo LLM — sempre via ferramentas.
- [ ] Todas as operações CSV são persistidas corretamente.
