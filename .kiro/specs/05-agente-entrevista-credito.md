# Spec 05 — Agente de Entrevista de Crédito

## Status: Draft

---

## 1. Visão Geral

O Agente de Entrevista de Crédito é acionado exclusivamente pelo Agente de Crédito quando uma solicitação de aumento é rejeitada e o cliente aceita realizar a entrevista financeira. Ele conduz uma conversa estruturada para coletar dados financeiros, calcula um novo score usando a fórmula ponderada definida no desafio, persiste o resultado em `clientes.csv` e devolve o controle ao Agente de Crédito para nova análise.

**Arquivo de implementação:** `agents/entrevista_credito.py`  
**Ferramentas usadas:** `tools/score_tools.py`

---

## 2. Responsabilidades

1. Conduzir entrevista conversacional estruturada com 5 perguntas financeiras.
2. Coletar e validar: renda mensal, tipo de emprego, despesas fixas, nº de dependentes e dívidas ativas.
3. Calcular novo score via ferramenta `calcular_score`.
4. Atualizar `score_credito` do cliente em `clientes.csv` via ferramenta `atualizar_score_cliente`.
5. Informar o novo score ao cliente de forma amigável (sem expor a fórmula).
6. Retornar o controle ao Agente de Crédito para que o cliente tente nova solicitação.

---

## 3. Perguntas da Entrevista

As perguntas devem ser feitas **uma de cada vez**, em sequência, aguardando a resposta antes de avançar. O tom deve ser natural e acolhedor, não parecer um formulário frio.

| # | Dado coletado | Pergunta sugerida | Validação |
|---|--------------|-------------------|-----------|
| 1 | `renda_mensal` | "Para começarmos, qual é a sua renda mensal aproximada em reais?" | Número > 0 |
| 2 | `tipo_emprego` | "Qual é a sua situação de emprego atual? (CLT/formal, autônomo ou desempregado)" | Uma das 3 opções |
| 3 | `despesas_fixas` | "Quais são suas despesas fixas mensais aproximadas (aluguel, contas, etc.)?" | Número >= 0 |
| 4 | `num_dependentes` | "Quantas pessoas dependem financeiramente de você?" | Inteiro >= 0 |
| 5 | `tem_dividas` | "Você possui dívidas ativas no momento? (sim ou não)" | "sim" ou "não" |

**Regras de validação:**
- Se a resposta for inválida (ex: texto onde se espera número), o agente pede gentilmente para reformular.
- Máximo de 2 tentativas por pergunta; se ainda inválido, usa valor padrão conservador (documentado abaixo).
- Valores padrão conservadores (fallback):
  - `renda_mensal`: 0 (sem renda declarada)
  - `tipo_emprego`: `"desempregado"`
  - `despesas_fixas`: 0
  - `num_dependentes`: 3 (penaliza mais)
  - `tem_dividas`: `"sim"` (penaliza mais)

---

## 4. Fórmula de Cálculo do Score

Implementada exclusivamente na ferramenta `calcular_score` (não pelo LLM diretamente).

```python
score_raw = (
    (renda_mensal / (despesas_fixas + 1)) * PESO_RENDA
    + PESO_EMPREGO[tipo_emprego]
    + PESO_DEPENDENTES[num_dependentes_key]
    + PESO_DIVIDAS[tem_dividas_key]
)

# Clampar entre 0 e 1000
score_final = max(SCORE_MIN, min(SCORE_MAX, round(score_raw)))
```

**Tabela de pesos (de `config.py`):**

```
PESO_RENDA = 30

PESO_EMPREGO:
  "formal"       → 300
  "autonomo"     → 200
  "desempregado" → 0

PESO_DEPENDENTES:
  0 dependentes  → 100
  1 dependente   → 80
  2 dependentes  → 60
  3+ dependentes → 30

PESO_DIVIDAS:
  "sim"          → -100
  "nao"          → +100
```

**Normalização de `num_dependentes` para a tabela:**
- `0` → chave `0`
- `1` → chave `1`
- `2` → chave `2`
- `3` ou mais → chave `3` (usa `PESO_DEPENDENTES[3]`)

**Normalização de `tipo_emprego` para a tabela:**
- Aceita variações: `"clt"`, `"formal"`, `"empregado"` → `"formal"`
- `"autônomo"`, `"autonomo"`, `"freelancer"`, `"mei"` → `"autonomo"`
- `"desempregado"`, `"desemprego"`, `"sem emprego"` → `"desempregado"`

