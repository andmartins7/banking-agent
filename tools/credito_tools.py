"""
Ferramentas de crédito do Banco Ágil.

Funções expostas como tools do Google ADK:
    - consultar_limite
    - registrar_solicitacao
    - processar_solicitacao
"""

import math
import os
import tempfile
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from google.adk.tools.tool_context import ToolContext

from config import CSV_CLIENTES, CSV_SCORE_LIMITE, CSV_SOLICITACOES
from session_state import (
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_STATUS,
    ErroAutorizacaoSessao,
    obter_cpf_autorizado,
    oferecer_entrevista_credito,
)


# ── Helpers ────────────────────────────────────────────────────────────────

class _ErroValidacaoLimite(ValueError):
    """Falha controlada na validação monetária de uma solicitação."""


class _ErroTransicaoStatus(ValueError):
    """Falha controlada na normalização do status final solicitado."""


def _validar_novo_limite(novo_limite: object, limite_atual: float) -> float:
    """Retorna o valor validado quando ele é finito e supera o limite atual."""
    if novo_limite is None or isinstance(novo_limite, bool):
        raise _ErroValidacaoLimite(
            "O novo limite deve ser um valor numérico finito e maior que zero."
        )

    try:
        valor = float(novo_limite)
        atual = float(limite_atual)
    except (TypeError, ValueError) as e:
        raise _ErroValidacaoLimite(
            "O novo limite deve ser um valor numérico finito e maior que zero."
        ) from e

    if not math.isfinite(valor) or valor <= 0:
        raise _ErroValidacaoLimite(
            "O novo limite deve ser um valor numérico finito e maior que zero."
        )
    if not math.isfinite(atual) or atual < 0:
        raise _ErroValidacaoLimite(
            "Não foi possível validar o limite atual do cliente."
        )
    if valor <= atual:
        raise _ErroValidacaoLimite(
            "O novo limite deve ser maior que o limite atual."
        )

    return valor


def _agora_utc() -> datetime:
    """Retorna o relógio UTC; separado para permitir controle nos testes."""
    return datetime.now(timezone.utc)


def _gerar_timestamp_utc() -> str:
    """Gera timestamp ISO 8601 UTC com offset e microssegundos explícitos."""
    instante = _agora_utc()
    if instante.tzinfo is None or instante.utcoffset() is None:
        instante = instante.replace(tzinfo=timezone.utc)
    else:
        instante = instante.astimezone(timezone.utc)
    return instante.isoformat(timespec="microseconds")


def _normalizar_status_final(novo_status: object) -> str:
    """Normaliza somente os estados finais aceitos pela máquina de estados."""
    if not isinstance(novo_status, str):
        raise _ErroTransicaoStatus(
            "O status deve ser 'aprovado' ou 'rejeitado'."
        )

    normalizado = novo_status.strip().lower()
    if normalizado == "reprovado":
        normalizado = "rejeitado"
    if normalizado not in {"aprovado", "rejeitado"}:
        raise _ErroTransicaoStatus(
            "O status deve ser 'aprovado' ou 'rejeitado'."
        )
    return normalizado


def _resultado_registro_erro(mensagem: str) -> dict:
    return {
        "registrado": False,
        "data_hora": "",
        "limite_atual": None,
        "novo_limite_solicitado": None,
        "status_pedido": None,
        "erro": mensagem,
    }


def _resultado_analise_erro(mensagem: str) -> dict:
    return {
        "aprovado": False,
        "score_minimo_necessario": None,
        "limite_maximo_faixa": None,
        "limite_coberto": False,
        "erro": mensagem,
    }


def _resultado_status_erro(
    mensagem: str,
    *,
    status_anterior: str | None = None,
    status_novo: str | None = None,
) -> dict:
    return {
        "atualizado": False,
        "status_anterior": status_anterior,
        "status_novo": status_novo,
        "erro": mensagem,
    }


def _resultado_processamento_erro(mensagem: str) -> dict:
    return {
        "processado": False,
        "status_pedido": None,
        "limite_atualizado": False,
        "novo_limite": None,
        "oferecer_entrevista": False,
        "erro": mensagem,
    }


