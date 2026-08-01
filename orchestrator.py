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
import unicodedata
from dotenv import load_dotenv
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from config import GOOGLE_API_KEY, APP_NAME
from session_state import (
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_ATTEMPTS,
    CREDIT_INTERVIEW_COLLECTING,
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_FIELDS,
    CREDIT_INTERVIEW_INTERRUPTED,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_READY,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    ErroAutorizacaoSessao,
    MENSAGEM_ATENDIMENTO_ENCERRADO,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    consumir_retorno_entrevista,
    criar_estado_inicial,
    encerrar_estado_atendimento,
    obter_cpf_autorizado_do_estado,
    recusar_entrevista_credito,
    registrar_resposta_entrevista,
    registrar_resposta_invalida_entrevista,
)
from tools.score_tools import (
    processar_entrevista_credito_autorizada,
    validar_resposta_entrevista,
)
from tools.credito_tools import reanalisar_solicitacao_autorizada
from tools.cambio_tools import renderizar_resultado_cotacao

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

_PERGUNTAS_ENTREVISTA = {
    "renda_mensal": "Informe sua renda mensal aproximada em reais.",
    "tipo_emprego": (
        "Informe sua situação de emprego: formal, autônomo ou desempregado."
    ),
    "despesas_fixas": "Informe suas despesas fixas mensais aproximadas.",
    "num_dependentes": "Informe quantas pessoas dependem financeiramente de você.",
    "tem_dividas": "Informe se possui dívidas ativas, respondendo sim ou não.",
}

_CHAVES_ESTADO_ENTREVISTA = (
    CREDIT_INTERVIEW_STATUS,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_ATTEMPTS,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
)

_MSG_ENTREVISTA_INTERROMPIDA = (
    "Não foi possível validar esta informação após duas tentativas. "
    "A entrevista foi interrompida sem processar seu perfil."
)
_MSG_ENTREVISTA_CONCLUIDA = (
    "Concluímos sua análise e seu perfil financeiro foi atualizado."
)
_MSG_ENTREVISTA_INCOMPLETA = (
    "Não foi possível processar a entrevista porque faltam respostas obrigatórias."
)
_MSG_REANALISE_APROVADA = (
    "Seu pedido de aumento de limite foi aprovado após a reanálise."
)
_MSG_REANALISE_REJEITADA = (
    "Seu pedido de aumento de limite permanece rejeitado após a reanálise."
)
_MSG_REANALISE_INDISPONIVEL = (
    "Não foi possível reanalisar seu pedido neste momento."
)
_MSG_OFERTA_ENTREVISTA = (
    "Seu pedido não pôde ser aprovado neste momento. "
    "Posso fazer algumas perguntas financeiras para atualizar seu score "
    "e reanalisar o mesmo pedido. Deseja continuar?"
)
_MSG_OFERTA_RECUSADA = (
    "Tudo bem, não faremos a entrevista. Você pode encerrar este atendimento "
    "ou solicitar outro atendimento quando desejar."
)
_MSG_OFERTA_AMBIGUA = (
    "Para confirmar, responda apenas sim ou não: deseja continuar com a entrevista?"
)

_RESPOSTAS_OFERTA_ACEITAS = {
    "sim",
    "quero",
    "aceito",
    "pode continuar",
    "pode continuar por favor",
    "vamos",
    "vamos continuar",
    "quero continuar",
    "sim quero",
    "sim aceito",
    "claro",
    "pode ser",
}
_RESPOSTAS_OFERTA_RECUSADAS = {
    "nao",
    "nao quero",
    "prefiro nao",
    "prefiro nao participar",
    "prefiro nao continuar",
    "agora nao",
    "nao agora",
    "nao obrigado",
    "nao obrigada",
}
_PEDIDOS_ENCERRAMENTO = {
    "encerrar",
    "encerrar atendimento",
    "quero encerrar",
    "quero encerrar o atendimento",
    "pode encerrar",
    "pode encerrar o atendimento",
    "finalizar",
    "finalizar atendimento",
    "quero finalizar",
    "quero finalizar o atendimento",
    "pode finalizar",
    "sair",
    "quero sair",
    "tchau",
    "ate mais",
    "ate logo",
}


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


