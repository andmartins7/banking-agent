# Spec 08 — Interface Streamlit (UI)

## Status: Draft

---

## 1. Visão Geral

A interface é uma aplicação Streamlit de chat que simula um atendimento bancário completo. O cliente digita mensagens em linguagem natural e recebe respostas dos agentes como se estivesse conversando com um único atendente. A UI é simples, funcional e não expõe detalhes técnicos do sistema de agentes.

**Arquivo de implementação:** `app.py`  
**Dependência principal:** `orchestrator.py` (via `criar_sessao`, `processar_mensagem`, `sessao_encerrada`)

---

## 2. Layout da Interface

```
┌─────────────────────────────────────────────────────────┐
│  🏦  Banco Ágil — Atendimento Digital                   │  ← Header (st.title)
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [avatar 🏦] Olá! Bem-vindo ao Banco Ágil...           │  ← Mensagem do agente
│                                                         │
│  [avatar 👤] 12345678901                               │  ← Mensagem do usuário
│                                                         │
│  [avatar 🏦] Obrigado! Agora informe sua data...       │  ← Mensagem do agente
│                                                         │
│  ...                                                    │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  [ Digite sua mensagem...              ] [ Enviar ]     │  ← Input (desabilitado após encerramento)
└─────────────────────────────────────────────────────────┘
```

**Elementos da UI:**
- `st.title`: cabeçalho fixo com nome do banco.
- `st.chat_message("assistant")`: balão de mensagem do agente (avatar 🏦).
- `st.chat_message("user")`: balão de mensagem do usuário (avatar 👤).
- `st.chat_input`: campo de texto + botão de envio.
- `st.spinner`: indicador de carregamento enquanto o agente processa.
- `st.info` / `st.warning`: avisos de sistema (ex: sessão encerrada).

---

## 3. Gerenciamento de Estado (`st.session_state`)

Todos os dados de sessão são mantidos em `st.session_state` para sobreviver a re-renders do Streamlit.

```python
# Campos obrigatórios no session_state

st.session_state.session_id: str         # UUID único por sessão do browser
st.session_state.messages: list[dict]    # Histórico: [{"role": "user"|"assistant", "content": str}]
st.session_state.initialized: bool       # Flag: se a sessão ADK já foi criada
st.session_state.ended: bool             # Flag: se o atendimento foi encerrado
```

---

## 4. Fluxo da Aplicação

### 4.1 Inicialização (primeira execução por sessão)

```python
if "initialized" not in st.session_state:
    # Gerar ID único de sessão
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.session_state.initialized = False
    st.session_state.ended = False

# Criar sessão ADK e obter saudação inicial
if not st.session_state.initialized:
    criar_sessao(st.session_state.session_id)
    
    # Disparar mensagem vazia para obter a saudação do agente
    saudacao = processar_mensagem(st.session_state.session_id, "olá")
    st.session_state.messages.append({"role": "assistant", "content": saudacao})
    st.session_state.initialized = True
```

### 4.2 Loop principal

```python
# 1. Renderizar histórico de mensagens
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🏦" if msg["role"] == "assistant" else "👤"):
        st.markdown(msg["content"])

# 2. Exibir aviso se sessão encerrada
if st.session_state.ended:
    st.info("Atendimento encerrado. Recarregue a página para iniciar uma nova sessão.")

# 3. Campo de input (desabilitado se encerrado)
if not st.session_state.ended:
    if user_input := st.chat_input("Digite sua mensagem..."):
        # Adicionar mensagem do usuário ao histórico
        st.session_state.messages.append({"role": "user", "content": user_input})
        
        # Exibir mensagem do usuário imediatamente
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)
        
        # Processar com spinner
        with st.chat_message("assistant", avatar="🏦"):
            with st.spinner(""):
                resposta = processar_mensagem(st.session_state.session_id, user_input)
            st.markdown(resposta)
        
        # Adicionar resposta ao histórico
        st.session_state.messages.append({"role": "assistant", "content": resposta})
        
        # Verificar se sessão foi encerrada
        if sessao_encerrada(st.session_state.session_id):
            st.session_state.ended = True
            st.rerun()
```

---

## 5. Estrutura Completa do `app.py`