def _escrever_csv_atomico(dataframe: pd.DataFrame, destino: Path) -> None:
    """Prepara um CSV ao lado do destino e o publica com substituição atômica."""
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


def _restaurar_bytes_atomico(conteudo: bytes, destino: Path) -> None:
    """Restaura bytes capturados previamente por substituição atômica."""
    destino = Path(destino)
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destino.parent,
            prefix=f".{destino.name}.",
            suffix=".tmp",
            delete=False,
        ) as arquivo:
            temporario = Path(arquivo.name)
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    finally:
        if temporario is not None and temporario.exists():
            temporario.unlink()


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


def _carregar_faixas_score() -> pd.DataFrame:
    """Carrega e valida a tabela usada pela política de crédito."""
    faixas = pd.read_csv(CSV_SCORE_LIMITE, dtype=str)
    faixas.columns = faixas.columns.str.strip()
    colunas_faixa = {"limite_maximo", "score_minimo"}
    if not colunas_faixa.issubset(faixas.columns) or faixas.empty:
        raise ValueError("CSV de faixas malformado")
    faixas["limite_maximo"] = (
        faixas["limite_maximo"].str.strip().astype(float)
    )
    faixas["score_minimo"] = faixas["score_minimo"].str.strip().astype(int)
    if not faixas["limite_maximo"].map(math.isfinite).all():
        raise ValueError("CSV de faixas malformado")
    return faixas.sort_values("limite_maximo").reset_index(drop=True)


def _avaliar_politica_credito(
    score_cliente: int,
    novo_limite: float,
    faixas: pd.DataFrame,
) -> bool:
    """Decide aprovação sem acessar sessão, LLM ou armazenamento."""
    faixa = faixas[faixas["limite_maximo"] >= novo_limite]
    return bool(
        not faixa.empty
        and score_cliente >= int(faixa.iloc[0]["score_minimo"])
    )


def _publicar_aprovacao(
    solicitacoes: pd.DataFrame,
    clientes: pd.DataFrame,
    mascara_cliente,
    novo_limite: float,
) -> str | None:
    """Publica pedido e limite com rollback para o estado anterior."""
    solicitacoes_originais = Path(CSV_SOLICITACOES).read_bytes()
    clientes_originais = Path(CSV_CLIENTES).read_bytes()
    clientes.loc[mascara_cliente, "limite_credito"] = str(novo_limite)

    _escrever_csv_atomico(solicitacoes, CSV_SOLICITACOES)
    try:
        _escrever_csv_atomico(clientes, CSV_CLIENTES)
    except Exception as e:
        print(
            "[TOOL ERROR] publicação de aprovação: "
            f"{type(e).__name__}"
        )
        try:
            if Path(CSV_CLIENTES).read_bytes() != clientes_originais:
                _restaurar_bytes_atomico(clientes_originais, CSV_CLIENTES)
            _restaurar_bytes_atomico(
                solicitacoes_originais,
                CSV_SOLICITACOES,
            )
        except Exception as rollback_error:
            print(
                "[TOOL ERROR] rollback de aprovação: "
                f"{type(rollback_error).__name__}"
            )
            return "Não foi possível concluir a aprovação; recuperação pendente."
        return "Não foi possível concluir a aprovação; alterações revertidas."
    return None


# ── Tools ──────────────────────────────────────────────────────────────────