def _normalizar_texto_deterministico(texto: object) -> str:
    """Normaliza somente a forma textual, sem inferir intenção."""
    if not isinstance(texto, str):
        return ""
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(caractere)
    )
    sem_pontuacao = "".join(
        " " if unicodedata.category(caractere).startswith("P") else caractere
        for caractere in sem_acentos
    )
    return " ".join(sem_pontuacao.split())


def _classificar_resposta_oferta(resposta: object) -> str:
    """Classifica uma manifestação inequívoca ou conserva a oferta ambígua."""
    normalizada = _normalizar_texto_deterministico(resposta)
    if normalizada in _RESPOSTAS_OFERTA_ACEITAS:
        return "accepted"
    if normalizada in _RESPOSTAS_OFERTA_RECUSADAS:
        return "declined"
    return "ambiguous"


def _classificar_encerramento(mensagem: object) -> str:
    """Reconhece somente pedidos explícitos pertencentes ao vocabulário finito."""
    normalizada = _normalizar_texto_deterministico(mensagem)
    if normalizada in _PEDIDOS_ENCERRAMENTO:
        return "terminate"
    return "not_terminate"


# ── API pública ────────────────────────────────────────────────────────────

def criar_sessao(session_id: str) -> None:
    """Cria uma nova sessão ADK para o cliente."""
    _run_async(
        _session_service.create_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
            state=criar_estado_inicial(),
        )
    )


def _persistir_estado_entrevista(session, novo_estado: dict[str, object]) -> None:
    """Persiste somente o delta da entrevista pelo mecanismo oficial do ADK."""
    state_delta = {
        chave: novo_estado[chave]
        for chave in _CHAVES_ESTADO_ENTREVISTA
        if novo_estado.get(chave) != session.state.get(chave)
    }
    if not state_delta:
        return

    event = Event(
        author="orquestrador_entrevista",
        actions=EventActions(state_delta=state_delta),
    )
    _run_async(_session_service.append_event(session=session, event=event))


def _processar_encerramento_global(
    session_id: str,
    mensagem_usuario: str,
) -> str | None:
    """Encerra ou bloqueia a sessão antes de qualquer outro fluxo."""
    session = _run_async(
        _session_service.get_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
    )
    if session is None:
        return None
    if bool(session.state.get(CONVERSATION_ENDED, False)):
        return MENSAGEM_ATENDIMENTO_ENCERRADO
    if _classificar_encerramento(mensagem_usuario) != "terminate":
        return None

    estado_encerrado = encerrar_estado_atendimento(session.state)
    event = Event(
        author="orquestrador_encerramento",
        actions=EventActions(state_delta={
            CONVERSATION_ENDED: estado_encerrado[CONVERSATION_ENDED],
        }),
    )
    _run_async(_session_service.append_event(session=session, event=event))
    return MENSAGEM_ATENDIMENTO_ENCERRADO


def _processar_retorno_pendente(
    session,
    state: dict[str, object] | None = None,
) -> str | None:
    """Reanalisa e consome um retorno pendente antes de qualquer Runner."""
    estado_atual = state if state is not None else session.state
    if not bool(estado_atual.get(CREDIT_INTERVIEW_RETURN_PENDING, False)):
        return None
    if bool(estado_atual.get(CONVERSATION_ENDED, False)):
        return "Este atendimento já foi encerrado."
    if estado_atual.get(CREDIT_INTERVIEW_STATUS) != CREDIT_INTERVIEW_COMPLETED:
        return f"{_MSG_REANALISE_INDISPONIVEL} Estado da entrevista inválido."

    try:
        cpf = obter_cpf_autorizado_do_estado(estado_atual)
    except ErroAutorizacaoSessao as e:
        return f"{_MSG_REANALISE_INDISPONIVEL} {e}"

    resultado = reanalisar_solicitacao_autorizada(
        cpf,
        estado_atual.get(CREDIT_INTERVIEW_REQUEST_TIMESTAMP),
    )
    status_pedido = resultado.get("status_pedido")
    if not resultado.get("processado") or status_pedido not in {
        "aprovado",
        "rejeitado",
    }:
        erro = resultado.get("erro") or "Tente novamente."
        return f"{_MSG_REANALISE_INDISPONIVEL} {erro}"

    estado_consumido = consumir_retorno_entrevista(
        estado_atual,
        status_pedido,
    )
    if (
        bool(estado_consumido.get(CREDIT_INTERVIEW_RETURN_PENDING, False))
        or estado_consumido.get(CREDIT_INTERVIEW_REQUEST_TIMESTAMP) is not None
    ):
        return f"{_MSG_REANALISE_INDISPONIVEL} Tente novamente."

    _persistir_estado_entrevista(session, estado_consumido)
    if status_pedido == "aprovado":
        return _MSG_REANALISE_APROVADA
    return _MSG_REANALISE_REJEITADA


