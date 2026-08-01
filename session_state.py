"""Contrato compartilhado do estado determinístico de uma sessão."""

AUTHENTICATED = "authenticated"
AUTHENTICATED_CPF = "authenticated_cpf"
AUTH_ATTEMPTS = "auth_attempts"
CONVERSATION_ENDED = "conversation_ended"
MENSAGEM_ATENDIMENTO_ENCERRADO = "Atendimento encerrado com sucesso."

CREDIT_INTERVIEW_STATUS = "credit_interview_status"
CREDIT_INTERVIEW_CURRENT_FIELD = "credit_interview_current_field"
CREDIT_INTERVIEW_RESPONSES = "credit_interview_responses"
CREDIT_INTERVIEW_ATTEMPTS = "credit_interview_attempts"
CREDIT_INTERVIEW_RETURN_PENDING = "credit_interview_return_pending"
CREDIT_INTERVIEW_REQUEST_TIMESTAMP = "credit_interview_request_timestamp"

CREDIT_INTERVIEW_NOT_OFFERED = "not_offered"
CREDIT_INTERVIEW_OFFERED = "offered"
CREDIT_INTERVIEW_COLLECTING = "collecting"
CREDIT_INTERVIEW_DECLINED = "declined"
CREDIT_INTERVIEW_READY = "ready_for_processing"
CREDIT_INTERVIEW_COMPLETED = "completed"
CREDIT_INTERVIEW_INTERRUPTED = "interrupted_by_fallback"

CREDIT_INTERVIEW_FIELDS = (
    "renda_mensal",
    "tipo_emprego",
    "despesas_fixas",
    "num_dependentes",
    "tem_dividas",
)

MAX_CREDIT_INTERVIEW_INVALID_ATTEMPTS = 2


class ErroAutorizacaoSessao(ValueError):
    """Falha controlada ao autorizar uma operação pela sessão."""


def criar_estado_inicial() -> dict[str, object]:
    """Retorna um novo estado independente para uma sessão de atendimento."""
    return {
        AUTHENTICATED: False,
        AUTHENTICATED_CPF: None,
        AUTH_ATTEMPTS: 0,
        CONVERSATION_ENDED: False,
        CREDIT_INTERVIEW_STATUS: CREDIT_INTERVIEW_NOT_OFFERED,
        CREDIT_INTERVIEW_CURRENT_FIELD: None,
        CREDIT_INTERVIEW_RESPONSES: {},
        CREDIT_INTERVIEW_ATTEMPTS: {
            campo: 0 for campo in CREDIT_INTERVIEW_FIELDS
        },
        CREDIT_INTERVIEW_RETURN_PENDING: False,
        CREDIT_INTERVIEW_REQUEST_TIMESTAMP: None,
    }


def _copiar_estado(state: dict[str, object]) -> dict[str, object]:
    """Copia o estado e suas coleções mutáveis da entrevista."""
    novo_estado = dict(state)
    novo_estado[CREDIT_INTERVIEW_RESPONSES] = dict(
        state.get(CREDIT_INTERVIEW_RESPONSES, {})
    )
    novo_estado[CREDIT_INTERVIEW_ATTEMPTS] = dict(
        state.get(CREDIT_INTERVIEW_ATTEMPTS, {})
    )
    return novo_estado


def _transicao_bloqueada(state: dict[str, object]) -> bool:
    """Indica se o encerramento global impede a transição."""
    return bool(state.get(CONVERSATION_ENDED, False))


def encerrar_estado_atendimento(
    state: dict[str, object],
) -> dict[str, object]:
    """Marca o encerramento global sem alterar os demais dados da sessão."""
    novo_estado = _copiar_estado(state)
    novo_estado[CONVERSATION_ENDED] = True
    return novo_estado


