"""
Interface Streamlit do Banco Ágil.

Chat de atendimento bancário com múltiplos agentes de IA especializados.
O cliente interage como se estivesse conversando com um único atendente.
"""

import uuid
import streamlit as st

# ── Configuração da página (deve ser o primeiro comando Streamlit) ─────────
st.set_page_config(
    page_title="Banco Ágil — Atendimento",
    page_icon="🏦",
    layout="centered",
)

# ── Importação do orchestrator (após set_page_config) ─────────────────────
try:
    from orchestrator import criar_sessao, processar_mensagem, sessao_encerrada
    _ORCHESTRATOR_OK = True
    _ORCHESTRATOR_ERRO = ""
except EnvironmentError as e:
    _ORCHESTRATOR_OK = False
    _ORCHESTRATOR_ERRO = str(e)
except Exception as e:
    _ORCHESTRATOR_OK = False
    _ORCHESTRATOR_ERRO = f"Erro ao inicializar o sistema: {type(e).__name__}: {e}"

# ── Cabeçalho ──────────────────────────────────────────────────────────────
st.title("🏦 Banco Ágil")
st.caption("Atendimento Digital — Seguro, rápido e inteligente.")
st.divider()

# ── Verificar inicialização do sistema ────────────────────────────────────
if not _ORCHESTRATOR_OK:
    st.error(
        "**Sistema indisponível.** Não foi possível inicializar o atendimento.\n\n"
        f"```\n{_ORCHESTRATOR_ERRO}\n```\n\n"
        "Verifique se o arquivo `.env` está configurado com `GOOGLE_API_KEY`."
    )
    st.stop()

# ── Inicialização do estado da sessão ─────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id  = str(uuid.uuid4())
    st.session_state.messages    = []   # [{"role": "user"|"assistant", "content": str}]
    st.session_state.initialized = False
    st.session_state.ended       = False

# ── Criar sessão ADK e obter saudação inicial ─────────────────────────────
if not st.session_state.initialized:
    try:
        criar_sessao(st.session_state.session_id)
        with st.spinner("Iniciando atendimento..."):
            saudacao = processar_mensagem(st.session_state.session_id, "iniciar atendimento")
        if saudacao:
            st.session_state.messages.append({"role": "assistant", "content": saudacao})
        st.session_state.initialized = True
    except Exception as e:
        st.error(f"Erro ao iniciar sessão: {e}")
        st.stop()

# ── Renderizar histórico de mensagens ─────────────────────────────────────
for msg in st.session_state.messages:
    avatar = "🏦" if msg["role"] == "assistant" else "👤"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])

# ── Atendimento encerrado ─────────────────────────────────────────────────
if st.session_state.ended:
    st.info(
        "✅ **Atendimento encerrado.**  \n"
        "Obrigado por utilizar o Banco Ágil! "
        "Recarregue a página para iniciar um novo atendimento."
    )
    st.stop()

# ── Campo de input do usuário ─────────────────────────────────────────────
if user_input := st.chat_input("Digite sua mensagem..."):

    # Exibir mensagem do usuário imediatamente
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)

    # Processar com o agente e exibir resposta
    with st.chat_message("assistant", avatar="🏦"):
        with st.spinner(""):
            try:
                resposta = processar_mensagem(st.session_state.session_id, user_input)
            except Exception as e:
                resposta = (
                    "Desculpe, ocorreu um problema técnico. "
                    "Por favor, tente novamente em instantes."
                )
                print(f"[APP ERROR] {type(e).__name__}: {e}")

        st.markdown(resposta)

    # Salvar resposta no histórico
    st.session_state.messages.append({"role": "assistant", "content": resposta})

    # Verificar se o atendimento foi encerrado pelo agente
    try:
        if sessao_encerrada(st.session_state.session_id):
            st.session_state.ended = True
            st.rerun()
    except Exception:
        pass  # Falha silenciosa — não interrompe o fluxo
