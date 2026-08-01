"""Ferramentas determinísticas para a entrevista de crédito do Banco Ágil.

O agente expõe somente ``processar_entrevista_credito``. As funções
``calcular_score`` e ``atualizar_score_cliente`` permanecem disponíveis apenas
para compatibilidade interna com integrações anteriores.
"""

import math
import os
import tempfile
from pathlib import Path

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


_CAMPOS_CLIENTES_OBRIGATORIOS = {"cpf", "score_credito"}


class _ErroCampoEntrevista(ValueError):
    """Falha controlada associada a uma resposta específica da entrevista."""

    def __init__(self, campo: str, mensagem: str):
        super().__init__(mensagem)
        self.campo = campo


# ── Helpers de normalização ────────────────────────────────────────────────

def _normalizar_tipo_emprego(tipo: str) -> str:
    """
    Normaliza variações de tipo de emprego para as chaves do dicionário PESO_EMPREGO.

    Exemplos:
        "CLT", "formal", "empregado" → "formal"
        "autônomo", "freelancer", "MEI" → "autonomo"
        "desempregado", "sem emprego" → "desempregado"
    """
    return _normalizar_emprego_estrito(tipo)


def _normalizar_tem_dividas(resposta: str) -> str:
    """
    Normaliza variações de sim/não para as chaves do dicionário PESO_DIVIDAS.

    Exemplos:
        "sim", "s", "yes", "tenho" → "sim"
        "não", "nao", "n", "no", "não tenho" → "nao"
    """
    return _normalizar_dividas_estrito(resposta)


def _validar_numero_nao_negativo(valor: object, campo: str) -> float:
    """Valida um número real, finito e não negativo sem aceitar booleanos."""
    if valor is None or isinstance(valor, bool):
        raise _ErroCampoEntrevista(
            campo,
            f"Informe um valor numérico válido para {campo}.",
        )

    try:
        numero = float(valor)
    except (TypeError, ValueError) as e:
        raise _ErroCampoEntrevista(
            campo,
            f"Informe um valor numérico válido para {campo}.",
        ) from e
    if not math.isfinite(numero) or numero < 0:
        raise _ErroCampoEntrevista(
            campo,
            f"Informe um valor finito e não negativo para {campo}.",
        )
    return numero


def _validar_dependentes(valor: object) -> int:
    """Valida dependentes como inteiro não negativo, inclusive texto numérico."""
    if valor is None or isinstance(valor, bool):
        raise _ErroCampoEntrevista(
            "num_dependentes",
            "Informe um número inteiro válido de dependentes.",
        )

    try:
        numero = float(valor)
    except (TypeError, ValueError) as e:
        raise _ErroCampoEntrevista(
            "num_dependentes",
            "Informe um número inteiro válido de dependentes.",
        ) from e
    if not math.isfinite(numero) or numero < 0 or not numero.is_integer():
        raise _ErroCampoEntrevista(
            "num_dependentes",
            "Informe um número inteiro não negativo de dependentes.",
        )
    return int(numero)


def _normalizar_emprego_estrito(valor: object) -> str:
    """Normaliza somente respostas de emprego reconhecidas pelo contrato."""
    if not isinstance(valor, str):
        raise _ErroCampoEntrevista(
            "tipo_emprego",
            "Informe uma situação de emprego válida.",
        )

    resposta = " ".join(valor.strip().lower().split())
    aliases = {
        "formal": "formal",
        "clt": "formal",
        "empregado": "formal",
        "registrado": "formal",
        "carteira assinada": "formal",
        "autonomo": "autonomo",
        "autônomo": "autonomo",
        "mei": "autonomo",
        "freelancer": "autonomo",
        "desempregado": "desempregado",
        "sem emprego": "desempregado",
    }
    normalizado = aliases.get(resposta)
    if normalizado is None:
        raise _ErroCampoEntrevista(
            "tipo_emprego",
            "Situação de emprego não reconhecida.",
        )
    return normalizado


def _normalizar_dividas_estrito(valor: object) -> str:
    """Normaliza somente respostas explícitas e reconhecidas sobre dívidas."""
    if not isinstance(valor, str):
        raise _ErroCampoEntrevista(
            "tem_dividas",
            "Informe se possui dívidas usando uma resposta reconhecida.",
        )

    resposta = " ".join(valor.strip().lower().split())
    positivos = {"sim", "s", "tenho", "possuo", "yes"}
    negativos = {"não", "nao", "n", "não tenho", "nao tenho", "no"}
    if resposta in positivos:
        return "sim"
    if resposta in negativos:
        return "nao"
    raise _ErroCampoEntrevista(
        "tem_dividas",
        "Resposta sobre dívidas não reconhecida.",
    )


_VALIDADORES_RESPOSTA_ENTREVISTA = {
    "renda_mensal": lambda valor: _validar_numero_nao_negativo(
        valor,
        "renda_mensal",
    ),
    "tipo_emprego": _normalizar_emprego_estrito,
    "despesas_fixas": lambda valor: _validar_numero_nao_negativo(
        valor,
        "despesas_fixas",
    ),
    "num_dependentes": _validar_dependentes,
    "tem_dividas": _normalizar_dividas_estrito,
}


def validar_resposta_entrevista(campo: str, valor_bruto: object) -> dict:
    """Valida e normaliza uma única resposta financeira, sem efeitos externos."""
    if (
        not isinstance(campo, str)
        or campo not in _VALIDADORES_RESPOSTA_ENTREVISTA
    ):
        return {
            "valida": False,
            "valor_normalizado": None,
            "erro": "Campo da entrevista desconhecido.",
        }

    try:
        valor_normalizado = _VALIDADORES_RESPOSTA_ENTREVISTA[campo](valor_bruto)
    except _ErroCampoEntrevista as e:
        return {
            "valida": False,
            "valor_normalizado": None,
            "erro": str(e),
        }

    return {
        "valida": True,
        "valor_normalizado": valor_normalizado,
        "erro": None,
    }


