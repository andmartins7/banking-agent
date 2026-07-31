"""
Ferramentas de crédito do Banco Ágil.

Funções expostas como tools do Google ADK:
    - consultar_limite
    - registrar_solicitacao
    - checar_score_para_limite
    - atualizar_status_solicitacao
    - atualizar_limite_cliente
"""

import pandas as pd
from datetime import datetime, timezone
from config import CSV_CLIENTES, CSV_SCORE_LIMITE, CSV_SOLICITACOES


# ── Helpers ────────────────────────────────────────────────────────────────

def _garantir_csv_solicitacoes() -> None:
    """Cria o CSV de solicitações com cabeçalho se ainda não existir."""
    if not CSV_SOLICITACOES.exists():
        CSV_SOLICITACOES.parent.mkdir(parents=True, exist_ok=True)
        colunas = [
            "cpf_cliente",
            "data_hora_solicitacao",
            "limite_atual",
            "novo_limite_solicitado",
            "status_pedido",
        ]
        pd.DataFrame(columns=colunas).to_csv(CSV_SOLICITACOES, index=False)


# ── Tools ──────────────────────────────────────────────────────────────────

def consultar_limite(cpf: str) -> dict:
    """
    Consulta o limite de crédito atual e o score do cliente.

    Args:
        cpf: CPF do cliente (11 dígitos, sem formatação).

    Returns:
        dict com:
            limite_atual (float): limite de crédito atual em R$.
            score_credito (int): score de crédito atual (0-1000).
            erro (str | None): mensagem de erro se houver.
    """
    try:
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        linha = df[df["cpf"] == cpf.strip()]
        if linha.empty:
            return {
                "limite_atual": 0.0,
                "score_credito": 0,
                "erro": f"Cliente com CPF {cpf} não encontrado.",
            }

        row = linha.iloc[0]
        return {
            "limite_atual": float(row["limite_credito"]),
            "score_credito": int(row["score_credito"]),
            "erro": None,
        }

    except FileNotFoundError:
        return {
            "limite_atual": 0.0,
            "score_credito": 0,
            "erro": "Base de clientes não encontrada.",
        }
    except Exception as e:
        print(f"[TOOL ERROR] consultar_limite: {type(e).__name__}: {e}")
        return {
            "limite_atual": 0.0,
            "score_credito": 0,
            "erro": "Erro ao consultar limite. Tente novamente.",
        }


def registrar_solicitacao(
    cpf: str,
    limite_atual: float,
    novo_limite_solicitado: float,
    status_pedido: str = "pendente",
) -> dict:
    """
    Registra uma solicitação de aumento de limite no CSV de solicitações.

    Cria o arquivo automaticamente caso não exista.
    Sempre faz append — preserva histórico de solicitações.

    Args:
        cpf: CPF do cliente.
        limite_atual: Limite de crédito atual do cliente.
        novo_limite_solicitado: Novo limite desejado pelo cliente.
        status_pedido: Status inicial da solicitação (padrão: 'pendente').

    Returns:
        dict com:
            data_hora (str): timestamp ISO 8601 da criação (chave para atualização posterior).
            erro (str | None).
    """
    try:
        _garantir_csv_solicitacoes()

        data_hora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        nova_linha = pd.DataFrame([{
            "cpf_cliente": cpf.strip(),
            "data_hora_solicitacao": data_hora,
            "limite_atual": float(limite_atual),
            "novo_limite_solicitado": float(novo_limite_solicitado),
            "status_pedido": status_pedido,
        }])

        nova_linha.to_csv(CSV_SOLICITACOES, mode="a", header=False, index=False)

        return {"data_hora": data_hora, "erro": None}

    except Exception as e:
        print(f"[TOOL ERROR] registrar_solicitacao: {type(e).__name__}: {e}")
        return {
            "data_hora": "",
            "erro": "Erro ao registrar solicitação. Tente novamente.",
        }


