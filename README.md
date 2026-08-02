# 🏦 Banco Ágil — Agente Bancário Inteligente

O Banco Ágil é um atendimento bancário conversacional construído com Python,
Google ADK e Streamlit. A interface apresenta um único assistente ao cliente,
enquanto quatro capacidades internas cooperam para autenticação, crédito,
entrevista financeira e câmbio.

## Visão Geral

A solução reúne quatro capacidades:

- **Triagem:** autentica o cliente e direciona sua intenção;
- **Crédito:** consulta limite e processa pedidos de aumento;
- **Entrevista de Crédito:** coleta o perfil financeiro e recalcula o score;
- **Câmbio:** consulta cotações permitidas em reais.

A UI é um chat Streamlit. O modelo auxilia apenas a conversa, a identificação de
intenção e o uso das ferramentas. Autenticação, autorização, tentativas, regras
financeiras, persistência e encerramento são controlados por código
determinístico.

## Arquitetura do Sistema

```text
UI Streamlit (`app.py`)
    → orquestrador e Runner ADK (`orchestrator.py`)
        → capacidades internas (`agents/`)
            → ferramentas determinísticas (`tools/`)
                → estado da sessão
                → CSVs locais / provider externo de câmbio
```

O estado da sessão é a autoridade para identidade autenticada, tentativas,
autorização, andamento da entrevista e encerramento. As transições entre
capacidades são internas: por contrato, elas não são anunciadas ao cliente, e a
UI mantém a experiência de um único atendente.

### Capacidades e ferramentas

```text
Triagem
├── autenticar_cliente
├── encerrar_atendimento
├── Crédito
│   ├── consultar_limite
│   ├── registrar_solicitacao
│   ├── processar_solicitacao
│   ├── encerrar_atendimento
│   └── Entrevista de Crédito
│       ├── processar_entrevista_credito
│       └── encerrar_atendimento
└── Câmbio
    ├── buscar_cotacao
    └── encerrar_atendimento
```

- **Triagem:** valida CPF e data de nascimento, limita a autenticação a três
  tentativas e encerra o atendimento após a terceira falha. Depois da
  autenticação, direciona implicitamente a intenção para Crédito ou Câmbio.
- **Crédito:** consulta o limite, registra o pedido e aplica a política de
  `score_limite.csv`. O resultado final é `aprovado` ou `rejeitado`.
- **Entrevista de Crédito:** coleta renda mensal, tipo de emprego, despesas
  fixas, número de dependentes e existência de dívidas; recalcula o score,
  atualiza `clientes.csv` e reanalisa o mesmo pedido rejeitado.
- **Câmbio:** consulta a AwesomeAPI para USD, EUR, GBP, JPY, BTC, CAD, AUD, CHF
  e ARS. Valor e temporalidade vêm da resposta validada do provider; falhas e
  indisponibilidade não geram cotação fictícia.

### Modelo e decisões

O modelo configurado é `gemini-3.1-flash-lite`, com
`gemini-3.5-flash` como fallback. O modelo não autentica, calcula score, decide
crédito, grava dados nem produz valores de câmbio por conta própria; essas
responsabilidades pertencem às ferramentas e ao orquestrador.

### Persistência em CSV

| Arquivo | Finalidade |
|---|---|
| `data/clientes.csv` | Identidade, nascimento, score e limite dos clientes |
| `data/score_limite.csv` | Faixas de limite e score mínimo da política de crédito |
| `data/solicitacoes_aumento_limite.csv` | Histórico dos pedidos de aumento |

Cada solicitação contém exatamente os campos
`cpf_cliente`, `data_hora_solicitacao`, `limite_atual`,
`novo_limite_solicitado` e `status_pedido`. O pedido nasce como `pendente`; os
status finais canônicos são `aprovado` e `rejeitado`.

### Fórmula do score

```python
score_bruto = (
    (renda_mensal / (despesas_fixas + 1)) * 30
    + {"formal": 300, "autonomo": 200, "desempregado": 0}[tipo_emprego]
    + {0: 100, 1: 80, 2: 60, 3: 30}[min(num_dependentes, 3)]
    + {"nao": 100, "sim": -100}[tem_dividas]
)
score_final = max(0, min(1000, round(score_bruto)))
```

O resultado considera renda em relação às despesas, situação de emprego,
dependentes e dívidas, e sempre fica na faixa de 0 a 1000.

## Funcionalidades Implementadas

- autenticação por CPF e data de nascimento, com normalização de formatos;
- rejeição segura de CPF duplicado e limite global de três falhas;
- consulta e aumento de limite com política determinística;
- persistência do pedido e atualização atômica dos dados financeiros;
- entrevista sequencial com cinco respostas e reanálise do pedido associado;
- cotação validada de moedas permitidas, com timeout explícito;
- histórico de chat e estado da sessão isolados por atendimento;
- encerramento global com precedência sobre os demais fluxos.

