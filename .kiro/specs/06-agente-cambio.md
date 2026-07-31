# Spec 06 — Agente de Câmbio

## Status: Draft

---

## 1. Visão Geral

O Agente de Câmbio é acionado após autenticação quando o cliente deseja consultar a cotação de uma moeda estrangeira em relação ao Real (BRL). Ele consulta uma API pública gratuita em tempo real, apresenta a cotação de forma clara e encerra o atendimento de câmbio com uma mensagem amigável.

**Arquivo de implementação:** `agents/cambio.py`  
**Ferramentas usadas:** `tools/cambio_tools.py`

---

## 2. Responsabilidades

1. Identificar qual moeda o cliente quer consultar.
2. Buscar a cotação em tempo real via API externa.
3. Apresentar a cotação de forma formatada e compreensível.
4. Encerrar o atendimento de câmbio ou oferecer nova consulta de outra moeda.

---

## 3. API de Câmbio

**Serviço:** AwesomeAPI — Economia  
**URL base:** `https://economia.awesomeapi.com.br/last/{par}`  
**Exemplo:** `https://economia.awesomeapi.com.br/last/USD-BRL`  
**Autenticação:** Nenhuma (totalmente gratuita)  
**Formato resposta:** JSON

**Resposta exemplo para `USD-BRL`:**
```json
{
  "USDBRL": {
    "code": "USD",
    "codein": "BRL",
    "name": "Dólar Americano/Real Brasileiro",
    "high": "5.85",
    "low": "5.72",
    "varBid": "-0.03",
    "pctChange": "-0.52",
    "bid": "5.74",
    "ask": "5.75",
    "timestamp": "1722430800",
    "create_date": "2026-07-31 10:00:00"
  }
}
```

**Campos usados na resposta ao cliente:**
- `bid`: cotação de compra (valor que o banco compra a moeda)
- `ask`: cotação de venda (valor que o banco vende a moeda)
- `name`: nome completo do par de moedas
- `pctChange`: variação percentual no dia
- `create_date`: data/hora da última atualização

---

## 4. Moedas Suportadas

| Nome informal (reconhecido pelo LLM) | Código API | Par consultado |
|-------------------------------------|-----------|----------------|
| dólar, dolar, USD | USD | USD-BRL |
| euro, EUR | EUR | EUR-BRL |
| libra, pound, GBP | GBP | GBP-BRL |
| iene, yen, JPY | JPY | JPY-BRL |
| bitcoin, BTC | BTC | BTC-BRL |
| dólar canadense, CAD | CAD | CAD-BRL |
| dólar australiano, AUD | AUD | AUD-BRL |
| franco suíço, CHF | CHF | CHF-BRL |
| peso argentino, ARS | ARS | ARS-BRL |

> O LLM é responsável por mapear a intenção do cliente para o código correto antes de chamar a ferramenta. A lista acima é orientativa para o system prompt.

---

## 5. Fluxo Detalhado

```
[Agente de Câmbio recebe controle]
    │
    ├── Moeda já identificada na frase inicial?
    │       SIM → usa moeda identificada
    │       NÃO → "Qual moeda deseja consultar? (ex: dólar, euro, libra)"
    │
    ▼
Chama: buscar_cotacao(codigo_moeda)
    │
    ├── SUCESSO ──────────────────────────────────────────────────────────────┐
    │                                                                         │
    │   Formata e exibe:                                                      │
    │   "💱 Cotação do [Nome] em relação ao Real:                             │
    │    • Compra: R$ X,XX                                                    │
    │    • Venda: R$ X,XX                                                     │
    │    • Variação hoje: X,XX%                                               │
    │    • Atualizado em: DD/MM/AAAA HH:MM"                                  │
    │                                                                         │
    │   "Deseja consultar outra moeda ou posso ajudá-lo com mais alguma coisa?"
    │       │                                                                 │
    │       ├── SIM (outra moeda) → reinicia fluxo                           │
    │       └── NÃO / encerrar   → mensagem de despedida                    │
    │                                                                         │
    └── ERRO (API indisponível) ──────────────────────────────────────────────┘
        "No momento estamos com dificuldades para buscar a cotação.
         Por favor, tente novamente em alguns instantes."
        → Oferece tentar outra moeda ou encerrar
```

---

## 6. System Prompt do Agente de Câmbio

```
Você é o especialista em câmbio do Banco Ágil. O cliente já foi autenticado.

Suas responsabilidades:
1. Identificar qual moeda o cliente deseja consultar.
2. Se a moeda não for mencionada, perguntar gentilmente qual moeda deseja.
3. Usar a ferramenta `buscar_cotacao` com o código correto da moeda (USD, EUR, GBP, etc.).
4. Apresentar a cotação de forma clara: compra, venda, variação do dia e horário.
5. Perguntar se deseja consultar outra moeda.
6. Encerrar com mensagem amigável quando o cliente não precisar de mais nada.

MAPEAMENTO DE MOEDAS:
- dólar / dolar / USD → "USD"
- euro / EUR → "EUR"
- libra / pound / GBP → "GBP"
- iene / yen / JPY → "JPY"
- bitcoin / BTC → "BTC"
- dólar canadense / CAD → "CAD"
- dólar australiano / AUD → "AUD"
- franco suíço / CHF → "CHF"
- peso argentino / ARS → "ARS"

REGRAS:
- Sempre use a ferramenta — nunca invente valores de cotação.
- Se a moeda não for reconhecida, informe que não temos suporte para ela e 
  sugira as opções disponíveis.
- Formate valores monetários com 2 casas decimais (R$ X,XX).
- Formate variação com sinal (ex: +0,52% ou -0,52%).
- Se o cliente quiser encerrar, use `encerrar_atendimento`.
```

