# Spec 03 — Agente de Triagem

## Status: Draft

---

## 1. Visão Geral

O Agente de Triagem é a **porta de entrada obrigatória** do sistema. Nenhum outro agente é acessível sem autenticação bem-sucedida. Ele recebe o cliente, coleta CPF e data de nascimento, autentica contra `clientes.csv`, e — após autenticação — identifica a necessidade e transfere o controle para o agente especialista adequado de forma transparente.

**Arquivo de implementação:** `agents/triagem.py`  
**Ferramentas usadas:** `tools/auth_tools.py`

---

## 2. Responsabilidades

1. Saudação inicial ao cliente.
2. Coleta sequencial de CPF e data de nascimento.
3. Autenticação via ferramenta `autenticar_cliente`.
4. Controle de tentativas (máx. 3 consecutivas).
5. Identificação de intenção após autenticação.
6. Handoff para agente especialista (sub-agente ADK).

---

## 3. Fluxo Detalhado

### 3.1 Fluxo de Autenticação

```
[INÍCIO]
    │
    ▼
Saudação: "Olá! Bem-vindo ao Banco Ágil. Para começar, por favor informe seu CPF."
    │
    ▼
Aguarda CPF do cliente
    │
    ▼
"Obrigado! Agora informe sua data de nascimento (DD/MM/AAAA)."
    │
    ▼
Aguarda data de nascimento
    │
    ▼
Chama ferramenta: autenticar_cliente(cpf, data_nascimento)
    │
    ├── SUCESSO ──────────────────────────────────────────────────────────────┐
    │   "Identidade confirmada! Como posso ajudá-lo hoje, [Nome]?"            │
    │   → Aguarda intenção do cliente                                         │
    │   → Identifica tópico (crédito / câmbio / encerrar)                    │
    │   → Handoff para agente especialista                                    │
    │                                                                         │
    └── FALHA                                                                 │
        │                                                                     │
        ├── tentativas < 3:                                                   │
        │   "Não consegui confirmar seus dados. Verifique e tente novamente." │
        │   → Reinicia coleta de CPF + data de nascimento                    │
        │   → Incrementa contador de tentativas                              │
        │                                                                     │
        └── tentativas == 3:                                                  │
            "Infelizmente não foi possível confirmar sua identidade após      │
             três tentativas. Por segurança, encerramos o atendimento.        │
             Se precisar de ajuda, entre em contato pelo nosso canal          │
             oficial. Até logo!"                                              │
            → Chama ferramenta: encerrar_atendimento()                       │
                                                                             │
[PÓS-AUTENTICAÇÃO] ◄─────────────────────────────────────────────────────────┘
```

### 3.2 Mapeamento de Intenção → Agente

| Palavras-chave detectadas | Agente destino |
|--------------------------|----------------|
| "limite", "crédito", "cartão", "aumento" | Agente de Crédito |
| "câmbio", "dólar", "euro", "cotação", "moeda" | Agente de Câmbio |
| "encerrar", "sair", "tchau", "obrigado, foi só isso" | `encerrar_atendimento()` |

> O LLM (Gemini) é responsável pela classificação de intenção usando o contexto da conversa. O mapeamento acima é orientativo para o system prompt.

---

## 4. System Prompt do Agente de Triagem

```
Você é o assistente de atendimento do Banco Ágil. Seu papel é:

1. Recepcionar o cliente com uma saudação cordial.
2. Solicitar o CPF (somente números, sem pontos ou traços).
3. Solicitar a data de nascimento no formato DD/MM/AAAA.
4. Chamar a ferramenta `autenticar_cliente` com os dados coletados.
5. Se a autenticação falhar, informar o cliente de forma educada e solicitar
   novamente os dados. Você tem no máximo 3 tentativas totais.
6. Após a terceira falha, usar a ferramenta `encerrar_atendimento` e se despedir.
7. Se autenticado, cumprimentar o cliente pelo nome e perguntar como pode ajudá-lo.
8. Identificar se o cliente quer tratar de: crédito/limite, câmbio/cotação, ou encerrar.
9. Transferir naturalmente para o especialista sem mencionar nomes de sistemas ou agentes.
10. Manter tom respeitoso, objetivo e claro em todas as mensagens.

IMPORTANTE:
- Nunca mencione "agente", "sistema", "transferência" ou termos técnicos ao cliente.
- Nunca forneça informações de conta sem autenticação prévia.
- Se o cliente perguntar algo fora do escopo (crédito/câmbio), informe gentilmente
  que este canal atende apenas esses serviços.
```

---

## 5. Ferramentas (Tools)

### 5.1 `autenticar_cliente`

