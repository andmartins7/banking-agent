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
Aguarda o valor desejado do cliente
    │
    ▼
Chama: registrar_solicitacao(novo_limite, tool_context)
    │
    ├── ERRO → solicita novo valor; nenhuma linha é persistida
    │
    └── REGISTRADO (`pendente`, com `data_hora`)
            │
            ▼
        Chama: checar_score_para_limite(novo_limite, tool_context)
            │
            ├── ERRO → não atualiza status; informa que a análise não foi concluída
            │
            ├── APROVADO
            │       ├── atualizar_status_solicitacao(data_hora, "aprovado", tool_context)
            │       ├── atualizar_limite_cliente(novo_limite, tool_context)
            │       └── comunica o novo limite
            │
            └── REJEITADO (`erro=None`, inclusive sem faixa de cobertura)
                    ├── atualizar_status_solicitacao(data_hora, "rejeitado", tool_context)
                    └── comunica a decisão sem expor score ou faixa e oferece entrevista
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
As ferramentas identificam o cliente exclusivamente pelo estado autenticado da sessão.

Suas responsabilidades:
1. Consultar e informar o limite de crédito atual quando solicitado.
2. Processar solicitações de aumento de limite.
3. Ao receber um valor desejado, usar `registrar_solicitacao` como autoridade da
   validação e, somente em sucesso, usar `checar_score_para_limite`.
4. Comunicar o resultado de forma clara e amigável.
5. Se rejeitado, oferecer ao cliente a opção de entrevista financeira para 
   melhorar o score, transferindo para o especialista se aceito.
6. Se o cliente retornar da entrevista, verificar o novo score e oferecer nova análise.

REGRAS:
- CPF, limite atual e score atual nunca são argumentos fornecidos pelo modelo.
- Se o registro rejeitar o valor, solicite outro valor e não prossiga.
- Se a análise retornar erro, não atualize o status.
- Use somente os status finais `aprovado` e `rejeitado`.
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
def consultar_limite(tool_context: ToolContext) -> dict:
    """
    Consulta o limite de crédito atual do cliente.
    
    Returns:
        dict:
            - limite_atual (float): limite atual em R$
            - score_credito (int): score atual do cliente
            - erro (str | None): mensagem de erro se houver
    """
```

**Lógica:**
1. Autorizar a sessão e obter o CPF persistido pelo `ToolContext`.
2. Ler `clientes.csv` e localizar a linha do cliente autenticado.
3. Retornar `limite_credito` e `score_credito`.
4. Se não encontrar: retornar `erro`.

---

### 5.2 `registrar_solicitacao`

**Arquivo:** `tools/credito_tools.py`

```python
def registrar_solicitacao(
    novo_limite_solicitado: float,
    tool_context: ToolContext,
) -> dict:
    """
    Registra uma solicitação de aumento de limite em solicitacoes_aumento_limite.csv.
    
    Args:
        novo_limite_solicitado: Novo limite desejado.
        tool_context: Contexto ADK da sessão autenticada.
    
    Returns:
        dict:
            - registrado (bool): indica se a linha foi persistida
            - data_hora (str): timestamp ISO 8601 da criação
            - limite_atual (float): valor obtido de `clientes.csv`
            - novo_limite_solicitado (float): valor validado
            - status_pedido (str): sempre `pendente` no registro
            - erro (str | None)
    """
```

**Lógica:**
1. Autorizar a sessão, ler o cliente e obter o limite atual persistido.
2. Validar deterministicamente: valor numérico finito, positivo e estritamente maior que o limite atual.
3. Em falha, retornar `registrado=False` antes de criar ou modificar o CSV de solicitações.
4. Em sucesso, criar o CSV se necessário e persistir a linha com status `pendente`.
5. Gerar `data_hora` em UTC ISO 8601, com microssegundos e offset `+00:00`.

---

### 5.3 `checar_score_para_limite`

**Arquivo:** `tools/credito_tools.py`

```python
def checar_score_para_limite(
    novo_limite: float,
    tool_context: ToolContext,
) -> dict:
    """
    Verifica se o score do cliente permite o novo limite solicitado.
    
    Args:
        novo_limite: Novo limite de crédito solicitado.
        tool_context: Contexto ADK da sessão autenticada.
    
    Returns:
        dict:
            - aprovado (bool): True se score suficiente, False caso contrário.
            - score_minimo_necessario (int | None): score mínimo da faixa.
            - limite_maximo_faixa (float | None): cobertura da faixa encontrada.
            - limite_coberto (bool): indica se a tabela cobre o valor.
            - erro (str | None)
    """
