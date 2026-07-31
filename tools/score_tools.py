"""
Ferramentas de score de crédito do Banco Ágil.

Funções expostas como tools do Google ADK:
    - calcular_score
    - atualizar_score_cliente
"""

import pandas as pd
from google.adk.tools.tool_context import ToolContext

from config import (
    CSV_CLIENTES,
    PESO_RENDA,
    PESO_EMPREGO,
    PESO_DEPENDENTES,
    PESO_DIVIDAS,
    SCORE_MIN,
    SCORE_MAX,
)
from session_state import ErroAutorizacaoSessao, obter_cpf_autorizado


# ── Helpers de normalização ────────────────────────────────────────────────

def _normalizar_tipo_emprego(tipo: str) -> str:
    """
    Normaliza variações de tipo de emprego para as chaves do dicionário PESO_EMPREGO.

    Exemplos:
        "CLT", "formal", "empregado" → "formal"
        "autônomo", "freelancer", "MEI" → "autonomo"
        "desempregado", "sem emprego" → "desempregado"
    """
    t = tipo.strip().lower()
    formais = {"formal", "clt", "empregado", "efetivado", "registrado"}
    autonomos = {"autonomo", "autônomo", "freelancer", "mei", "free", "autonomo"}
    if t in formais:
        return "formal"
    if t in autonomos:
        return "autonomo"
    # Qualquer outra string → desempregado (fallback conservador)
    return "desempregado"


def _normalizar_tem_dividas(resposta: str) -> str:
    """
    Normaliza variações de sim/não para as chaves do dicionário PESO_DIVIDAS.

    Exemplos:
        "sim", "s", "yes", "tenho" → "sim"
        "não", "nao", "n", "no", "não tenho" → "nao"
    """
    r = resposta.strip().lower()
    positivos = {"sim", "s", "yes", "y", "tenho", "possuo", "true", "1"}
    if r in positivos or r.startswith("sim") or r.startswith("tenho"):
        return "sim"
    return "nao"


# ── Tools ──────────────────────────────────────────────────────────────────

def calcular_score(
    renda_mensal: float,
    tipo_emprego: str,
    despesas_fixas: float,
    num_dependentes: int,
    tem_dividas: str,
    tool_context: ToolContext,
) -> dict:
    """
    Calcula o novo score de crédito com base nos dados financeiros coletados na entrevista.

    Fórmula:
        score_raw = (renda_mensal / (despesas_fixas + 1)) * PESO_RENDA
                    + PESO_EMPREGO[tipo_emprego]
                    + PESO_DEPENDENTES[min(num_dependentes, 3)]
                    + PESO_DIVIDAS[tem_dividas]
        score_final = clamp(round(score_raw), 0, 1000)

    Args:
        renda_mensal: Renda mensal em R$ (>= 0).
        tipo_emprego: Situação de emprego (normalizado internamente).
        despesas_fixas: Despesas fixas mensais em R$ (>= 0).
        num_dependentes: Número de dependentes financeiros (>= 0).
        tem_dividas: Se possui dívidas ativas — normalizado internamente.
        tool_context: Contexto ADK da sessão autenticada.

    Returns:
        dict com:
            score (int): novo score calculado (0-1000).
            detalhes (dict): componentes do cálculo (uso interno/debug).
            erro (str | None).
    """
    try:
        obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return {
            "score": SCORE_MIN,
            "detalhes": {},
            "erro": str(e),
        }

    try:
        # Normalizar inputs
        emprego_key   = _normalizar_tipo_emprego(str(tipo_emprego))
        dividas_key   = _normalizar_tem_dividas(str(tem_dividas))
        dep_key        = min(int(num_dependentes), 3)
        renda          = max(float(renda_mensal), 0.0)
        despesas       = max(float(despesas_fixas), 0.0)

        # Componentes
        comp_renda      = (renda / (despesas + 1)) * PESO_RENDA
        comp_emprego    = PESO_EMPREGO[emprego_key]
        comp_dependentes = PESO_DEPENDENTES[dep_key]
        comp_dividas    = PESO_DIVIDAS[dividas_key]

        score_raw   = comp_renda + comp_emprego + comp_dependentes + comp_dividas
        score_final = max(SCORE_MIN, min(SCORE_MAX, round(score_raw)))

        return {
            "score": score_final,
            "detalhes": {
                "componente_renda": round(comp_renda, 2),
                "componente_emprego": comp_emprego,
                "componente_dependentes": comp_dependentes,
                "componente_dividas": comp_dividas,
                "score_raw": round(score_raw, 2),
                "emprego_normalizado": emprego_key,
                "dividas_normalizado": dividas_key,
            },
            "erro": None,
        }

    except Exception as e:
        print(f"[TOOL ERROR] calcular_score: {type(e).__name__}")
        return {
            "score": SCORE_MIN,
            "detalhes": {},
            "erro": "Erro ao calcular score. Tente novamente.",
        }


def atualizar_score_cliente(
    novo_score: int,
    tool_context: ToolContext,
) -> dict:
    """
    Atualiza o score de crédito do cliente em clientes.csv.

    Args:
        novo_score: Novo score calculado (0-1000).
        tool_context: Contexto ADK da sessão autenticada.

    Returns:
        dict com:
            atualizado (bool).
            score_anterior (int): score antes da atualização.
            score_novo (int): score após a atualização.
            erro (str | None).
    """
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return {
            "atualizado": False,
            "score_anterior": 0,
            "score_novo": 0,
            "erro": str(e),
        }

    try:
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        mascara = df["cpf"] == cpf
        if not mascara.any():
            return {
                "atualizado": False,
                "score_anterior": 0,
                "score_novo": 0,
                "erro": "Cliente autenticado não encontrado.",
            }

        score_anterior = int(df.loc[mascara, "score_credito"].iloc[0])

        # Garantir que o score está no intervalo válido
        score_valido = max(SCORE_MIN, min(SCORE_MAX, int(novo_score)))
        df.loc[mascara, "score_credito"] = str(score_valido)
        df.to_csv(CSV_CLIENTES, index=False)

        return {
            "atualizado": True,
            "score_anterior": score_anterior,
            "score_novo": score_valido,
            "erro": None,
        }

    except FileNotFoundError:
        return {
            "atualizado": False,
            "score_anterior": 0,
            "score_novo": 0,
            "erro": "Base de clientes não encontrada.",
        }
    except Exception as e:
        print(f"[TOOL ERROR] atualizar_score_cliente: {type(e).__name__}")
        return {
            "atualizado": False,
            "score_anterior": 0,
            "score_novo": 0,
            "erro": "Erro ao atualizar score. Tente novamente.",
        }
