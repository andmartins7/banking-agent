# 🏦 Banco Ágil — Agente Bancário Inteligente

Sistema de atendimento ao cliente bancário baseado em múltiplos agentes de IA especializados, construído com Google ADK (Agent Developer Kit) e Gemini 2.0 Flash.

---

## Visão Geral

O Banco Ágil simula um canal de atendimento digital onde o cliente interage com uma interface de chat unificada (Streamlit). Por trás dos panos, um sistema de **4 agentes especializados** colabora de forma transparente — o cliente nunca percebe as transições entre eles.

**Serviços disponíveis:**
- Autenticação segura por CPF e data de nascimento
- Consulta e solicitação de aumento de limite de crédito
- Análise financeira e recálculo de score de crédito
- Cotação de moedas estrangeiras em tempo real

---

## Arquitetura do Sistema

### Hierarquia de Agentes

```
Runner (ADK)
└── Agente de Triagem  [RAIZ — porta de entrada obrigatória]
    ├── tools: autenticar_cliente, encerrar_atendimento
    └── sub_agents:
        ├── Agente de Crédito
        │   ├── tools: consultar_limite, registrar_solicitacao,
        │   │          checar_score_para_limite, atualizar_status_solicitacao,
        │   │          atualizar_limite_cliente, encerrar_atendimento
        │   └── sub_agents:
        │       └── Agente de Entrevista de Crédito
        │           └── tools: calcular_score, atualizar_score_cliente,
        │                      encerrar_atendimento
        └── Agente de Câmbio
            └── tools: buscar_cotacao, encerrar_atendimento
```

### Responsabilidades por Agente

| Agente | Arquivo | Responsabilidade |
|--------|---------|-----------------|
| **Triagem** | `agents/triagem.py` | Autentica (CPF + nasc.), máx. 3 tentativas, roteia para especialistas |
| **Crédito** | `agents/credito.py` | Consulta limite, processa aumento, checa score vs tabela |
| **Entrevista de Crédito** | `agents/entrevista_credito.py` | 5 perguntas financeiras, recalcula score, atualiza CSV |
| **Câmbio** | `agents/cambio.py` | Cotação em tempo real via AwesomeAPI (USD, EUR, GBP, etc.) |

### Fluxo de Atendimento

```
Cliente abre o app
        │
        ▼
[Triagem] — Autentica (CPF + data de nascimento)
        │
        ├── "crédito / limite / aumento"
        │         ▼
        │   [Crédito] — Consulta → Solicitação → Checa score
        │         │
        │         ├── Aprovado → Atualiza limite → Confirma
        │         └── Rejeitado → Oferece entrevista
        │                   ▼ (aceita)
        │             [Entrevista] — 5 perguntas → Novo score → Retorna
        │
        └── "câmbio / dólar / cotação"
                  ▼
            [Câmbio] — Busca API → Exibe cotação → Oferece nova consulta
```

### Persistência de Dados (CSV)

| Arquivo | Uso |
|---------|-----|
| `data/clientes.csv` | Base de clientes: CPF, nome, data nascimento, score, limite |
| `data/score_limite.csv` | Tabela de faixas: limite máximo × score mínimo exigido |
| `data/solicitacoes_aumento_limite.csv` | Histórico de pedidos com status (pendente/aprovado/rejeitado) |

### Fórmula de Score de Crédito

```python
score = (
    (renda_mensal / (despesas_fixas + 1)) * 30      # peso_renda
    + {"formal": 300, "autonomo": 200, "desempregado": 0}[tipo_emprego]
    + {0: 100, 1: 80, 2: 60, 3: 30}[min(dependentes, 3)]
    + {"nao": 100, "sim": -100}[tem_dividas]
)
score_final = max(0, min(1000, round(score)))
```

---

## Funcionalidades Implementadas

- [x] Autenticação com CPF e data de nascimento (normalização de formatos)
- [x] Controle de tentativas de autenticação (máx. 3, encerra na 3ª falha)
- [x] Consulta de limite de crédito atual
- [x] Solicitação de aumento de limite com validação (novo > atual)
- [x] Registro persistente em CSV com status pendente/aprovado/rejeitado
- [x] Aprovação automática baseada em score vs tabela de faixas
- [x] Entrevista financeira com 5 perguntas sequenciais
- [x] Cálculo de score ponderado com clamp [0, 1000]
- [x] Atualização de score e limite em `clientes.csv`
- [x] Retorno ao Agente de Crédito após entrevista para nova análise
- [x] Cotação de moedas em tempo real (USD, EUR, GBP, JPY, BTC, CAD, AUD, CHF, ARS)
- [x] Handoff entre agentes invisível ao cliente
- [x] Encerramento por solicitação do cliente a qualquer momento
- [x] Tratamento de erros: API indisponível, CSV ausente, entrada inválida
- [x] Interface Streamlit com chat, spinner, histórico e desabilitação pós-encerramento

---

## Escolhas Técnicas