def oferecer_entrevista_credito(
    state: dict[str, object],
) -> dict[str, object]:
    """Oferece a entrevista somente a partir do estado inicial."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) == CREDIT_INTERVIEW_NOT_OFFERED:
        novo_estado[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_OFFERED
    return novo_estado


def aceitar_entrevista_credito(
    state: dict[str, object],
) -> dict[str, object]:
    """Inicia a coleta na primeira pergunta de uma entrevista oferecida."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) == CREDIT_INTERVIEW_OFFERED:
        novo_estado[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_COLLECTING
        novo_estado[CREDIT_INTERVIEW_CURRENT_FIELD] = CREDIT_INTERVIEW_FIELDS[0]
    return novo_estado


def recusar_entrevista_credito(
    state: dict[str, object],
) -> dict[str, object]:
    """Registra a recusa sem iniciar ou modificar a coleta."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) == CREDIT_INTERVIEW_OFFERED:
        novo_estado[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_DECLINED
        novo_estado[CREDIT_INTERVIEW_CURRENT_FIELD] = None
    return novo_estado


def registrar_resposta_entrevista(
    state: dict[str, object],
    resposta_validada: object,
) -> dict[str, object]:
    """Registra a resposta já validada da pergunta atual e avança a coleta."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) != CREDIT_INTERVIEW_COLLECTING:
        return novo_estado

    campo_atual = state.get(CREDIT_INTERVIEW_CURRENT_FIELD)
    if campo_atual not in CREDIT_INTERVIEW_FIELDS:
        return novo_estado

    respostas = novo_estado[CREDIT_INTERVIEW_RESPONSES]
    respostas[campo_atual] = resposta_validada
    proximo_indice = CREDIT_INTERVIEW_FIELDS.index(campo_atual) + 1

    if proximo_indice == len(CREDIT_INTERVIEW_FIELDS):
        novo_estado[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_READY
        novo_estado[CREDIT_INTERVIEW_CURRENT_FIELD] = None
    else:
        novo_estado[CREDIT_INTERVIEW_CURRENT_FIELD] = (
            CREDIT_INTERVIEW_FIELDS[proximo_indice]
        )
    return novo_estado


def registrar_resposta_invalida_entrevista(
    state: dict[str, object],
) -> dict[str, object]:
    """Conta uma invalidade e interrompe a entrevista na segunda tentativa."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) != CREDIT_INTERVIEW_COLLECTING:
        return novo_estado

    campo_atual = state.get(CREDIT_INTERVIEW_CURRENT_FIELD)
    if campo_atual not in CREDIT_INTERVIEW_FIELDS:
        return novo_estado

    tentativas = novo_estado[CREDIT_INTERVIEW_ATTEMPTS]
    quantidade = min(
        MAX_CREDIT_INTERVIEW_INVALID_ATTEMPTS,
        int(tentativas.get(campo_atual, 0)) + 1,
    )
    tentativas[campo_atual] = quantidade
    if quantidade == MAX_CREDIT_INTERVIEW_INVALID_ATTEMPTS:
        novo_estado[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_INTERRUPTED
    return novo_estado


def concluir_processamento_entrevista(
    state: dict[str, object],
) -> dict[str, object]:
    """Conclui uma entrevista pronta uma única vez e solicita o retorno."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) == CREDIT_INTERVIEW_READY:
        novo_estado[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_COMPLETED
        novo_estado[CREDIT_INTERVIEW_RETURN_PENDING] = True
    return novo_estado


def consumir_retorno_entrevista(
    state: dict[str, object],
    status_pedido: str,
) -> dict[str, object]:
    """Consome o retorno somente após uma reanálise terminal identificada."""
    novo_estado = _copiar_estado(state)
    if _transicao_bloqueada(state):
        return novo_estado
    if state.get(CREDIT_INTERVIEW_STATUS) != CREDIT_INTERVIEW_COMPLETED:
        return novo_estado
    if not bool(state.get(CREDIT_INTERVIEW_RETURN_PENDING, False)):
        return novo_estado
    timestamp = state.get(CREDIT_INTERVIEW_REQUEST_TIMESTAMP)
    if not isinstance(timestamp, str) or not timestamp.strip():
        return novo_estado
    if status_pedido not in {"aprovado", "rejeitado"}:
        return novo_estado

    novo_estado[CREDIT_INTERVIEW_RETURN_PENDING] = False
    novo_estado[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = None
    return novo_estado


def obter_cpf_autorizado_do_estado(state) -> str:
    """Retorna o CPF validado diretamente do estado autenticado e aberto."""
    if state is None or not hasattr(state, "get"):
        raise ErroAutorizacaoSessao("Sessão inválida para esta operação.")

    if bool(state.get(CONVERSATION_ENDED, False)):
        raise ErroAutorizacaoSessao("Este atendimento já foi encerrado.")

    if not bool(state.get(AUTHENTICATED, False)):
        raise ErroAutorizacaoSessao("Autenticação necessária para esta operação.")

    cpf = state.get(AUTHENTICATED_CPF)
    if not isinstance(cpf, str) or len(cpf) != 11 or not cpf.isdigit():
        raise ErroAutorizacaoSessao("Identidade da sessão inválida.")

    return cpf


def obter_cpf_autorizado(tool_context) -> str:
    """Retorna somente o CPF validado de uma sessão autenticada e aberta."""
    if tool_context is None or not hasattr(tool_context, "state"):
        raise ErroAutorizacaoSessao("Sessão inválida para esta operação.")
    return obter_cpf_autorizado_do_estado(tool_context.state)
