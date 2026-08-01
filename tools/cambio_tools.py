"""Tool pública de câmbio do Banco Ágil."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from google.adk.tools import ToolContext

from config import MOEDAS_SUPORTADAS
from session_state import ErroAutorizacaoSessao, obter_cpf_autorizado
from tools.cambio_provider import (
    AwesomeApiProvider,
    CategoriaErroCambio,
    CotacaoCambio,
    ErroCambioProvider,
)


_MENSAGENS_ERRO_PROVIDER = {
    CategoriaErroCambio.TIMEOUT: (
        "Não foi possível consultar a cotação no momento. "
        "Tente novamente em instantes."
    ),
    CategoriaErroCambio.TRANSPORTE: (
        "O serviço de cotação está temporariamente indisponível. "
        "Tente novamente em instantes."
    ),
    CategoriaErroCambio.HTTP: (
        "O serviço de cotação está temporariamente indisponível. "
        "Tente novamente em instantes."
    ),
    CategoriaErroCambio.RESPOSTA_INVALIDA: (
        "A fonte externa não forneceu uma cotação válida no momento."
    ),
}

_CATEGORIAS_ERRO_PUBLICAS = {
    "autorizacao",
    "entrada_invalida",
    "timeout",
    "transporte",
    "http",
    "resposta_invalida",
}
_MENSAGEM_APRESENTACAO_INDISPONIVEL = (
    "Não foi possível apresentar a cotação com segurança. "
    "Tente novamente em instantes."
)
_FORMATO_DATA_FONTE = "%Y-%m-%d %H:%M:%S"


def _resultado_falha(categoria: str, mensagem: str) -> dict[str, object]:
    """Cria falha pública sem valores financeiros artificiais."""
    return {
        "sucesso": False,
        "categoria_erro": categoria,
        "erro": mensagem,
    }


def _serializar_cotacao(cotacao: CotacaoCambio) -> dict[str, object]:
    """Converte uma cotação validada para tipos JSON-safe explícitos."""
    return {
        "sucesso": True,
        "moeda_origem": cotacao.moeda_origem,
        "moeda_destino": cotacao.moeda_destino,
        "nome": cotacao.nome,
        "cotacao_compra": format(cotacao.cotacao_compra, "f"),
        "cotacao_venda": format(cotacao.cotacao_venda, "f"),
        "variacao_pct": format(cotacao.variacao_pct, "f"),
        "timestamp_fonte": cotacao.timestamp_fonte,
        "data_atualizacao_fonte": cotacao.data_atualizacao_fonte.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "provider": cotacao.provider,
        "categoria_erro": None,
        "erro": None,
    }


def _decimal_publico_valido(valor: object, *, positivo: bool) -> bool:
    """Valida uma string decimal sem alterar sua representação pública."""
    if not isinstance(valor, str) or not valor:
        return False
    try:
        numero = Decimal(valor)
    except InvalidOperation:
        return False
    if not numero.is_finite():
        return False
    return not positivo or numero > 0


def _data_fonte_valida(valor: object) -> bool:
    """Confirma o formato público sem atribuir timezone à data da fonte."""
    if not isinstance(valor, str):
        return False
    try:
        data = datetime.strptime(valor, _FORMATO_DATA_FONTE)
    except ValueError:
        return False
    return data.strftime(_FORMATO_DATA_FONTE) == valor


def renderizar_resultado_cotacao(resultado: object) -> str:
    """Renderiza deterministicamente o contrato público de câmbio."""
    if not isinstance(resultado, dict):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL

    if resultado.get("sucesso") is False:
        categoria = resultado.get("categoria_erro")
        erro = resultado.get("erro")
        if (
            categoria not in _CATEGORIAS_ERRO_PUBLICAS
            or not isinstance(erro, str)
            or not erro.strip()
        ):
            return _MENSAGEM_APRESENTACAO_INDISPONIVEL
        return f"Falha na consulta de câmbio ({categoria}): {erro.strip()}"

    if resultado.get("sucesso") is not True:
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL
    if resultado.get("categoria_erro") is not None or resultado.get("erro") is not None:
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL

    campos_texto = (
        "moeda_origem",
        "moeda_destino",
        "nome",
        "provider",
    )
    if any(
        not isinstance(resultado.get(campo), str)
        or not resultado[campo].strip()
        for campo in campos_texto
    ):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL
    if resultado["moeda_destino"] != "BRL":
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL

    compra = resultado.get("cotacao_compra")
    venda = resultado.get("cotacao_venda")
    variacao = resultado.get("variacao_pct")
    if not _decimal_publico_valido(compra, positivo=True):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL
    if not _decimal_publico_valido(venda, positivo=True):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL
    if not _decimal_publico_valido(variacao, positivo=False):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL

    timestamp = resultado.get("timestamp_fonte")
    data_fonte = resultado.get("data_atualizacao_fonte")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL
    if not _data_fonte_valida(data_fonte):
        return _MENSAGEM_APRESENTACAO_INDISPONIVEL

    origem = resultado["moeda_origem"].strip()
    destino = resultado["moeda_destino"].strip()
    nome = resultado["nome"].strip()
    provider = resultado["provider"].strip()
    return "\n".join((
        f"Cotação validada — {origem}/{destino}",
        f"Moeda: {nome}",
        f"Compra: {compra} {destino}",
        f"Venda: {venda} {destino}",
        f"Variação: {variacao}%",
        f"Fonte: {provider}",
        f"Data/hora informada pela fonte: {data_fonte}",
        "Fuso horário da data textual: não informado pela fonte.",
        f"Timestamp Unix informado pela fonte: {timestamp}",
    ))


def buscar_cotacao_autorizada(
    codigo_moeda: str,
    provider: AwesomeApiProvider,
) -> dict[str, object]:
    """Normaliza a moeda e delega exclusivamente ao provider validado."""
    if not isinstance(codigo_moeda, str):
        return _resultado_falha(
            "entrada_invalida",
            "O código da moeda é inválido.",
        )

    codigo = codigo_moeda.strip().upper()
    if codigo not in MOEDAS_SUPORTADAS:
        opcoes = ", ".join(sorted(MOEDAS_SUPORTADAS))
        return _resultado_falha(
            "entrada_invalida",
            f"Moeda não suportada. Disponíveis: {opcoes}.",
        )

    try:
        cotacao = provider.consultar(codigo)
    except ErroCambioProvider as erro:
        return _resultado_falha(
            erro.categoria.value,
            _MENSAGENS_ERRO_PROVIDER[erro.categoria],
        )

    return _serializar_cotacao(cotacao)


def buscar_cotacao(
    codigo_moeda: str,
    tool_context: ToolContext,
) -> dict[str, object]:
    """
    Consulta uma cotação validada para uma sessão autenticada e ativa.

    Args:
        codigo_moeda: Código da moeda de origem, como USD, EUR ou GBP.

    Returns:
        Dicionário JSON-safe com a cotação validada ou uma falha controlada.
    """
    try:
        obter_cpf_autorizado(tool_context)
    except ErroAutorizacaoSessao:
        return _resultado_falha(
            "autorizacao",
            "A consulta de câmbio requer uma sessão autenticada e ativa.",
        )

    return buscar_cotacao_autorizada(
        codigo_moeda,
        AwesomeApiProvider(),
    )
