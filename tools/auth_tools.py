"""
Ferramentas de autenticação do Banco Ágil.

Funções expostas como tools do Google ADK:
    - autenticar_cliente
    - encerrar_atendimento
"""

import pandas as pd
from config import CSV_CLIENTES


def autenticar_cliente(cpf: str, data_nascimento: str) -> dict:
    """
    Autentica um cliente verificando CPF e data de nascimento contra clientes.csv.

    Args:
        cpf: CPF do cliente. Aceita formatações diversas (com pontos, traços ou espaços);
             normalizado internamente para somente dígitos.
        data_nascimento: Data de nascimento no formato DD/MM/AAAA.

    Returns:
        dict com:
            autenticado (bool): True se os dados conferem.
            cliente (dict | None): {cpf, nome, score_credito, limite_credito} se autenticado.
            erro (str | None): mensagem de erro técnico, se houver.
    """
    try:
        # 1. Normalizar CPF — manter apenas dígitos
        cpf_normalizado = "".join(filter(str.isdigit, cpf))

        if len(cpf_normalizado) != 11:
            return {
                "autenticado": False,
                "cliente": None,
                "erro": None,
            }

        # 2. Ler base de clientes
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        # 3. Buscar por CPF
        linha = df[df["cpf"] == cpf_normalizado]
        if linha.empty:
            return {"autenticado": False, "cliente": None, "erro": None}

        # 4. Validar data de nascimento
        data_base = linha.iloc[0]["data_nascimento"]
        data_input = data_nascimento.strip()

        if data_base != data_input:
            return {"autenticado": False, "cliente": None, "erro": None}

        # 5. Autenticado — retornar dados do cliente
        row = linha.iloc[0]
        return {
            "autenticado": True,
            "cliente": {
                "cpf": row["cpf"],
                "nome": row["nome"],
                "score_credito": int(row["score_credito"]),
                "limite_credito": float(row["limite_credito"]),
            },
            "erro": None,
        }

    except FileNotFoundError:
        return {
            "autenticado": False,
            "cliente": None,
            "erro": "Base de clientes não encontrada. Por favor, contate o suporte.",
        }
    except Exception as e:
        print(f"[TOOL ERROR] autenticar_cliente: {type(e).__name__}: {e}")
        return {
            "autenticado": False,
            "cliente": None,
            "erro": "Erro interno na autenticação. Tente novamente.",
        }


def encerrar_atendimento() -> dict:
    """
    Sinaliza o encerramento do atendimento ao cliente.

    O Streamlit monitora este sinal via sessao_encerrada() no orchestrator
    para desabilitar o campo de input após o encerramento.

    Returns:
        dict com:
            encerrado (bool): sempre True.
            mensagem (str): confirmação do encerramento.
    """
    return {
        "encerrado": True,
        "mensagem": "Atendimento encerrado com sucesso.",
    }
