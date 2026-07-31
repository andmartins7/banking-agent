"""Contrato compartilhado do estado determinístico de uma sessão."""

AUTHENTICATED = "authenticated"
AUTHENTICATED_CPF = "authenticated_cpf"
AUTH_ATTEMPTS = "auth_attempts"
CONVERSATION_ENDED = "conversation_ended"


class ErroAutorizacaoSessao(ValueError):
    """Falha controlada ao autorizar uma operação pela sessão."""


def criar_estado_inicial() -> dict[str, object]:
    """Retorna um novo estado independente para uma sessão de atendimento."""
    return {
        AUTHENTICATED: False,
        AUTHENTICATED_CPF: None,
        AUTH_ATTEMPTS: 0,
        CONVERSATION_ENDED: False,
    }


def obter_cpf_autorizado(tool_context) -> str:
    """Retorna somente o CPF validado de uma sessão autenticada e aberta."""
    if tool_context is None or not hasattr(tool_context, "state"):
        raise ErroAutorizacaoSessao("Sessão inválida para esta operação.")

    state = tool_context.state
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