def _calcular_score_oficial(
    renda_mensal: float,
    tipo_emprego: str,
    despesas_fixas: float,
    num_dependentes: int,
    tem_dividas: str,
) -> int:
    """Aplica a fórmula e os pesos oficiais sem expor componentes ao agente."""
    score_raw = (
        (renda_mensal / (despesas_fixas + 1)) * PESO_RENDA
        + PESO_EMPREGO[tipo_emprego]
        + PESO_DEPENDENTES[min(num_dependentes, 3)]
        + PESO_DIVIDAS[tem_dividas]
    )
    return max(SCORE_MIN, min(SCORE_MAX, round(score_raw)))


def _score_persistido_valido(valor: object) -> bool:
    """Confirma que o score anterior é inteiro e pertence ao intervalo oficial."""
    try:
        numero = float(str(valor).strip())
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(numero)
        and numero.is_integer()
        and SCORE_MIN <= numero <= SCORE_MAX
    )


def _escrever_clientes_atomico(dataframe: pd.DataFrame, destino: Path) -> None:
    """Publica clientes.csv por substituição atômica no mesmo diretório."""
    destino = Path(destino)
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=destino.parent,
            prefix=f".{destino.name}.",
            suffix=".tmp",
            delete=False,
        ) as arquivo:
            temporario = Path(arquivo.name)
            dataframe.to_csv(arquivo, index=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    finally:
        if temporario is not None and temporario.exists():
            temporario.unlink()


def _resultado_entrevista_erro(
    mensagem: str,
    campo_invalido: str | None = None,
) -> dict:
    """Cria o retorno público controlado de uma entrevista não processada."""
    return {
        "processado": False,
        "perfil_atualizado": False,
        "retornar_credito": False,
        "campo_invalido": campo_invalido,
        "erro": mensagem,
    }


def processar_entrevista_credito(
    renda_mensal: float,
    tipo_emprego: str,
    despesas_fixas: float,
    num_dependentes: int,
    tem_dividas: str,
    tool_context: ToolContext,
) -> dict:
    """Valida a entrevista, calcula o score e publica o perfil atualizado."""
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return _resultado_entrevista_erro(str(e))

    return processar_entrevista_credito_autorizada(
        cpf=cpf,
        renda_mensal=renda_mensal,
        tipo_emprego=tipo_emprego,
        despesas_fixas=despesas_fixas,
        num_dependentes=num_dependentes,
        tem_dividas=tem_dividas,
    )


def processar_entrevista_credito_autorizada(
    cpf: str,
    renda_mensal: float,
    tipo_emprego: str,
    despesas_fixas: float,
    num_dependentes: int,
    tem_dividas: str,
) -> dict:
    """Executa o processamento financeiro para uma identidade já autorizada."""
    respostas_brutas = {
        "renda_mensal": renda_mensal,
        "tipo_emprego": tipo_emprego,
        "despesas_fixas": despesas_fixas,
        "num_dependentes": num_dependentes,
        "tem_dividas": tem_dividas,
    }
    respostas_normalizadas = {}
    for campo, valor_bruto in respostas_brutas.items():
        validacao = validar_resposta_entrevista(campo, valor_bruto)
        if not validacao["valida"]:
            return _resultado_entrevista_erro(validacao["erro"], campo)
        respostas_normalizadas[campo] = validacao["valor_normalizado"]

    score_final = _calcular_score_oficial(
        respostas_normalizadas["renda_mensal"],
        respostas_normalizadas["tipo_emprego"],
        respostas_normalizadas["despesas_fixas"],
        respostas_normalizadas["num_dependentes"],
        respostas_normalizadas["tem_dividas"],
    )

    try:
        destino = Path(CSV_CLIENTES)
        if not destino.is_file():
            raise FileNotFoundError

        clientes = pd.read_csv(destino, dtype=str)
        if not _CAMPOS_CLIENTES_OBRIGATORIOS.issubset(clientes.columns):
            raise ValueError("CSV de clientes malformado")

        cpfs = clientes["cpf"].fillna("").astype(str).str.strip()
        mascara = cpfs == cpf
        quantidade = int(mascara.sum())
        if quantidade == 0:
            return _resultado_entrevista_erro(
                "Cliente autenticado não encontrado.",
            )
        if quantidade != 1:
            return _resultado_entrevista_erro(
                "Cadastro duplicado para o cliente autenticado.",
            )

        score_anterior = clientes.loc[mascara, "score_credito"].iloc[0]
        if not _score_persistido_valido(score_anterior):
            raise ValueError("CSV de clientes malformado")

        clientes.loc[mascara, "score_credito"] = str(score_final)
        _escrever_clientes_atomico(clientes, destino)
    except FileNotFoundError:
        return _resultado_entrevista_erro(
            "Base de clientes não encontrada.",
        )
    except (KeyError, pd.errors.EmptyDataError, pd.errors.ParserError, ValueError):
        return _resultado_entrevista_erro(
            "Base de clientes inválida para atualização do perfil.",
        )
    except Exception as e:
        print(
            "[TOOL ERROR] processar_entrevista_credito: "
            f"{type(e).__name__}"
        )
        return _resultado_entrevista_erro(
            "Não foi possível atualizar o perfil. Tente novamente.",
        )

    return {
        "processado": True,
        "perfil_atualizado": True,
        "retornar_credito": True,
        "campo_invalido": None,
        "erro": None,
    }


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