O encerramento grava `conversation_ended` no estado. A partir daí, o
orquestrador não processa nova operação e a UI deixa de receber novos turnos
naquele atendimento. A autoridade é o estado da sessão, não uma heurística
textual da resposta.

Erros esperados são convertidos em respostas controladas: credencial inválida,
três falhas de autenticação, CSV ausente ou malformado, entrada financeira
inválida, falha de persistência, moeda não permitida, timeout, erro de transporte
e resposta inválida da API. Chaves e detalhes internos não são exibidos ao
cliente.

## Desafios e Soluções

- **Regras financeiras fora do LLM:** score, política de crédito e persistência
  ficam em funções determinísticas e testáveis.
- **Estado ADK confiável:** identidade, autorização e andamento do fluxo ficam
  no estado da sessão, separados do texto do histórico.
- **Pedido correto após entrevista:** a entrevista exige associação única ao
  pedido rejeitado e reanalisa essa mesma solicitação.
- **Provider externo isolado:** o acesso à AwesomeAPI tem allowlist, timeout,
  parsing estrito e renderer determinístico.
- **Validação vertical reproduzível:** os testes substituem LLM e rede por
  doubles controlados e exercitam o app Streamlit de ponta a ponta.

## Escolhas Técnicas e Justificativas

| Tecnologia/decisão | Justificativa |
|---|---|
| Python | Base única para agentes, regras, dados e testes |
| Streamlit | UI de chat pequena e reproduzível para o MVP |
| Google ADK | Orquestração de agentes, sessões e chamadas de ferramentas |
| Estado determinístico | Mantém autenticação e decisões críticas fora do modelo |
| CSVs | Persistência simples exigida pelo desafio |
| Provider de câmbio isolado | Evita acoplar regra de negócio ao transporte HTTP |
| `unittest` e Streamlit `AppTest` | Cobrem unidades, integração e fluxos verticais sem serviços reais |

## Tutorial de Execução e Testes

### Pré-requisitos

- Python 3.11 ou superior;
- chave da API Google Gemini para executar a aplicação real.

### 1. Clonar e instalar

```bash
git clone https://github.com/andmartins7/banking-agent.git
cd banking-agent
pip install -r requirements.txt
```

### 2. Configurar o ambiente

Copie `.env.example` para `.env` e preencha somente a variável obrigatória:

```env
GOOGLE_API_KEY=sua_chave_aqui
```

No Linux/macOS, use `cp .env.example .env`; no Windows, use
`copy .env.example .env`. Nunca versione o `.env` ou uma chave real.

### 3. Inicializar os dados

```bash
python data/seed.py
```

Sem argumentos, o seed cria apenas os CSVs ausentes e preserva os existentes.
Para recriar todos os arquivos:

```bash
python data/seed.py --force
```

`--force` sobrescreve os CSVs e deve ser usado somente quando a perda dos dados
locais atuais for intencional.

### 4. Executar a UI

```bash
python -m streamlit run app.py
```

A aplicação fica disponível em `http://localhost:8501`. No Windows,
`.\run.ps1` é um atalho opcional para o mesmo fluxo.

### 5. Executar os testes

```bash
python -B -m unittest discover -s tests
```

No checkpoint funcional desta entrega, a suíte registrou **417/417 testes
aprovados**, sem falhas, erros ou testes ignorados. Essa contagem descreve o
checkpoint atual e deve ser atualizada se a suíte mudar. A cobertura inclui
testes unitários, integração/orquestração e E2E verticais com Streamlit
`AppTest`, sem LLM ou rede real.

### Evidências verticais

Os cenários verticais comprovam autenticação válida; três falhas sem quarta
tentativa; consulta de limite; aumento aprovado e rejeitado; entrevista e novo
score; reanálise do mesmo pedido; Câmbio; timeout; encerramento; e isolamento
entre sessões.

### Dados de demonstração

| Nome | CPF | Data de nascimento | Score | Limite |
|---|---|---|---:|---:|
| João Silva | 12345678901 | 15/03/1985 | 750 | R$ 5.000 |
| Maria Oliveira | 98765432100 | 22/07/1990 | 420 | R$ 1.500 |
| Carlos Mendes | 11122233344 | 08/11/1978 | 600 | R$ 3.000 |
| Ana Souza | 55566677788 | 30/01/1995 | 850 | R$ 10.000 |
| Pedro Costa | 99988877766 | 14/06/1982 | 300 | R$ 500 |

## Limitações conhecidas

- as sessões são mantidas em memória;
- a persistência em CSV é destinada ao desafio/MVP;
- não há CI automatizada configurada no repositório.

## Estrutura do Projeto

```text
banking-agent/
├── agents/          # Capacidades internas e instruções
├── tools/           # Regras e integrações determinísticas
├── data/            # CSVs e seed idempotente
├── tests/           # Testes unitários, integração e E2E Streamlit
├── specs/           # Especificações e memória do projeto
├── app.py            # Interface Streamlit
├── orchestrator.py   # Sessões, roteamento e Runner ADK
├── config.py         # Constantes e configuração
├── requirements.txt
└── .env.example
```
