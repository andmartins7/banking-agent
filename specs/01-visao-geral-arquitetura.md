# Spec 01 — Visão Geral e Arquitetura do Sistema

## Status: Draft

---

## 1. Visão Geral

O **Banco Ágil** é um sistema de atendimento ao cliente bancário baseado em múltiplos agentes de IA especializados. O cliente interage com uma interface unificada (Streamlit) sem perceber as transições entre agentes — a experiência é de uma única conversa fluida.

O sistema implementa um padrão de **orquestração multi-agente com handoff implícito**: um agente coordenador (Triagem) autentica o cliente e delega para agentes especialistas conforme a necessidade detectada na conversa.

---

## 2. Princípios de Design

- **Handoff invisível**: o cliente nunca vê nomes de agentes ou mensagens de redirecionamento técnico.
- **Escopo estrito**: cada agente responde apenas por suas responsabilidades definidas.
- **Persistência via CSV**: toda a base de dados e registro de operações usa arquivos CSV locais.
- **LLM como motor de raciocínio**: os agentes usam LLM (Gemini via Google ADK) para linguagem natural; lógica de negócio é implementada em Python puro com ferramentas explícitas.
- **Graceful degradation**: erros de API, CSV corrompido ou entrada inválida são tratados sem encerrar abruptamente a sessão.

---

## 3. Agentes do Sistema

| Agente | Responsabilidade | Ativado quando |
|--------|-----------------|----------------|
| **Triagem** | Autenticação (CPF + data nasc.) e roteamento | Sempre — porta de entrada |
| **Crédito** | Consulta e solicitação de aumento de limite | Cliente autenticado solicita crédito |
| **Entrevista de Crédito** | Entrevista financeira + recálculo de score | Solicitação de crédito rejeitada ou pedido direto |
| **Câmbio** | Cotação de moedas em tempo real | Cliente solicita cotação de moeda |

---

## 4. Arquitetura Técnica

### Stack escolhida

| Camada | Tecnologia | Justificativa |
|--------|-----------|---------------|
| Framework de agentes | **Google ADK** (Agent Developer Kit) | Suporte nativo a multi-agente, ferramentas, handoff e Gemini |
| LLM | **Gemini 2.0 Flash** (via Gemini API) | Free tier generoso, baixa latência, suporte a function calling |
| UI | **Streamlit** | Requisito do desafio; rápido para prototipar chat |
| Persistência | **CSV** (pandas) | Requisito do desafio |
| Cotação de câmbio | **API pública de câmbio** (exchangerate-api.com ou awesomeapi) | Free tier sem autenticação, resposta JSON simples |
| Ambiente | **Python 3.11+**, `.env` para secrets | Padrão de mercado |

### Estrutura de diretórios

```
banking-agent/
├── specs/                      # Specs do projeto
├── agents/
│   ├── __init__.py
│   ├── triagem.py              # Agente de Triagem
│   ├── credito.py              # Agente de Crédito
│   ├── entrevista_credito.py   # Agente de Entrevista de Crédito
│   └── cambio.py               # Agente de Câmbio
├── tools/
│   ├── __init__.py
│   ├── auth_tools.py           # Ferramentas de autenticação (leitura clientes.csv)
│   ├── credito_tools.py        # Ferramentas de crédito (leitura/escrita CSV)
│   ├── score_tools.py          # Ferramentas de score (cálculo e atualização)
│   └── cambio_tools.py         # Ferramentas de câmbio (chamada API externa)
├── data/
│   ├── clientes.csv            # Base de clientes (CPF, data nasc., score, limite)
│   ├── score_limite.csv        # Tabela de score mínimo por faixa de limite
│   └── solicitacoes_aumento_limite.csv  # Registro de pedidos (criado em runtime)
├── orchestrator.py             # Criação e configuração do agente raiz (Triagem)
├── app.py                      # Interface Streamlit
├── config.py                   # Configurações centralizadas (paths, pesos de score)
├── requirements.txt
├── .env.example
└── README.md
```

---

## 5. Fluxo Geral de Atendimento

```
Cliente abre o app (Streamlit)
        │
        ▼
[Agente de Triagem]
  - Saudação inicial
  - Coleta CPF
  - Coleta data de nascimento
  - Autentica via clientes.csv
  - Até 3 tentativas; falha → encerra
        │
        ├─ "quero ver meu limite" / "crédito"
        │         ▼
        │   [Agente de Crédito]
        │     - Consulta limite atual
        │     - Processa solicitação de aumento
        │     - Checa score vs score_limite.csv
        │     - Aprovado → registra 'aprovado'
        │     - Reprovado → oferta entrevista
        │              ▼ (aceita entrevista)
        │         [Agente de Entrevista de Crédito]
        │           - Coleta dados financeiros
        │           - Calcula novo score
        │           - Atualiza clientes.csv
        │           - Retorna ao Agente de Crédito
        │
        └─ "câmbio" / "cotação" / "dólar"
                  ▼
            [Agente de Câmbio]
              - Busca cotação via API
              - Exibe ao cliente
              - Encerra atendimento de câmbio
```

---

## 6. Modelo de Estado de Sessão

O estado da conversa é mantido no `session_state` do Streamlit e passado ao ADK via `InMemorySessionService`. Os campos relevantes são:

```python
{
    "authenticated": bool,
    "cliente": {           # preenchido após autenticação
        "cpf": str,
        "nome": str,
        "score": int,
        "limite_atual": float
    },
    "auth_attempts": int,  # contador de tentativas (máx 3)
    "conversation_ended": bool
}
```

---

## 7. Regras Transversais

1. Nenhum agente responde fora do seu escopo. Se o cliente perguntar algo fora do escopo, o agente informa educadamente e, se necessário, redireciona ao fluxo correto.
2. Qualquer solicitação de encerramento pelo cliente dispara a ferramenta `encerrar_atendimento`.
3. Tom: sempre respeitoso, objetivo, sem jargão técnico.
4. Handoff é feito por sub-agentes no Google ADK (`sub_agents` no `Agent`): o agente pai transfere controle sem expor a transição ao cliente.
5. Erros técnicos são logados em console/arquivo; o cliente recebe mensagem amigável.

---

## 8. Critérios de Aceitação (MVP)

- [ ] Cliente consegue se autenticar com CPF e data de nascimento.
- [ ] Após 3 falhas de autenticação, o atendimento é encerrado.
- [ ] Cliente autenticado consegue consultar seu limite de crédito.
- [ ] Cliente consegue solicitar aumento de limite, com resultado registrado em CSV.
- [ ] Solicitação é aprovada ou rejeitada com base no score e na tabela `score_limite.csv`.
- [ ] Solicitação rejeitada oferece opção de entrevista de crédito.
- [ ] Entrevista coleta dados financeiros e recalcula o score, atualizando `clientes.csv`.
- [ ] Após entrevista, cliente é retornado ao Agente de Crédito para nova análise.
- [ ] Cliente consegue consultar cotação de moedas em tempo real.
- [ ] Interface Streamlit funcional para todo o fluxo acima.
- [ ] Transições entre agentes são invisíveis ao cliente.
