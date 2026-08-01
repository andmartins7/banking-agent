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
4. Processar a solicitação pendente por timestamp em uma única tool determinística.
5. Deixar a tool decidir o status `'aprovado'` ou `'rejeitado'` sem argumento do modelo.
6. Se aprovado: aplicar em `clientes.csv` exatamente o valor persistido na solicitação.
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
        Chama: processar_solicitacao(data_hora, tool_context)
            │
            ├── ERRO → não comunica rejeição; informa que o processamento não foi concluído
            │
            ├── APROVADO
            │       ├── tool persiste status `aprovado`
            │       ├── tool aplica o valor obtido da própria solicitação
            │       └── agente comunica `novo_limite` retornado
            │
            └── REJEITADO (inclusive sem faixa de cobertura)
                    ├── tool persiste somente status `rejeitado`
                    └── agente comunica a decisão e oferece entrevista
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
3. Ao receber um valor desejado, usar `registrar_solicitacao` e, somente em sucesso,
   chamar `processar_solicitacao` com o timestamp retornado.
4. Comunicar o resultado de forma clara e amigável.
5. Se rejeitado, oferecer ao cliente a opção de entrevista financeira para 
   melhorar o score, transferindo para o especialista se aceito.
6. Se o cliente retornar da entrevista, verificar o novo score e oferecer nova análise.

REGRAS:
- CPF, novo limite, limite atual, score e status nunca são argumentos fornecidos pelo
  modelo a `processar_solicitacao`.
- Se o registro rejeitar o valor, solicite outro valor e não prossiga.
- Se o processamento retornar erro, não comunique o resultado como rejeição.
- O status final e o valor aplicado são decididos exclusivamente pela tool.
- Nunca aprove ou rejeite manualmente.
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

### 5.3 `processar_solicitacao`

**Arquivo:** `tools/credito_tools.py`

```python
def processar_solicitacao(
    data_hora_solicitacao: str,
    tool_context: ToolContext,
) -> dict:
    """
    Decide e aplica deterministicamente uma solicitação pendente.
    
    Args:
        data_hora_solicitacao: Timestamp retornado pelo registro.
        tool_context: Contexto ADK da sessão autenticada.
    
    Returns:
        dict:
            - processado (bool)
            - status_pedido (str | None): `aprovado` ou `rejeitado`
            - limite_atualizado (bool)
            - novo_limite (float | None): somente o valor efetivamente aplicado
            - oferecer_entrevista (bool)
            - erro (str | None)
    """
```

**Lógica:**
1. Autorizar a sessão e localizar exatamente uma solicitação pelo CPF autenticado e timestamp.
2. Exigir status `pendente` no fluxo normal; `aprovado` é aceito somente para a recuperação descrita abaixo.
3. Obter `novo_limite_solicitado` do registro e revalidá-lo antes de qualquer escrita.
4. Consultar `score_limite.csv` e decidir o status sem receber score, faixa ou status do modelo.
5. Em rejeição, publicar atomicamente somente a solicitação e oferecer entrevista.
6. Em aprovação, publicar primeiro a solicitação e depois aplicar exatamente o valor registrado ao cliente.
7. Bloquear sem escrita solicitações ou clientes ausentes/duplicados, dados inválidos, sessão não autorizada e CSVs ausentes/malformados.
8. Não retornar CPF, score, score mínimo, faixa ou critérios da política.

As funções de baixo nível `checar_score_para_limite`, `atualizar_status_solicitacao` e `atualizar_limite_cliente` permanecem no módulo apenas para compatibilidade interna e não são tools do agente.

### 5.4 Garantias de Persistência e Recuperação

- Cada CSV é preparado em um arquivo temporário no mesmo diretório, fechado e publicado individualmente com `os.replace`.
- Falha durante a preparação ou substituição remove o temporário e preserva o destino anterior.
- Na aprovação, a solicitação `aprovado` é publicada antes do novo limite do cliente.
- Se a segunda publicação falhar, a tool tenta restaurar atomicamente os bytes originais da solicitação.
- Se o rollback também falhar, permanece o estado reconhecível `aprovado + limite snapshot`; uma nova chamada aplica atomicamente ao cliente o valor já registrado.
- `aprovado + limite já aplicado` é uma operação concluída e permanece bloqueada contra reprocessamento.
- `aprovado + limite diferente do snapshot e do valor registrado` é bloqueado sem escrita.
- A substituição é atômica por arquivo, mas não constitui transação atômica entre os dois CSVs.
- Locks e controle de concorrência permanecem como débito separado.