```

**Lógica:**
1. Autorizar a sessão e obter limite atual e score persistidos em `clientes.csv`.
2. Revalidar o novo limite com a mesma regra determinística do registro.
3. Ordenar `score_limite.csv` por `limite_maximo` crescente e usar a primeira faixa que cobre o valor.
4. Comparar o score persistido com `score_minimo`, sem expor o score ao modelo.
5. Se nenhuma faixa cobrir um limite válido, retornar `aprovado=False`, `limite_coberto=False` e `erro=None`; essa rejeição de negócio permite finalizar a solicitação como `rejeitado` e nunca reutiliza automaticamente a última faixa.
6. Representar separadamente falhas técnicas da tabela com `erro != None`; nesses casos, não finalizar a solicitação.

---

### 5.4 `atualizar_status_solicitacao`

**Arquivo:** `tools/credito_tools.py`

```python
def atualizar_status_solicitacao(
    data_hora_solicitacao: str,
    novo_status: str,
    tool_context: ToolContext,
) -> dict:
    """
    Atualiza o status de uma solicitação existente em solicitacoes_aumento_limite.csv.
    
    Args:
        data_hora_solicitacao: Timestamp ISO 8601 da solicitação (chave de busca).
        novo_status: Novo status ('aprovado' ou 'rejeitado').
        tool_context: Contexto ADK da sessão autenticada.
    
    Returns:
        dict:
            - atualizado (bool)
            - status_anterior (str | None)
            - status_novo (str | None)
            - erro (str | None)
    """
```

**Lógica:**
1. Aceitar como destino somente `aprovado` ou `rejeitado`; `reprovado` é apenas sinônimo textual de entrada e normaliza para `rejeitado`.
2. Localizar exatamente uma linha pelo CPF da sessão e pelo timestamp.
3. Permitir somente `pendente → aprovado` ou `pendente → rejeitado`.
4. Rejeitar destino `pendente`, status arbitrário, duplicidade e reprocessamento de solicitação finalizada.
5. Validar tudo antes de reescrever o CSV.

---

### 5.5 `atualizar_limite_cliente`

**Arquivo:** `tools/credito_tools.py`

```python
def atualizar_limite_cliente(
    novo_limite: float,
    tool_context: ToolContext,
) -> dict:
    """
    Atualiza o limite de crédito do cliente em clientes.csv após aprovação.
    
    Args:
        novo_limite: Novo limite aprovado.
        tool_context: Contexto ADK da sessão autenticada.
    
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
| CSV de solicitações não existe | `registrar_solicitacao` cria o arquivo somente após validar o valor |
| Novo limite nulo, booleano, não numérico, não finito ou ≤ 0 | Ferramenta retorna `registrado=False` sem criar ou modificar o CSV |
| Novo limite ≤ limite atual | Ferramenta rejeita deterministicamente e o agente solicita novo valor |
| Novo limite válido fora da cobertura de `score_limite.csv` | Rejeição de negócio com `aprovado=False`, `limite_coberto=False` e `erro=None`; status transita de `pendente` para `rejeitado` |
| `score_limite.csv` não encontrado | Ferramenta retorna `erro`; agente informa instabilidade temporária |
| `novo_limite` não é número válido | Agente pede para informar um valor numérico (ex: "5000" ou "5000.00") |
| `clientes.csv` corrompido na atualização | Ferramenta retorna `erro`; agente informa e sugere tentar novamente |
| Status de entrada `reprovado` | Normalizado e persistido como `rejeitado` |
| Solicitação já finalizada | Reprocessamento rejeitado sem escrita |
| CPF + timestamp duplicados | Erro de integridade sem escrita |

---

## 8. Critérios de Aceitação

- [ ] Agente informa o limite atual ao ser acionado (ou ao ser solicitado).
- [ ] Valores nulos, booleanos, não numéricos, não finitos ou não positivos não são persistidos.
- [ ] Novo limite igual ou menor que o atual é rejeitado pela tool com mensagem controlada.
- [ ] Solicitação é registrada em CSV com status `'pendente'` antes da checagem.
- [ ] Timestamp do registro usa ISO 8601 UTC com microssegundos e offset explícito.
- [ ] Limite válido fora da cobertura da tabela é rejeitado sem erro técnico, finaliza o status como `'rejeitado'` e não usa a última faixa como fallback.
- [ ] Score suficiente → status atualizado para `'aprovado'` + `clientes.csv` atualizado.
- [ ] Score insuficiente → status atualizado para `'rejeitado'`.
- [ ] Somente `pendente → aprovado` e `pendente → rejeitado` são permitidas.
- [ ] `reprovado` é aceito somente como sinônimo de entrada e persiste como `rejeitado`.
- [ ] Solicitação finalizada ou chave duplicada não é reprocessada.
- [ ] Rejeição oferece opção de entrevista de crédito.
- [ ] Aceite de entrevista faz handoff invisível para Agente de Entrevista.
- [ ] Recusa de entrevista encerra ou oferece outras opções sem forçar.
- [ ] Após retorno da entrevista, agente oferece nova análise de limite.
- [ ] Nenhuma aprovação/rejeição manual pelo LLM — sempre via ferramentas.
- [ ] CPF, limite atual e score atual nunca são argumentos fornecidos pelo LLM.
- [ ] Todas as operações CSV são persistidas corretamente.