```python
# app.py

import uuid
import streamlit as st
from orchestrator import criar_sessao, processar_mensagem, sessao_encerrada

# ── Configuração da página ─────────────────────────────────────────────────
st.set_page_config(
    page_title="Banco Ágil — Atendimento",
    page_icon="🏦",
    layout="centered",
)

# ── Cabeçalho ──────────────────────────────────────────────────────────────
st.title("🏦 Banco Ágil")
st.caption("Atendimento Digital — Como posso ajudá-lo hoje?")
st.divider()

# ── Inicialização do estado da sessão ─────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id  = str(uuid.uuid4())
    st.session_state.messages    = []
    st.session_state.initialized = False
    st.session_state.ended       = False

# ── Criar sessão ADK e obter saudação inicial ─────────────────────────────
if not st.session_state.initialized:
    criar_sessao(st.session_state.session_id)
    with st.spinner("Iniciando atendimento..."):
        saudacao = processar_mensagem(st.session_state.session_id, "iniciar")
    st.session_state.messages.append({"role": "assistant", "content": saudacao})
    st.session_state.initialized = True

# ── Renderizar histórico ───────────────────────────────────────────────────
for msg in st.session_state.messages:
    avatar = "🏦" if msg["role"] == "assistant" else "👤"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])

# ── Aviso de sessão encerrada ─────────────────────────────────────────────
if st.session_state.ended:
    st.info("✅ Atendimento encerrado. Recarregue a página para iniciar uma nova sessão.")
    st.stop()

# ── Input do usuário ───────────────────────────────────────────────────────
if user_input := st.chat_input("Digite sua mensagem..."):
    # Exibir mensagem do usuário
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)

    # Processar e exibir resposta do agente
    with st.chat_message("assistant", avatar="🏦"):
        with st.spinner(""):
            resposta = processar_mensagem(st.session_state.session_id, user_input)
        st.markdown(resposta)

    st.session_state.messages.append({"role": "assistant", "content": resposta})

    # Checar encerramento
    if sessao_encerrada(st.session_state.session_id):
        st.session_state.ended = True
        st.rerun()
```

---

## 6. Comportamentos Esperados por Cenário

| Cenário | Comportamento na UI |
|---------|-------------------|
| Primeira abertura | Saudação do agente exibida automaticamente |
| Usuário envia mensagem | Input desaparece, spinner ativo, resposta aparece |
| Agente pede CPF | Campo de input livre (sem máscara) |
| Autenticação falha | Agente responde no chat, input permanece ativo |
| 3ª falha de autenticação | Mensagem de encerramento + `st.info` + input desabilitado |
| Atendimento encerrado pelo usuário | Mesmo comportamento do item acima |
| Erro de API/sistema | Mensagem amigável no chat; input permanece ativo |
| Recarregar página | Nova sessão limpa começa do zero (novo UUID) |

---

## 7. Considerações de UX

- **Sem formulários separados**: toda interação é via chat livre. O agente guia o fluxo.
- **Sem botões de navegação**: sem "ir para crédito", "ir para câmbio". O agente detecta a intenção.
- **Sem exposição de dados técnicos**: scores, nomes de agentes, IDs de sessão nunca aparecem.
- **Markdown nas respostas**: o agente pode usar negrito, listas e emojis para melhor legibilidade.
- **Responsivo**: `layout="centered"` garante leitura confortável em qualquer largura.
- **Sem histórico persistente entre sessões**: ao recarregar, o contexto é zerado (MVP).

---

## 8. Como Executar

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Criar e preencher variáveis de ambiente
copy .env.example .env
# Editar .env e adicionar GOOGLE_API_KEY=sua_chave

# 3. Criar dados iniciais
python data/seed.py

# 4. Iniciar a aplicação
streamlit run app.py
```

A aplicação abrirá automaticamente em `http://localhost:8501`.

---

## 9. Critérios de Aceitação

- [ ] App inicia sem erros após `streamlit run app.py`.
- [ ] Saudação do agente aparece automaticamente ao abrir a página.
- [ ] Mensagens do usuário e do agente são exibidas com avatares distintos.
- [ ] Spinner é exibido enquanto o agente processa.
- [ ] Campo de input é desabilitado (`st.stop()`) após encerramento.
- [ ] Mensagem de `st.info` é exibida após encerramento.
- [ ] Recarregar a página inicia nova sessão limpa.
- [ ] Toda a conversa (histórico completo) é visível na tela sem scroll horizontal.
- [ ] Erros de backend não quebram a UI — mensagem amigável é exibida no chat.
- [ ] Fluxo completo funcional: autenticação → crédito → entrevista → retorno → câmbio.
- [ ] Markdown nas respostas é renderizado corretamente (`st.markdown`).
- [ ] `page_title` e `page_icon` configurados corretamente.