**Normalização de `tem_dividas`:**
- `"sim"`, `"s"`, `"yes"` → `"sim"`
- `"não"`, `"nao"`, `"n"`, `"no"` → `"nao"`

---

## 5. Exemplos de Cálculo

**Exemplo 1 — Perfil favorável:**
- Renda: R$ 5.000, Formal, Despesas: R$ 2.000, 1 dependente, Sem dívidas
- `score = (5000 / 2001) * 30 + 300 + 80 + 100`
- `score = 74.96 + 300 + 80 + 100 = 554.96` → **555**

**Exemplo 2 — Perfil desfavorável:**
- Renda: R$ 1.500, Autônomo, Despesas: R$ 1.200, 3 dependentes, Com dívidas
- `score = (1500 / 1201) * 30 + 200 + 30 + (-100)`
- `score = 37.47 + 200 + 30 - 100 = 167.47` → **167**

**Exemplo 3 — Desempregado sem dívidas:**
- Renda: R$ 0, Desempregado, Despesas: R$ 500, 0 dependentes, Sem dívidas
- `score = (0 / 501) * 30 + 0 + 100 + 100`
- `score = 0 + 0 + 100 + 100 = 200` → **200**

---

## 6. Fluxo Detalhado

```
[Agente de Entrevista de Crédito recebe controle]
    │
    ▼
"Para melhorar seu perfil de crédito, vou fazer algumas perguntas
 rápidas sobre sua situação financeira. Pode ficar à vontade!"
    │
    ▼
Pergunta 1: Renda mensal → aguarda resposta → valida
    │
    ▼
Pergunta 2: Tipo de emprego → aguarda resposta → valida
    │
    ▼
Pergunta 3: Despesas fixas → aguarda resposta → valida
    │
    ▼
Pergunta 4: Nº de dependentes → aguarda resposta → valida
    │
    ▼
Pergunta 5: Dívidas ativas → aguarda resposta → valida
    │
    ▼
Chama: calcular_score(renda, tipo_emprego, despesas, dependentes, tem_dividas)
    │
    ▼
Chama: atualizar_score_cliente(cpf, novo_score)
    │
    ▼
"Concluímos sua análise financeira! Com base nas informações fornecidas,
 seu perfil foi atualizado. Vou verificar agora se podemos aprovar
 seu aumento de limite."
    │
    ▼
[Retorna controle ao Agente de Crédito]
```

---

## 7. System Prompt do Agente de Entrevista de Crédito

```
Você é o analista financeiro do Banco Ágil. Sua função é conduzir uma 
entrevista amigável para entender melhor o perfil financeiro do cliente
e ajudá-lo a melhorar suas condições de crédito.

INSTRUÇÕES:
1. Faça as perguntas UMA DE CADA VEZ, aguardando a resposta antes de avançar.
2. Seja acolhedor e explique brevemente por que cada informação é importante.
3. Se a resposta for inválida, peça gentilmente para reformular uma vez.
4. Ao coletar todos os dados, use a ferramenta `calcular_score` para calcular o novo score.
5. Use `atualizar_score_cliente` para salvar o novo score.
6. Informe ao cliente que o perfil foi atualizado e que vai verificar o limite.
7. NUNCA mencione valores numéricos do score ao cliente.
8. NUNCA explique a fórmula ou os pesos do cálculo.
9. Mantenha tom empático — o cliente está numa situação de rejeição de crédito.

ORDEM DAS PERGUNTAS (obrigatória):
1. Renda mensal (R$)
2. Situação de emprego (formal/autônomo/desempregado)  
3. Despesas fixas mensais (R$)
4. Número de dependentes financeiros
5. Possui dívidas ativas (sim/não)

Após coletar tudo, calcule e salve o score, depois informe que o perfil
foi atualizado e retorne o contexto para nova análise de crédito.
```

---

## 8. Ferramentas (Tools)

### 8.1 `calcular_score`

**Arquivo:** `tools/score_tools.py`