def consultar_limite(tool_context: ToolContext) -> dict:
    """
    Consulta o limite de crédito atual e o score do cliente.

    Returns:
        dict com:
            limite_atual (float): limite de crédito atual em R$.
            score_credito (int): score de crédito atual (0-1000).
            erro (str | None): mensagem de erro se houver.
    """
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return {
            "limite_atual": 0.0,
            "score_credito": 0,
            "erro": str(e),
        }

    try:
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        linha = df[df["cpf"] == cpf.strip()]
        if linha.empty:
            return {
                "limite_atual": 0.0,
                "score_credito": 0,
                "erro": "Cliente autenticado não encontrado.",
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
        print(f"[TOOL ERROR] consultar_limite: {type(e).__name__}")
        return {
            "limite_atual": 0.0,
            "score_credito": 0,
            "erro": "Erro ao consultar limite. Tente novamente.",
        }


def registrar_solicitacao(
    novo_limite_solicitado: float,
    tool_context: ToolContext,
) -> dict:
    """
    Registra uma solicitação de aumento de limite no CSV de solicitações.

    Cria o arquivo automaticamente caso não exista.
    Sempre faz append — preserva histórico de solicitações.

    Args:
        novo_limite_solicitado: Novo limite desejado pelo cliente.
        tool_context: Contexto ADK da sessão autenticada.

    Returns:
        dict com:
            data_hora (str): timestamp ISO 8601 da criação (chave para atualização posterior).
            erro (str | None).
    """
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return _resultado_registro_erro(str(e))

    try:
        clientes = pd.read_csv(CSV_CLIENTES, dtype=str)
        clientes.columns = clientes.columns.str.strip()
        clientes = clientes.map(lambda x: x.strip() if isinstance(x, str) else x)
        linha_cliente = clientes[clientes["cpf"] == cpf]
        if linha_cliente.empty:
            return _resultado_registro_erro(
                "Cliente autenticado não encontrado."
            )

        limite_atual = float(linha_cliente.iloc[0]["limite_credito"])
        try:
            novo_limite = _validar_novo_limite(
                novo_limite_solicitado,
                limite_atual,
            )
        except _ErroValidacaoLimite as e:
            return _resultado_registro_erro(str(e))

        _garantir_csv_solicitacoes()

        data_hora = _gerar_timestamp_utc()

        nova_linha = pd.DataFrame([{
            "cpf_cliente": cpf,
            "data_hora_solicitacao": data_hora,
            "limite_atual": float(limite_atual),
            "novo_limite_solicitado": novo_limite,
            "status_pedido": "pendente",
        }])

        nova_linha.to_csv(CSV_SOLICITACOES, mode="a", header=False, index=False)

        return {
            "registrado": True,
            "data_hora": data_hora,
            "limite_atual": limite_atual,
            "novo_limite_solicitado": novo_limite,
            "status_pedido": "pendente",
            "erro": None,
        }

    except Exception as e:
        print(f"[TOOL ERROR] registrar_solicitacao: {type(e).__name__}")
        return _resultado_registro_erro(
            "Erro ao registrar solicitação. Tente novamente."
        )


def processar_solicitacao(
    data_hora_solicitacao: str,
    tool_context: ToolContext,
) -> dict:
    """Decide e aplica deterministicamente uma solicitação pendente."""
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return _resultado_processamento_erro(str(e))

    if not isinstance(data_hora_solicitacao, str):
        return _resultado_processamento_erro(
            "O timestamp da solicitação é inválido."
        )
    timestamp = data_hora_solicitacao.strip()
    if not timestamp:
        return _resultado_processamento_erro(
            "O timestamp da solicitação é inválido."
        )

    try:
        solicitacoes = pd.read_csv(CSV_SOLICITACOES, dtype=str)
        solicitacoes.columns = solicitacoes.columns.str.strip()
        colunas_solicitacao = {
            "cpf_cliente",
            "data_hora_solicitacao",
            "limite_atual",
            "novo_limite_solicitado",
            "status_pedido",
        }
        if not colunas_solicitacao.issubset(solicitacoes.columns):
            raise ValueError("CSV de solicitações malformado")
        solicitacoes = solicitacoes.map(
            lambda x: x.strip() if isinstance(x, str) else x
        )

        mascara_solicitacao = (
            (solicitacoes["cpf_cliente"] == cpf)
            & (solicitacoes["data_hora_solicitacao"] == timestamp)
        )
        quantidade_solicitacoes = int(mascara_solicitacao.sum())
        if quantidade_solicitacoes == 0:
            return _resultado_processamento_erro(
                "Solicitação do cliente autenticado não encontrada."
            )
        if quantidade_solicitacoes > 1:
            return _resultado_processamento_erro(
                "Erro de integridade: mais de uma solicitação encontrada."
            )

        solicitacao = solicitacoes.loc[mascara_solicitacao].iloc[0]
        status_atual = str(solicitacao["status_pedido"]).strip().lower()
        if status_atual not in {"pendente", "aprovado", "rejeitado"}:
            return _resultado_processamento_erro(
                "A solicitação possui status persistido inválido."
            )
        if status_atual == "rejeitado":
            return _resultado_processamento_erro(
                "A solicitação já foi finalizada e não pode ser reprocessada."
            )

        try:
            novo_limite = _validar_novo_limite(
                solicitacao["novo_limite_solicitado"],
                solicitacao["limite_atual"],
            )
            limite_snapshot = float(solicitacao["limite_atual"])
        except _ErroValidacaoLimite as e:
            return _resultado_processamento_erro(str(e))

        clientes = pd.read_csv(CSV_CLIENTES, dtype=str)
        clientes.columns = clientes.columns.str.strip()
        colunas_cliente = {"cpf", "score_credito", "limite_credito"}
        if not colunas_cliente.issubset(clientes.columns):
            raise ValueError("CSV de clientes malformado")
        clientes = clientes.map(
            lambda x: x.strip() if isinstance(x, str) else x
        )

        mascara_cliente = clientes["cpf"] == cpf
        quantidade_clientes = int(mascara_cliente.sum())
        if quantidade_clientes == 0:
            return _resultado_processamento_erro(
                "Cliente autenticado não encontrado."
            )
        if quantidade_clientes > 1:
            return _resultado_processamento_erro(
                "Erro de integridade: mais de um cliente autenticado encontrado."
            )

        cliente = clientes.loc[mascara_cliente].iloc[0]
        try:
            limite_cliente = float(cliente["limite_credito"])
            score_cliente = int(cliente["score_credito"])
        except (TypeError, ValueError) as e:
            raise ValueError("CSV de clientes malformado") from e

        if not math.isfinite(limite_cliente) or limite_cliente < 0:
            raise ValueError("CSV de clientes malformado")

        if status_atual == "aprovado":
            if limite_cliente == novo_limite:
                return _resultado_processamento_erro(
                    "A aprovação já foi concluída e não pode ser reprocessada."
                )
            if limite_cliente != limite_snapshot:
                return _resultado_processamento_erro(
                    "O limite atual não corresponde ao estado recuperável da solicitação."
                )

            clientes.loc[mascara_cliente, "limite_credito"] = str(novo_limite)
            try:
                _escrever_csv_atomico(clientes, CSV_CLIENTES)
            except Exception as e:
                print(
                    "[TOOL ERROR] processar_solicitacao recuperação: "
                    f"{type(e).__name__}"
                )
                return _resultado_processamento_erro(
                    "Não foi possível recuperar a aprovação. Tente novamente."
                )

            tool_context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = None
            return {
                "processado": True,
                "status_pedido": "aprovado",
                "limite_atualizado": True,
                "novo_limite": novo_limite,
                "oferecer_entrevista": False,
                "erro": None,
            }

        if limite_cliente != limite_snapshot:
            return _resultado_processamento_erro(
                "O limite atual diverge do valor registrado na solicitação."
            )

        try:
            novo_limite = _validar_novo_limite(novo_limite, limite_cliente)
        except _ErroValidacaoLimite as e:
            return _resultado_processamento_erro(str(e))

        faixas = _carregar_faixas_score()
        aprovado = _avaliar_politica_credito(
            score_cliente,
            novo_limite,
            faixas,
        )
        status_final = "aprovado" if aprovado else "rejeitado"

        solicitacoes.loc[mascara_solicitacao, "status_pedido"] = status_final

        if not aprovado:
            _escrever_csv_atomico(solicitacoes, CSV_SOLICITACOES)
            tool_context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = timestamp
            estado_ofertado = oferecer_entrevista_credito(
                tool_context.state
            )
            oferta_ativa = (
                estado_ofertado.get(CREDIT_INTERVIEW_STATUS)
                == CREDIT_INTERVIEW_OFFERED
            )
            if oferta_ativa:
                tool_context.state[CREDIT_INTERVIEW_STATUS] = (
                    CREDIT_INTERVIEW_OFFERED
                )
            return {
                "processado": True,
                "status_pedido": "rejeitado",
                "limite_atualizado": False,
                "novo_limite": None,
                "oferecer_entrevista": oferta_ativa,
                "erro": None,
            }

        erro_publicacao = _publicar_aprovacao(
            solicitacoes,
            clientes,
            mascara_cliente,
            novo_limite,
        )
        if erro_publicacao:
            return _resultado_processamento_erro(erro_publicacao)

        tool_context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = None
        return {
            "processado": True,
            "status_pedido": "aprovado",
            "limite_atualizado": True,
            "novo_limite": novo_limite,
            "oferecer_entrevista": False,
            "erro": None,
        }

    except FileNotFoundError:
        return _resultado_processamento_erro(
            "Arquivo necessário ao processamento não encontrado."
        )
    except Exception as e:
        print(f"[TOOL ERROR] processar_solicitacao: {type(e).__name__}")
        return _resultado_processamento_erro(
            "Erro ao processar solicitação. Tente novamente."
        )


def reanalisar_solicitacao(tool_context: ToolContext) -> dict:
    """Reanalisa internamente o pedido rejeitado associado à entrevista."""
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return _resultado_processamento_erro(str(e))

    return reanalisar_solicitacao_autorizada(
        cpf,
        tool_context.state.get(CREDIT_INTERVIEW_REQUEST_TIMESTAMP),
    )


def reanalisar_solicitacao_autorizada(
    cpf: str,
    timestamp_associado: object,
) -> dict:
    """Reanalisa por identidade já autorizada, sem depender de ToolContext."""
    if not isinstance(timestamp_associado, str):
        return _resultado_processamento_erro(
            "Não há solicitação rejeitada associada à entrevista."
        )
    timestamp = timestamp_associado.strip()
    if not timestamp:
        return _resultado_processamento_erro(
            "Não há solicitação rejeitada associada à entrevista."
        )

    try:
        solicitacoes = pd.read_csv(CSV_SOLICITACOES, dtype=str)
        solicitacoes.columns = solicitacoes.columns.str.strip()
        colunas_solicitacao = {
            "cpf_cliente",
            "data_hora_solicitacao",
            "limite_atual",
            "novo_limite_solicitado",
            "status_pedido",
        }
        if not colunas_solicitacao.issubset(solicitacoes.columns):
            raise ValueError("CSV de solicitações malformado")
        solicitacoes = solicitacoes.map(
            lambda x: x.strip() if isinstance(x, str) else x
        )

        mascara_solicitacao = (
            (solicitacoes["cpf_cliente"] == cpf)
            & (solicitacoes["data_hora_solicitacao"] == timestamp)
        )
        quantidade_solicitacoes = int(mascara_solicitacao.sum())
        if quantidade_solicitacoes == 0:
            return _resultado_processamento_erro(
                "Solicitação rejeitada do cliente autenticado não encontrada."
            )
        if quantidade_solicitacoes > 1:
            return _resultado_processamento_erro(
                "Erro de integridade: mais de uma solicitação encontrada."
            )

        solicitacao = solicitacoes.loc[mascara_solicitacao].iloc[0]
        status_atual = str(solicitacao["status_pedido"]).strip().lower()
        if status_atual != "rejeitado":
            return _resultado_processamento_erro(
                "Somente uma solicitação rejeitada pode ser reanalisada."
            )

        try:
            novo_limite = _validar_novo_limite(
                solicitacao["novo_limite_solicitado"],
                solicitacao["limite_atual"],
            )
            limite_snapshot = float(solicitacao["limite_atual"])
        except _ErroValidacaoLimite as e:
            return _resultado_processamento_erro(str(e))

        clientes = pd.read_csv(CSV_CLIENTES, dtype=str)
        clientes.columns = clientes.columns.str.strip()
        colunas_cliente = {"cpf", "score_credito", "limite_credito"}
        if not colunas_cliente.issubset(clientes.columns):
            raise ValueError("CSV de clientes malformado")
        clientes = clientes.map(
            lambda x: x.strip() if isinstance(x, str) else x
        )

        mascara_cliente = clientes["cpf"] == cpf
        quantidade_clientes = int(mascara_cliente.sum())
        if quantidade_clientes == 0:
            return _resultado_processamento_erro(
                "Cliente autenticado não encontrado."
            )
        if quantidade_clientes > 1:
            return _resultado_processamento_erro(
                "Erro de integridade: mais de um cliente autenticado encontrado."
            )

        cliente = clientes.loc[mascara_cliente].iloc[0]
        try:
            limite_cliente = float(cliente["limite_credito"])
            score_cliente = int(cliente["score_credito"])
        except (TypeError, ValueError) as e:
            raise ValueError("CSV de clientes malformado") from e

        if not math.isfinite(limite_cliente) or limite_cliente < 0:
            raise ValueError("CSV de clientes malformado")
        if limite_cliente != limite_snapshot:
            return _resultado_processamento_erro(
                "O limite atual diverge do valor registrado na solicitação."
            )

        faixas = _carregar_faixas_score()
        aprovado = _avaliar_politica_credito(
            score_cliente,
            novo_limite,
            faixas,
        )
        if not aprovado:
            return {
                "processado": True,
                "status_pedido": "rejeitado",
                "limite_atualizado": False,
                "novo_limite": None,
                "oferecer_entrevista": False,
                "erro": None,
            }

        solicitacoes.loc[mascara_solicitacao, "status_pedido"] = "aprovado"
        erro_publicacao = _publicar_aprovacao(
            solicitacoes,
            clientes,
            mascara_cliente,
            novo_limite,
        )
        if erro_publicacao:
            return _resultado_processamento_erro(erro_publicacao)

        return {
            "processado": True,
            "status_pedido": "aprovado",
            "limite_atualizado": True,
            "novo_limite": novo_limite,
            "oferecer_entrevista": False,
            "erro": None,
        }

    except FileNotFoundError:
        return _resultado_processamento_erro(
            "Arquivo necessário à reanálise não encontrado."
        )
    except Exception as e:
        print(f"[TOOL ERROR] reanalisar_solicitacao: {type(e).__name__}")
        return _resultado_processamento_erro(
            "Erro ao reanalisar solicitação. Tente novamente."
        )


def checar_score_para_limite(
    novo_limite: float,
    tool_context: ToolContext,
) -> dict:
    """
    Verifica se o score do cliente é suficiente para o novo limite solicitado.

    Consulta score_limite.csv para encontrar a faixa correspondente ao limite
    e compara com o score atual do cliente.

    Args:
        novo_limite: Novo limite de crédito solicitado em R$.
        tool_context: Contexto ADK da sessão autenticada.

    Returns:
        dict com:
            aprovado (bool): True se score >= score mínimo da faixa.
            score_minimo_necessario (int): score mínimo exigido para esta faixa.
            erro (str | None).
    """
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return _resultado_analise_erro(str(e))

    try:
        clientes = pd.read_csv(CSV_CLIENTES, dtype=str)
        clientes.columns = clientes.columns.str.strip()
        clientes = clientes.map(lambda x: x.strip() if isinstance(x, str) else x)
        linha_cliente = clientes[clientes["cpf"] == cpf]
        if linha_cliente.empty:
            return _resultado_analise_erro(
                "Cliente autenticado não encontrado."
            )
        row_cliente = linha_cliente.iloc[0]
        score_cliente = int(row_cliente["score_credito"])
        limite_atual = float(row_cliente["limite_credito"])
        try:
            novo_limite_validado = _validar_novo_limite(
                novo_limite,
                limite_atual,
            )
        except _ErroValidacaoLimite as e:
            return _resultado_analise_erro(str(e))

        df = pd.read_csv(CSV_SCORE_LIMITE, dtype=str)
        df.columns = df.columns.str.strip()
        df["limite_maximo"] = df["limite_maximo"].str.strip().astype(float)
        df["score_minimo"]  = df["score_minimo"].str.strip().astype(int)

        # Ordenar crescente e buscar primeira faixa que cobre o novo limite
        df_sorted = df.sort_values("limite_maximo").reset_index(drop=True)
        faixa = df_sorted[
            df_sorted["limite_maximo"] >= novo_limite_validado
        ]

        if faixa.empty:
            return {
                "aprovado": False,
                "score_minimo_necessario": None,
                "limite_maximo_faixa": None,
                "limite_coberto": False,
                "erro": None,
            }

        faixa_aplicavel = faixa.iloc[0]
        score_minimo = int(faixa_aplicavel["score_minimo"])
        limite_maximo_faixa = float(faixa_aplicavel["limite_maximo"])

        aprovado = score_cliente >= score_minimo

        return {
            "aprovado": aprovado,
            "score_minimo_necessario": score_minimo,
            "limite_maximo_faixa": limite_maximo_faixa,
            "limite_coberto": True,
            "erro": None,
        }

    except FileNotFoundError:
        return _resultado_analise_erro("Tabela de score não encontrada.")
    except Exception as e:
        print(f"[TOOL ERROR] checar_score_para_limite: {type(e).__name__}")
        return _resultado_analise_erro(
            "Erro ao verificar score. Tente novamente."
        )


def atualizar_status_solicitacao(
    data_hora_solicitacao: str,
    novo_status: str,
    tool_context: ToolContext,
) -> dict:
    """
    Atualiza o status de uma solicitação existente em solicitacoes_aumento_limite.csv.

    Args:
        data_hora_solicitacao: Timestamp ISO 8601 da solicitação (chave de busca).
        novo_status: Novo status — 'aprovado' ou 'rejeitado'.
        tool_context: Contexto ADK da sessão autenticada.

    Returns:
        dict com:
            atualizado (bool).
            erro (str | None).
    """
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return _resultado_status_erro(str(e))

    try:
        status_final = _normalizar_status_final(novo_status)
    except _ErroTransicaoStatus as e:
        return _resultado_status_erro(str(e))

    try:
        _garantir_csv_solicitacoes()

        df = pd.read_csv(CSV_SOLICITACOES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        mascara = (
            (df["cpf_cliente"] == cpf) &
            (
                df["data_hora_solicitacao"]
                == str(data_hora_solicitacao or "").strip()
            )
        )

        quantidade = int(mascara.sum())
        if quantidade == 0:
            return _resultado_status_erro(
                "Solicitação do cliente autenticado não encontrada."
            )
        if quantidade > 1:
            return _resultado_status_erro(
                "Erro de integridade: mais de uma solicitação encontrada."
            )

        status_anterior = str(
            df.loc[mascara, "status_pedido"].iloc[0]
        ).strip().lower()
        if status_anterior not in {"pendente", "aprovado", "rejeitado"}:
            return _resultado_status_erro(
                "A solicitação possui status persistido inválido.",
                status_anterior=status_anterior,
                status_novo=status_final,
            )
        if status_anterior != "pendente":
            return _resultado_status_erro(
                "A solicitação já foi finalizada e não pode ser reprocessada.",
                status_anterior=status_anterior,
                status_novo=status_final,
            )

        df.loc[mascara, "status_pedido"] = status_final
        df.to_csv(CSV_SOLICITACOES, index=False)

        return {
            "atualizado": True,
            "status_anterior": status_anterior,
            "status_novo": status_final,
            "erro": None,
        }

    except Exception as e:
        print(f"[TOOL ERROR] atualizar_status_solicitacao: {type(e).__name__}")
        return _resultado_status_erro(
            "Erro ao atualizar status da solicitação."
        )


def atualizar_limite_cliente(
    novo_limite: float,
    tool_context: ToolContext,
) -> dict:
    """
    Atualiza o limite de crédito do cliente em clientes.csv após aprovação.

    Args:
        novo_limite: Novo limite aprovado em R$.
        tool_context: Contexto ADK da sessão autenticada.

    Returns:
        dict com:
            atualizado (bool).
            erro (str | None).
    """
    try:
        cpf = obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao as e:
        return {"atualizado": False, "erro": str(e)}

    try:
        df = pd.read_csv(CSV_CLIENTES, dtype=str)
        df.columns = df.columns.str.strip()
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        mascara = df["cpf"] == cpf
        if not mascara.any():
            return {
                "atualizado": False,
                "erro": "Cliente autenticado não encontrado para atualização.",
            }

        df.loc[mascara, "limite_credito"] = str(float(novo_limite))
        df.to_csv(CSV_CLIENTES, index=False)

        return {"atualizado": True, "erro": None}

    except Exception as e:
        print(f"[TOOL ERROR] atualizar_limite_cliente: {type(e).__name__}")
        return {
            "atualizado": False,
            "erro": "Erro ao atualizar limite do cliente. Tente novamente.",
        }