---

## 7. Ferramentas (Tools)

### 7.1 `buscar_cotacao`

**Arquivo:** `tools/cambio_tools.py`

```python
def buscar_cotacao(codigo_moeda: str) -> dict:
    """
    Busca a cotação atual de uma moeda em relação ao Real (BRL).
    
    Args:
        codigo_moeda: Código ISO da moeda (ex: 'USD', 'EUR', 'GBP').
                      Case-insensitive; normalizado internamente para maiúsculas.
    
    Returns:
        dict:
            - sucesso (bool)
            - moeda_codigo (str): código normalizado (ex: 'USD')
            - moeda_nome (str): nome completo (ex: 'Dólar Americano/Real Brasileiro')
            - cotacao_compra (float): valor bid
            - cotacao_venda (float): valor ask
            - variacao_pct (float): variação percentual no dia
            - data_atualizacao (str): data/hora formatada 'DD/MM/AAAA HH:MM'
            - erro (str | None): mensagem de erro se sucesso=False
    """
```

**Lógica:**
1. Normalizar `codigo_moeda` para maiúsculas.
2. Verificar se código está na lista de moedas suportadas (`MOEDAS_SUPORTADAS` de `config.py`).
3. Montar URL: `{CAMBIO_API_BASE_URL}/{codigo_moeda}-BRL`.
4. Fazer requisição HTTP GET com `httpx` (timeout: 5 segundos).
5. Parsear JSON e extrair campos `bid`, `ask`, `pctChange`, `name`, `create_date`.
6. Converter `bid`, `ask` para float; `pctChange` para float.
7. Formatar `create_date` para `DD/MM/AAAA HH:MM`.
8. Retornar dict estruturado.

**Tratamento de erros:**
- Timeout ou conexão recusada → `sucesso=False`, `erro="API de câmbio indisponível"`
- Status HTTP != 200 → `sucesso=False`, `erro="Moeda não encontrada ou serviço indisponível"`
- Código não suportado (não está na lista) → `sucesso=False`, `erro="Moeda '{codigo}' não suportada. Moedas disponíveis: USD, EUR, GBP, JPY, BTC, CAD, AUD, CHF, ARS"`
- JSON malformado → `sucesso=False`, `erro="Erro ao processar resposta da API"`

---

## 8. Definição ADK do Agente

```python
# agents/cambio.py (estrutura)

from google.adk.agents import Agent
from tools.cambio_tools import buscar_cotacao
from tools.auth_tools import encerrar_atendimento

agente_cambio = Agent(
    name="agente_cambio",
    model="gemini-2.0-flash",
    description="Especialista em câmbio: consulta cotações de moedas estrangeiras em tempo real.",
    instruction=SYSTEM_PROMPT_CAMBIO,
    tools=[
        buscar_cotacao,
        encerrar_atendimento,
    ],
)
```

> Este agente **não tem sub-agentes**. É um agente folha na hierarquia.

---

## 9. Formato de Exibição da Cotação

O agente deve formatar a resposta ao cliente de forma padronizada. Exemplo para USD:

```
💱 Cotação do Dólar Americano (USD → BRL):

  Compra:    R$ 5,74
  Venda:     R$ 5,75
  Variação:  -0,52% hoje

Atualizado em: 31/07/2026 às 10:00
```

> A formatação exata é de responsabilidade do LLM com base nas instruções do system prompt. O exemplo acima serve como orientação.

---

## 10. Tratamento de Erros

| Cenário | Comportamento |
|---------|--------------|
| API timeout (> 5s) | Informa instabilidade, sugere tentar novamente ou consultar outra moeda |
| Moeda não suportada pelo sistema | Lista as moedas disponíveis e pede nova escolha |
| Moeda não encontrada na API (400/404) | Informa que a moeda não está disponível no momento |
| Resposta JSON inesperada | Informa instabilidade e oferece alternativas |
| Cliente pede previsão futura de câmbio | Informa que só fornece cotação em tempo real, não previsões |

---

## 11. Critérios de Aceitação

- [ ] Agente identifica a moeda quando mencionada na frase inicial ("quanto está o dólar?").
- [ ] Agente pergunta a moeda se não mencionada.
- [ ] `buscar_cotacao("USD")` retorna cotação atual com compra, venda e variação.
- [ ] Cotação exibida com 2 casas decimais formatadas em R$.
- [ ] Variação exibida com sinal (+ ou -) e símbolo %.
- [ ] Data de atualização exibida no formato legível.
- [ ] API indisponível → mensagem amigável sem encerrar abruptamente.
- [ ] Moeda não suportada → lista as opções disponíveis.
- [ ] Após exibir cotação, agente oferece consultar outra moeda.
- [ ] Cliente pode encerrar a qualquer momento via `encerrar_atendimento`.
- [ ] Agente nunca inventa ou alucina valores de cotação — sempre usa a ferramenta.