```python
def calcular_score(
    renda_mensal: float,
    tipo_emprego: str,
    despesas_fixas: float,
    num_dependentes: int,
    tem_dividas: str
) -> dict:
    """
    Calcula o novo score de crédito com base nos dados financeiros coletados.
    
    Args:
        renda_mensal: Renda mensal em R$ (>= 0).
        tipo_emprego: 'formal', 'autonomo' ou 'desempregado' (normalizado internamente).
        despesas_fixas: Despesas fixas mensais em R$ (>= 0).
        num_dependentes: Número de dependentes (>= 0).
        tem_dividas: 'sim' ou 'nao' (normalizado internamente).
    
    Returns:
        dict:
            - score (int): novo score calculado (0-1000)
            - detalhes (dict): componentes do cálculo para debug interno
            - erro (str | None)
    """
```

**Lógica:**
1. Normalizar `tipo_emprego`, `tem_dividas` conforme tabelas da Seção 4.
2. Determinar chave de `num_dependentes` (min(num_dependentes, 3)).
3. Aplicar fórmula.
4. Clampar resultado em `[0, 1000]`.
5. Retornar `score` (int) + `detalhes` dos componentes.

---

### 8.2 `atualizar_score_cliente`

**Arquivo:** `tools/score_tools.py`

```python
def atualizar_score_cliente(cpf: str, novo_score: int) -> dict:
    """
    Atualiza o score de crédito do cliente em clientes.csv.
    
    Args:
        cpf: CPF do cliente (normalizado).
        novo_score: Novo score calculado (0-1000).
    
    Returns:
        dict:
            - atualizado (bool)
            - score_anterior (int): score antes da atualização
            - score_novo (int): score após atualização
            - erro (str | None)
    """
```

**Lógica:**
1. Ler `clientes.csv`.
2. Localizar linha pelo CPF.
3. Guardar `score_anterior` para retorno.
4. Atualizar `score_credito` com `novo_score`.
5. Reescrever CSV.
6. Retornar confirmação com ambos os valores.

---

## 9. Definição ADK do Agente

```python
# agents/entrevista_credito.py (estrutura)

from google.adk.agents import Agent
from tools.score_tools import calcular_score, atualizar_score_cliente
from tools.auth_tools import encerrar_atendimento

agente_entrevista_credito = Agent(
    name="agente_entrevista_credito",
    model="gemini-2.0-flash",
    description="Analista financeiro: conduz entrevista, calcula novo score e atualiza perfil do cliente.",
    instruction=SYSTEM_PROMPT_ENTREVISTA,
    tools=[
        calcular_score,
        atualizar_score_cliente,
        encerrar_atendimento,
    ],
)
```

> Este agente **não tem sub-agentes**. Após concluir, o ADK retorna automaticamente ao agente pai (Agente de Crédito) via mecanismo de handoff do `sub_agents`.

---

## 10. Tratamento de Erros

| Cenário | Comportamento |
|---------|--------------|
| Resposta inválida a uma pergunta (1ª vez) | Agente pede para reformular com dica do formato esperado |
| Resposta ainda inválida (2ª vez) | Usa valor padrão conservador e avança para próxima pergunta |
| `clientes.csv` indisponível na atualização | Ferramenta retorna `erro`; agente informa: "Houve uma instabilidade ao salvar seu perfil. Tente novamente em instantes." |
| Score calculado negativo | Clampado para 0 automaticamente |
| Score calculado > 1000 | Clampado para 1000 automaticamente |
| Cliente deseja encerrar durante entrevista | Agente usa `encerrar_atendimento` e agradece pela participação |

---

## 11. Critérios de Aceitação

- [ ] Agente faz exatamente 5 perguntas, uma por vez, na ordem definida.
- [ ] Resposta inválida gera pedido de reformulação (apenas uma vez por pergunta).
- [ ] Segunda resposta inválida aplica fallback conservador sem bloquear o fluxo.
- [ ] `calcular_score` aplica a fórmula corretamente para os 3 exemplos da Seção 5.
- [ ] Score é sempre um inteiro entre 0 e 1000 inclusive.
- [ ] `atualizar_score_cliente` persiste o novo score em `clientes.csv`.
- [ ] Agente NÃO revela o valor numérico do score ao cliente.
- [ ] Agente NÃO explica a fórmula ou os pesos ao cliente.
- [ ] Após atualização, agente informa que o perfil foi revisado.
- [ ] Controle retorna ao Agente de Crédito para nova análise (handoff ADK).
- [ ] Normalização de tipo de emprego funciona para variações comuns ("clt", "autônomo", "MEI").
- [ ] Normalização de dívidas funciona para variações ("s", "n", "não", "yes").