def _recuperar_retorno_pendente(session_id: str) -> str | None:
    """Recupera retorno pendente da sessão antes do fluxo conversacional."""
    session = _run_async(
        _session_service.get_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
    )
    if session is None:
        return None
    return _processar_retorno_pendente(session)


def _processar_resposta_oferta(
    session_id: str,
    mensagem_usuario: str,
) -> str | None:
    """Intercepta aceite, recusa ou ambiguidade antes de qualquer Runner."""
    session = _run_async(
        _session_service.get_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
    )
    if session is None:
        return None

    state = session.state
    if state.get(CREDIT_INTERVIEW_STATUS) != CREDIT_INTERVIEW_OFFERED:
        return None
    if bool(state.get(CONVERSATION_ENDED, False)):
        return "Este atendimento já foi encerrado."

    classificacao = _classificar_resposta_oferta(mensagem_usuario)
    if classificacao == "accepted":
        novo_estado = aceitar_entrevista_credito(state)
        _persistir_estado_entrevista(session, novo_estado)
        primeiro_campo = novo_estado[CREDIT_INTERVIEW_CURRENT_FIELD]
        return _PERGUNTAS_ENTREVISTA[primeiro_campo]
    if classificacao == "declined":
        novo_estado = recusar_entrevista_credito(state)
        _persistir_estado_entrevista(session, novo_estado)
        return _MSG_OFERTA_RECUSADA
    return _MSG_OFERTA_AMBIGUA


def _obter_oferta_pendente(session_id: str) -> str | None:
    """Retorna a oferta oficial após o turno que persistiu a rejeição."""
    session = _run_async(
        _session_service.get_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
    )
    if session is None:
        return None
    state = session.state
    if bool(state.get(CONVERSATION_ENDED, False)):
        return None
    if state.get(CREDIT_INTERVIEW_STATUS) == CREDIT_INTERVIEW_OFFERED:
        return _MSG_OFERTA_ENTREVISTA
    return None


def _processar_entrevista_pronta(session, state: dict[str, object]) -> str:
    """Processa uma entrevista pronta e conclui somente após sucesso real."""
    respostas = state.get(CREDIT_INTERVIEW_RESPONSES)
    if not isinstance(respostas, dict) or any(
        campo not in respostas for campo in CREDIT_INTERVIEW_FIELDS
    ):
        _persistir_estado_entrevista(session, state)
        return _MSG_ENTREVISTA_INCOMPLETA

    try:
        cpf = obter_cpf_autorizado_do_estado(state)
    except ErroAutorizacaoSessao as e:
        _persistir_estado_entrevista(session, state)
        return f"Não foi possível processar a entrevista. {e}"

    resultado = processar_entrevista_credito_autorizada(
        cpf=cpf,
        renda_mensal=respostas["renda_mensal"],
        tipo_emprego=respostas["tipo_emprego"],
        despesas_fixas=respostas["despesas_fixas"],
        num_dependentes=respostas["num_dependentes"],
        tem_dividas=respostas["tem_dividas"],
    )
    if not resultado["processado"] or not resultado["perfil_atualizado"]:
        _persistir_estado_entrevista(session, state)
        erro = resultado["erro"] or "Tente novamente."
        return f"Não foi possível processar a entrevista. {erro}"

    estado_concluido = concluir_processamento_entrevista(state)
    _persistir_estado_entrevista(session, estado_concluido)
    resposta_reanalise = _processar_retorno_pendente(
        session,
        estado_concluido,
    )
    if resposta_reanalise is None:
        return _MSG_ENTREVISTA_CONCLUIDA
    return f"{_MSG_ENTREVISTA_CONCLUIDA} {resposta_reanalise}"