def checar_score_para_limite(score_cliente: int, novo_limite: float) -> dict:
    """
    Verifica se o score do cliente é suficiente para o novo limite solicitado.

    Consulta score_limite.csv para encontrar a faixa correspondente ao limite
    e compara com o score atual do cliente.

    Args:
        score_cliente: Score de crédito atual do cliente (0-1000).
        novo_limite: Novo limite de crédito solicitado em R$.

    Returns:
        dict com:
            aprovado (bool): True se score >= score mínimo da faixa.
            score_minimo_necessario (int): score mínimo exigido para esta faixa.
            erro (str | None).
    """
    try:
        df = pd.read_csv(CSV_SCORE_LIMITE, dtype=str)
        df.columns = df.columns.str.strip()
        df["limite_maximo"] = df["limite_maximo"].str.strip().astype(float)
        df["score_minimo"]  = df["score_minimo"].str.strip().astype(int)

        # Ordenar crescente e buscar primeira faixa que cobre o novo limite
        df_sorted = df.sort_values("limite_maximo").reset_index(drop=True)
        faixa = df_sorted[df_sorted["limite_maximo"] >= float(novo_limite)]

        if faixa.empty:
            # Novo limite excede todas as faixas — usar o score mínimo da última faixa
            score_minimo = int(df_sorted.iloc[-1]["score_minimo"])
        else:
            score_minimo = int(faixa.iloc[0]["score_minimo"])

        aprovado = score_cliente >= score_minimo

        return {
            "aprovado": aprovado,
            "score_minimo_necessario": score_minimo,
            "erro": None,
        }

    except FileNotFoundError:
        return {
            "aprovado": False,
            "score_minimo_necessario": 1000,
            "erro": "Tabela de score não encontrada.",
        }
    except Exception as e:
        print(f"[TOOL ERROR] checar_score_para_limite: {type(e).__name__}: {e}")
        return {
            "aprovado": False,
            "score_minimo_necessario": 1000,
            "erro": "Erro ao verificar score. Tente novamente.",
        }


def atualizar_status_solicitacao(
    cpf: str,
    data_hora_solicitacao: str,
    novo_status: str,
) -> dict:
    """
    Atualiza o status de uma solicitação existente em solicitacoes_aumento_limite.csv.

    Args:
        cpf: CPF do cliente.
        data_hora_solicitacao: Timestamp ISO 8601 da solicitação (chave de busca).
        novo_status: Novo status — 'aprovado' ou 'rejeitado'.

    Returns:
        dict com:
            atualizado (bool).
            erro (str | None).
    """
    try:
        _garantir_csv_solicitacoes()

        df = pd.read_csv(CSV_SOLICITACOES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        mascara = (
            (df["cpf_cliente"] == cpf.strip()) &
            (df["data_hora_solicitacao"] == data_hora_solicitacao.strip())
        )

        if not mascara.any():
            return {
                "atualizado": False,
                "erro": f"Solicitação não encontrada para CPF {cpf} em {data_hora_solicitacao}.",
            }

        df.loc[mascara, "status_pedido"] = novo_status
        df.to_csv(CSV_SOLICITACOES, index=False)

        return {"atualizado": True, "erro": None}

    except Exception as e:
        print(f"[TOOL ERROR] atualizar_status_solicitacao: {type(e).__name__}: {e}")
        return {
            "atualizado": False,
            "erro": "Erro ao atualizar status da solicitação.",
        }


def atualizar_limite_cliente(cpf: str, novo_limite: float) -> dict:
    """
    Atualiza o limite de crédito do cliente em clientes.csv após aprovação.

    Args:
        cpf: CPF do cliente.
        novo_limite: Novo limite aprovado em R$.

    Returns:
        dict com:
            atualizado (bool).
            erro (str | None).
    """
    try:
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        mascara = df["cpf"] == cpf.strip()
        if not mascara.any():
            return {
                "atualizado": False,
                "erro": f"Cliente com CPF {cpf} não encontrado para atualização.",
            }

        df.loc[mascara, "limite_credito"] = str(float(novo_limite))
        df.to_csv(CSV_CLIENTES, index=False)

        return {"atualizado": True, "erro": None}

    except Exception as e:
        print(f"[TOOL ERROR] atualizar_limite_cliente: {type(e).__name__}: {e}")
        return {
            "atualizado": False,
            "erro": "Erro ao atualizar limite do cliente. Tente novamente.",
        }