---

## 6. Definição ADK do Agente

```python
# agents/credito.py (estrutura)

from google.adk.agents import Agent
from tools.credito_tools import (
    consultar_limite,
    registrar_solicitacao,
    processar_solicitacao,
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
        processar_solicitacao,
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
| Novo limite válido fora da cobertura de `score_limite.csv` | `processar_solicitacao` decide `rejeitado`, atualiza somente a solicitação e oferece entrevista |
| `score_limite.csv` não encontrado | Processamento retorna `erro` sem escrita; agente não comunica rejeição |
| `novo_limite` não é número válido | Agente pede para informar um valor numérico (ex: "5000" ou "5000.00") |
| Snapshot da solicitação diverge do limite do cliente | Processamento bloqueado sem escrita |
| Aprovação interrompida após publicar a solicitação | Nova chamada reconhece `aprovado + limite snapshot` e aplica o valor registrado ao cliente |
| Aprovação já aplicada | Reprocessamento bloqueado sem escrita |
| Falha ao publicar o cliente | Rollback compensatório tenta restaurar a solicitação original |
| Falha do rollback | Estado `aprovado + limite snapshot` permanece recuperável |
| CSV ausente ou malformado | Processamento retorna erro controlado sem escrita |
| Status persistido diferente de `pendente`, `aprovado` ou `rejeitado` | Processamento bloqueado sem escrita |
| Solicitação já finalizada | Reprocessamento rejeitado sem escrita |
| CPF + timestamp duplicados | Erro de integridade sem escrita |
| Cliente ausente ou duplicado | Erro controlado sem escrita |

---

## 8. Critérios de Aceitação

- [ ] Agente informa o limite atual ao ser acionado (ou ao ser solicitado).
- [ ] Valores nulos, booleanos, não numéricos, não finitos ou não positivos não são persistidos.
- [ ] Novo limite igual ou menor que o atual é rejeitado pela tool com mensagem controlada.
- [ ] Solicitação é registrada em CSV com status `'pendente'` antes da checagem.
- [ ] Timestamp do registro usa ISO 8601 UTC com microssegundos e offset explícito.
- [ ] Limite válido fora da cobertura da tabela é finalizado como `'rejeitado'` por `processar_solicitacao`.
- [ ] Score suficiente → tool atualiza status para `'aprovado'` e aplica o valor registrado ao cliente.
- [ ] Score insuficiente → tool atualiza somente o status para `'rejeitado'`.
- [ ] Somente `pendente → aprovado` e `pendente → rejeitado` são permitidas.
- [ ] Solicitação finalizada ou chave duplicada não é reprocessada.
- [ ] Aprovação interrompida em `aprovado + limite snapshot` é recuperada com o valor registrado.
- [ ] Aprovação já aplicada e limite divergente não reconhecido permanecem bloqueados.
- [ ] Cada CSV é substituído atomicamente e não deixa arquivo `.tmp` residual.
- [ ] Falha na segunda publicação tenta rollback compensatório da solicitação.
- [ ] A solução não é descrita como transação atômica entre arquivos e não implementa locks.
- [ ] Snapshot divergente e cliente ausente ou duplicado bloqueiam o processamento sem escrita.
- [ ] Rejeição oferece opção de entrevista de crédito.
- [ ] Aceite de entrevista faz handoff invisível para Agente de Entrevista.
- [ ] Recusa de entrevista encerra ou oferece outras opções sem forçar.
- [ ] Após retorno da entrevista, agente oferece nova análise de limite.
- [ ] Nenhuma aprovação/rejeição manual pelo LLM — a tool decide o status final.
- [ ] CPF, novo limite, limite atual, score e status não são argumentos de `processar_solicitacao`.
- [ ] O agente expõe somente `consultar_limite`, `registrar_solicitacao`, `processar_solicitacao` e `encerrar_atendimento`.
- [ ] Todas as operações CSV são persistidas corretamente.