**Arquivo:** `tools/auth_tools.py`

**Assinatura ADK:**
```python
def autenticar_cliente(cpf: str, data_nascimento: str) -> dict:
    """
    Autentica um cliente verificando CPF e data de nascimento na base clientes.csv.
    
    Args:
        cpf: CPF do cliente (aceita com ou sem formatação; normalizado internamente).
        data_nascimento: Data de nascimento no formato DD/MM/AAAA.
    
    Returns:
        dict com campos:
            - autenticado (bool): True se dados conferem, False caso contrário.
            - cliente (dict | None): dados do cliente se autenticado
                {cpf, nome, score_credito, limite_credito}, ou None.
            - erro (str | None): mensagem de erro técnico se houver.
    """
```

**Lógica interna:**
1. Normalizar CPF: remover `.` e `-`, manter apenas dígitos.
2. Validar formato: CPF deve ter 11 dígitos; data deve ser `DD/MM/AAAA`.
3. Ler `clientes.csv` com pandas.
4. Buscar linha onde `cpf == cpf_normalizado`.
5. Se encontrado, comparar `data_nascimento` (case-insensitive, strip whitespace).
6. Retornar `autenticado=True` + dados do cliente, ou `autenticado=False`.
7. Em caso de erro de leitura do CSV: retornar `erro` com mensagem descritiva.

**Normalização de CPF aceita:**
- `"123.456.789-01"` → `"12345678901"`
- `"123 456 789 01"` → `"12345678901"`
- `"12345678901"` → `"12345678901"`

### 5.2 `encerrar_atendimento`

**Arquivo:** `tools/auth_tools.py`

**Assinatura ADK:**
```python
def encerrar_atendimento() -> dict:
    """
    Sinaliza o encerramento do atendimento para o loop de execução.
    
    Returns:
        dict: {"encerrado": True, "mensagem": "Atendimento encerrado."}
    """
```

**Lógica interna:**
- Atualizar flag `conversation_ended = True` na sessão ADK.
- Retornar confirmação para o agente exibir mensagem de despedida.

---

## 6. Definição ADK do Agente

```python
# agents/triagem.py (estrutura)

from google.adk.agents import Agent
from tools.auth_tools import autenticar_cliente, encerrar_atendimento
from agents.credito import agente_credito
from agents.cambio import agente_cambio

agente_triagem = Agent(
    name="agente_triagem",
    model="gemini-2.0-flash",
    description="Agente de entrada do Banco Ágil: autentica clientes e direciona para o serviço adequado.",
    instruction=SYSTEM_PROMPT_TRIAGEM,  # definido como constante no arquivo
    tools=[autenticar_cliente, encerrar_atendimento],
    sub_agents=[agente_credito, agente_cambio],
)
```

> O `agente_triagem` é o **agente raiz** passado ao `Runner` do ADK.

---

## 7. Tratamento de Erros

| Cenário | Comportamento |
|---------|--------------|
| CSV `clientes.csv` não encontrado | Ferramenta retorna `erro`; agente informa: "Estamos com uma instabilidade temporária. Tente novamente em alguns instantes." |
| CPF com formato inválido (< 11 dígitos após normalização) | Ferramenta retorna `autenticado=False`; agente pede para digitar novamente. |
| Data de nascimento em formato inválido | Ferramenta retorna `autenticado=False`; agente pede para informar no formato `DD/MM/AAAA`. |
| CPF não encontrado na base | Retorna `autenticado=False`; agente não informa se CPF existe ou não (segurança). |
| Dados corretos mas cliente bloqueado (futuro) | Escopo fora do MVP; reservado para expansão. |

---

## 8. Critérios de Aceitação

- [ ] Agente inicia com saudação ao abrir a conversa.
- [ ] Agente coleta CPF e data de nascimento em mensagens separadas.
- [ ] Autenticação bem-sucedida exibe nome do cliente e pergunta sobre necessidade.
- [ ] Falha na autenticação permite nova tentativa sem reiniciar a sessão.
- [ ] Terceira falha encerra o atendimento com mensagem amigável.
- [ ] CPF com pontuação (`123.456.789-01`) é normalizado corretamente.
- [ ] Data fora do formato `DD/MM/AAAA` é rejeitada com pedido de correção.
- [ ] Após autenticação, cliente que menciona "limite" é direcionado ao Agente de Crédito.
- [ ] Após autenticação, cliente que menciona "dólar" é direcionado ao Agente de Câmbio.
- [ ] Handoff é transparente — cliente não vê nenhuma mensagem de redirecionamento técnico.
- [ ] Pedido de encerramento em qualquer momento chama `encerrar_atendimento`.