def _processar_coleta_entrevista(
    session_id: str,
    mensagem_usuario: str,
) -> str | None:
    """Processa um turno determinístico quando a entrevista está em coleta."""
    session = _run_async(
        _session_service.get_session(
            app_name=APP_NAME,
            user_id=session_id,
            session_id=session_id,
        )
    )
    if session is None:
        return None

    state = session.state
    if bool(state.get(CONVERSATION_ENDED, False)):
        return None
    status = state.get(CREDIT_INTERVIEW_STATUS)
    if status == CREDIT_INTERVIEW_READY:
        return _processar_entrevista_pronta(session, state)
    if status != CREDIT_INTERVIEW_COLLECTING:
        return None

    campo_atual = state.get(CREDIT_INTERVIEW_CURRENT_FIELD)
    validacao = validar_resposta_entrevista(campo_atual, mensagem_usuario)
    if validacao["valida"]:
        novo_estado = registrar_resposta_entrevista(
            state,
            validacao["valor_normalizado"],
        )
    else:
        novo_estado = registrar_resposta_invalida_entrevista(state)

    novo_status = novo_estado[CREDIT_INTERVIEW_STATUS]

    if novo_status == CREDIT_INTERVIEW_INTERRUPTED:
        _persistir_estado_entrevista(session, novo_estado)
        return _MSG_ENTREVISTA_INTERROMPIDA
    if novo_status == CREDIT_INTERVIEW_READY:
        return _processar_entrevista_pronta(session, novo_estado)

    _persistir_estado_entrevista(session, novo_estado)
    proximo_campo = novo_estado[CREDIT_INTERVIEW_CURRENT_FIELD]
    pergunta = _PERGUNTAS_ENTREVISTA[proximo_campo]
    if validacao["valida"]:
        return pergunta
    return f"{validacao['erro']} Tente novamente. {pergunta}"


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
    resposta_final = ""
    resultado_cambio_encontrado = False
    resultado_cambio = None
    try:
        resposta_encerramento = _processar_encerramento_global(
            session_id,
            mensagem_usuario,
        )
        if resposta_encerramento is not None:
            return resposta_encerramento.strip()

        resposta_retorno = _recuperar_retorno_pendente(session_id)
        if resposta_retorno is not None:
            return resposta_retorno.strip()

        resposta_oferta = _processar_resposta_oferta(
            session_id,
            mensagem_usuario,
        )
        if resposta_oferta is not None:
            return resposta_oferta.strip()

        resposta_coleta = _processar_coleta_entrevista(
            session_id,
            mensagem_usuario,
        )
        if resposta_coleta is not None:
            return resposta_coleta

        content = Content(role="user", parts=[Part(text=mensagem_usuario)])
        for event in _runner.run(
            user_id=session_id,
            session_id=session_id,
            new_message=content,
        ):
            obter_respostas = getattr(event, "get_function_responses", None)
            if callable(obter_respostas):
                for resposta_tool in obter_respostas():
                    if resposta_tool.name == "buscar_cotacao":
                        resultado_cambio_encontrado = True
                        resultado_cambio = resposta_tool.response
            if event.is_final_response():
                if event.content and event.content.parts:
                    resposta_final = event.content.parts[0].text or ""
                break

        if resultado_cambio_encontrado:
            return renderizar_resultado_cotacao(resultado_cambio).strip()

        oferta_pendente = _obter_oferta_pendente(session_id)
        if oferta_pendente is not None:
            return oferta_pendente.strip()

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
        return bool(session.state.get(CONVERSATION_ENDED, False))
    except Exception as e:
        print(f"[ERRO sessao_encerrada] session={session_id} | {e}")
        return False