| Decisão | Justificativa |
|---------|--------------|
| **Google ADK** | Suporte nativo a multi-agente com `sub_agents`, handoff implícito, function calling e Gemini integrado |
| **Gemini 2.0 Flash** | Free tier generoso, baixa latência, excelente para function calling |
| **AwesomeAPI** | Gratuita, sem autenticação, JSON simples, suporta BRL |
| **Pandas** | Leitura/escrita CSV robusta, tipagem controlada com `dtype=str` |
| **InMemorySessionService** | Suficiente para MVP single-user; documentado como limitação para escala |
| **httpx** | Cliente HTTP moderno com timeout configurável e tratamento de erros limpo |

### Desafios e Soluções

**pandas 2.x — `applymap` removido:** Substituído por `.map()` em todas as ferramentas de CSV.

**ADK 1.3.0 — API assíncrona:** `InMemorySessionService` é 100% async. O `Runner.run()` é síncrono (gerador), mas `create_session` e `get_session` requerem `asyncio.run()`. O orquestrador encapsula isso em `_run_async()`.

**Handoff invisível:** O ADK gerencia `transfer_to_agent` internamente quando o agente pai decide delegar para um `sub_agent`. O sistema prompt de cada agente instrui a não mencionar termos técnicos.

**Estado de encerramento:** `encerrar_atendimento()` retorna `{"encerrado": True}`, mas o ADK não atualiza `session.state` automaticamente a partir do retorno de uma tool. O Streamlit detecta o encerramento pelo padrão de resposta do agente (heurística de conteúdo), mantendo robustez sem depender de estado interno do ADK.

---

## Tutorial de Execução

### Pré-requisitos

- Python 3.11+
- Chave da API do Google Gemini (gratuita): https://aistudio.google.com/app/apikey

### 1. Clonar o repositório

```bash
git clone https://github.com/seu-usuario/banking-agent.git
cd banking-agent
```

### 2. Instalar dependências

```bash
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente

```bash
# Linux/macOS
cp .env.example .env

# Windows
copy .env.example .env
```

Edite o arquivo `.env` e insira sua chave:
```env
GOOGLE_API_KEY=sua_chave_aqui
```

### 4. Inicializar dados

```bash
python data/seed.py
```

Isso cria os arquivos CSV com dados de teste em `data/`.

### 5. Executar a aplicação

```bash
python -m streamlit run app.py
```

> **Nota Windows:** Use sempre `python -m streamlit run app.py` em vez de `streamlit run app.py` diretamente, para garantir que o executável correto do Python seja usado.

Acesse em: **http://localhost:8501**

Ou use o script de atalho:
```powershell
.\run.ps1
```

---

## Dados de Teste

Use os seguintes dados para testar a autenticação:

| Nome | CPF | Data de Nascimento | Score | Limite |
|------|-----|--------------------|-------|--------|
| João Silva | 12345678901 | 15/03/1985 | 750 | R$ 5.000 |
| Maria Oliveira | 98765432100 | 22/07/1990 | 420 | R$ 1.500 |
| Carlos Mendes | 11122233344 | 08/11/1978 | 600 | R$ 3.000 |
| Ana Souza | 55566677788 | 30/01/1995 | 850 | R$ 10.000 |
| Pedro Costa | 99988877766 | 14/06/1982 | 300 | R$ 500 |

### Cenários de Teste Sugeridos

**Cenário 1 — Crédito aprovado:**
Login com Ana Souza (score 850) → solicitar aumento para R$ 15.000 → aprovado ✓

**Cenário 2 — Crédito rejeitado + entrevista:**
Login com Maria Oliveira (score 420) → solicitar aumento para R$ 5.000 → rejeitado → aceitar entrevista → novo score calculado

**Cenário 3 — Câmbio:**
Login com qualquer cliente → "quero saber a cotação do dólar"

**Cenário 4 — Falha de autenticação:**
Inserir CPF válido com data errada 3 vezes → encerramento automático

---

## Estrutura do Projeto

```
banking-agent/
├── agents/
│   ├── __init__.py
│   ├── triagem.py              # Agente raiz (autenticação + roteamento)
│   ├── credito.py              # Agente de crédito
│   ├── entrevista_credito.py   # Agente de entrevista financeira
│   └── cambio.py               # Agente de câmbio
├── tools/
│   ├── __init__.py
│   ├── auth_tools.py           # autenticar_cliente, encerrar_atendimento
│   ├── credito_tools.py        # 5 ferramentas de crédito
│   ├── score_tools.py          # calcular_score, atualizar_score_cliente
│   └── cambio_tools.py         # buscar_cotacao
├── data/
│   ├── seed.py                 # Script de inicialização dos CSVs
│   ├── clientes.csv            # Base de clientes
│   ├── score_limite.csv        # Tabela de faixas de limite
│   └── solicitacoes_aumento_limite.csv
├── .kiro/specs/                # Specs do projeto (spec-driven development)
├── app.py                      # Interface Streamlit
├── orchestrator.py             # Runner ADK + funções de sessão
├── config.py                   # Configurações centralizadas
├── requirements.txt
├── .env.example
└── README.md
```
