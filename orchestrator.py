"""
Orquestrador do sistema Banco Ágil.

Configura o Runner do Google ADK com o agente_triagem como raiz e
expõe funções síncronas para uso pelo Streamlit:
    - criar_sessao(session_id)
    - processar_mensagem(session_id, mensagem) -> str
    - sessao_encerrada(session_id) -> bool
"""

import asyncio
import os
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from config import GOOGLE_API_KEY, APP_NAME

load_dotenv()

# ── Validação de ambiente ──────────────────────────────────────────────────
if not GOOGLE_API_KEY:
    raise EnvironmentError(
        "\n\n[Banco Ágil] GOOGLE_API_KEY não encontrada!\n"
        "Passos para resolver:\n"
        "  1. Copie .env.example para .env\n"
        "  2. Preencha GOOGLE_API_KEY com sua chave do Google AI Studio\n"
        "     Obtenha em: https://aistudio.google.com/app/apikey\n"
    )

os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY

# ── Importação do agente raiz (após setar a env var) ──────────────────────
from agents.triagem import agente_triagem  # noqa: E402

# ── Serviços ADK ──────────────────────────────────────────────────────────
_session_service = InMemorySessionService()

_runner = Runner(
    app_name=APP_NAME,
    agent=agente_triagem,
    session_service=_session_service,
)

# ── Mensagens amigáveis por tipo de erro ──────────────────────────────────
_MSG_503 = (
    "O serviço está momentaneamente sobrecarregado. "
    "Por favor, tente enviar sua mensagem novamente em alguns segundos."
)
_MSG_429 = (
    "Atingimos o limite de requisições por minuto. "
    "Aguarde alguns instantes e tente novamente."
)
_MSG_ERRO_GERAL = (
    "Ocorreu um erro inesperado no sistema. "
    "Por favor, tente novamente ou entre em contato com o suporte."
)


# ── Event loop helper ─────────────────────────────────────────────────────

def _run_async(coro):
    """Executa uma coroutine de forma síncrona, compatível com Streamlit."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _classificar_erro(e: Exception) -> str:
    """Retorna mensagem amigável baseada no tipo de erro da API."""
    msg = str(e)
    if "503" in msg or "UNAVAILABLE" in msg:
        print(f"[AVISO] Modelo com alta demanda (503) — tente novamente em instantes.")
        return _MSG_503
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        print(f"[AVISO] Rate limit atingido (429).")
        return _MSG_429
    print(f"[ERRO RUNNER] {type(e).__name__}: {msg[:120]}")
    return _MSG_ERRO_GERAL


# ── API pública ────────────────────────────────────────────────────────────

def criar_sessao(session_id: str) -> None:
    """Cria uma nova sessão ADK para o cliente."""
    _run_async(
        _session_service.create_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
    )


def processar_mensagem(session_id: str, mensagem_usuario: str) -> str:
    """
    Envia uma mensagem ao agente e retorna a resposta em texto.

    Nunca propaga exceções — erros 503/429 retornam mensagem orientando
    o usuário a tentar novamente; outros erros retornam mensagem genérica.

    Args:
        session_id: Identificador único da sessão.
        mensagem_usuario: Texto digitado pelo usuário na UI.

    Returns:
        str: Resposta do agente para exibição na interface.
    """
    content = Content(role="user", parts=[Part(text=mensagem_usuario)])

    resposta_final = ""
    try:
        for event in _runner.run(
            user_id=session_id,
            session_id=session_id,
            new_message=content,
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    resposta_final = event.content.parts[0].text or ""
                break

    except Exception as e:
        resposta_final = _classificar_erro(e)

    return resposta_final.strip()


def sessao_encerrada(session_id: str) -> bool:
    """
    Verifica se o atendimento foi encerrado nesta sessão.

    Usada pelo Streamlit para desabilitar o campo de input.
    """
    try:
        session = _run_async(
            _session_service.get_session(
                app_name=APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )
        if session is None:
            return False
        return bool(session.state.get("conversation_ended", False))
    except Exception as e:
        print(f"[ERRO sessao_encerrada] session={session_id} | {e}")
        return False
