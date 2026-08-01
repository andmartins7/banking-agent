"""
Ferramentas de autenticação do Banco Ágil.

Funções expostas como tools do Google ADK:
    - autenticar_cliente
    - encerrar_atendimento
"""

import pandas as pd
from google.adk.tools.tool_context import ToolContext

from config import CSV_CLIENTES, MAX_AUTH_ATTEMPTS
from session_state import (
    AUTH_ATTEMPTS,
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    MENSAGEM_ATENDIMENTO_ENCERRADO,
    encerrar_estado_atendimento,
)


def _numero_tentativas(state) -> int:
    """Lê o contador defensivamente e o mantém no intervalo permitido."""
    try:
        return max(0, min(MAX_AUTH_ATTEMPTS, int(state.get(AUTH_ATTEMPTS, 0))))
    except (TypeError, ValueError):
        return 0


def _status_tentativas(state, *, encerrado: bool | None = None) -> dict:
    tentativas = _numero_tentativas(state)
    atendimento_encerrado = (
        bool(state.get(CONVERSATION_ENDED, False))
        if encerrado is None
        else encerrado
    )
    esgotadas = tentativas >= MAX_AUTH_ATTEMPTS
    return {
        "tentativas_realizadas": tentativas,
        "tentativas_restantes": (
            0
            if atendimento_encerrado
            else max(0, MAX_AUTH_ATTEMPTS - tentativas)
        ),
        "tentativas_esgotadas": esgotadas,
        "encerrado": atendimento_encerrado,
    }


def _registrar_falha_credencial(tool_context: ToolContext) -> dict:
    """Registra exatamente uma falha e encerra ao atingir o limite."""
    state = tool_context.state
    tentativas = min(MAX_AUTH_ATTEMPTS, _numero_tentativas(state) + 1)
    esgotadas = tentativas >= MAX_AUTH_ATTEMPTS

    state[AUTHENTICATED] = False
    state[AUTHENTICATED_CPF] = None
    state[AUTH_ATTEMPTS] = tentativas
    state[CONVERSATION_ENDED] = esgotadas

    return {
        "autenticado": False,
        "cliente": None,
        "erro": None,
        "ja_autenticado": False,
        **_status_tentativas(state),
    }


def _resultado_erro_tecnico(tool_context: ToolContext, mensagem: str) -> dict:
    """Retorna falha técnica sem consumir uma tentativa de credencial."""
    return {
        "autenticado": False,
        "cliente": None,
        "erro": mensagem,
        "ja_autenticado": False,
        **_status_tentativas(tool_context.state),
    }


def autenticar_cliente(
    cpf: str,
    data_nascimento: str,
    tool_context: ToolContext,
) -> dict:
    """
    Autentica um cliente verificando CPF e data de nascimento contra clientes.csv.

    Args:
        cpf: CPF do cliente. Aceita formatações diversas (com pontos, traços ou espaços);
             normalizado internamente para somente dígitos.
        data_nascimento: Data de nascimento no formato DD/MM/AAAA.
        tool_context: Contexto ADK com o estado determinístico da sessão.

    Returns:
        dict com:
            autenticado (bool): True se os dados conferem.
            cliente (dict | None): {cpf, nome, score_credito, limite_credito} se autenticado.
            erro (str | None): mensagem de erro técnico, se houver.
    """
    state = tool_context.state

    # Estado encerrado e autenticação prévia são barreiras determinísticas.
    if bool(state.get(CONVERSATION_ENDED, False)):
        return {
            "autenticado": False,
            "cliente": None,
            "erro": None,
            "ja_autenticado": False,
            "mensagem": "Este atendimento já foi encerrado.",
            **_status_tentativas(state, encerrado=True),
        }

    if bool(state.get(AUTHENTICATED, False)):
        return {
            "autenticado": True,
            "cliente": None,
            "erro": None,
            "ja_autenticado": True,
            "mensagem": "Cliente já autenticado nesta sessão.",
            **_status_tentativas(state),
        }

    try:
        # 1. Normalizar CPF — manter apenas dígitos
        cpf_normalizado = "".join(filter(str.isdigit, str(cpf or "")))

        if len(cpf_normalizado) != 11:
            return _registrar_falha_credencial(tool_context)

        # 2. Ler base de clientes
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        # 3. Buscar por CPF
        linha = df[df["cpf"] == cpf_normalizado]
        if linha.empty:
            return _registrar_falha_credencial(tool_context)

        # 4. Validar data de nascimento
        data_base = linha.iloc[0]["data_nascimento"]
        data_input = str(data_nascimento or "").strip()

        if data_base != data_input:
            return _registrar_falha_credencial(tool_context)

        # 5. Validar todos os dados antes de alterar o estado da sessão
        row = linha.iloc[0]
        cliente = {
            "cpf": row["cpf"],
            "nome": row["nome"],
            "score_credito": int(row["score_credito"]),
            "limite_credito": float(row["limite_credito"]),
        }

        # 6. Autenticado — vincular a identidade normalizada à sessão
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf_normalizado
        state[AUTH_ATTEMPTS] = 0
        state[CONVERSATION_ENDED] = False

        return {
            "autenticado": True,
            "cliente": cliente,
            "erro": None,
            "ja_autenticado": False,
            **_status_tentativas(state),
        }

    except FileNotFoundError:
        return _resultado_erro_tecnico(
            tool_context,
            "Base de clientes não encontrada. Por favor, contate o suporte.",
        )
    except Exception as e:
        print(f"[TOOL ERROR] autenticar_cliente: {type(e).__name__}")
        return _resultado_erro_tecnico(
            tool_context,
            "Erro interno na autenticação. Tente novamente.",
        )


def encerrar_atendimento(tool_context: ToolContext) -> dict:
    """
    Sinaliza o encerramento do atendimento ao cliente.

    O Streamlit monitora este sinal via sessao_encerrada() no orchestrator
    para desabilitar o campo de input após o encerramento.

    Returns:
        dict com:
            encerrado (bool): sempre True.
            mensagem (str): confirmação do encerramento.
    """
    estado_encerrado = encerrar_estado_atendimento(tool_context.state)
    tool_context.state[CONVERSATION_ENDED] = estado_encerrado[
        CONVERSATION_ENDED
    ]
    return {
        "encerrado": True,
        "mensagem": MENSAGEM_ATENDIMENTO_ENCERRADO,
    }
