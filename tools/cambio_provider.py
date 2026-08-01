"""Provider tipado para obter cotações validadas da AwesomeAPI."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import math
import re
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from config import MOEDAS_SUPORTADAS


_AWESOMEAPI_LAST_BASE_URL = "https://economia.awesomeapi.com.br/json/last"
_MOEDA_DESTINO = "BRL"
_PROVIDER = "AwesomeAPI"
_FORMATO_CREATE_DATE = "%Y-%m-%d %H:%M:%S"
_TIMEOUT_SEGUNDOS = 5.0


class CategoriaErroCambio(str, Enum):
    """Categorias estáveis de falha do provider externo."""

    TIMEOUT = "timeout"
    TRANSPORTE = "transporte"
    HTTP = "http"
    RESPOSTA_INVALIDA = "resposta_invalida"


class ErroCambioProvider(RuntimeError):
    """Falha controlada ao consultar ou validar a AwesomeAPI."""

    def __init__(
        self,
        categoria: CategoriaErroCambio,
        mensagem: str,
        *,
        status_http: int | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.categoria = categoria
        self.status_http = status_http


@dataclass(frozen=True)
class CotacaoCambio:
    """Cotação externa validada, sem valores artificiais de fallback."""

    moeda_origem: str
    moeda_destino: str
    nome: str
    cotacao_compra: Decimal
    cotacao_venda: Decimal
    variacao_pct: Decimal
    timestamp_fonte: int
    data_atualizacao_fonte: datetime
    provider: str


class HttpGet(Protocol):
    """Limite mínimo de transporte necessário pelo provider."""

    def __call__(
        self,
        url: str,
        *,
        timeout: httpx.Timeout,
        follow_redirects: bool,
    ) -> httpx.Response: ...


class AwesomeApiProvider:
    """Consulta exclusivamente pares permitidos entre uma moeda e BRL."""

    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or httpx.get
        self._timeout = httpx.Timeout(_TIMEOUT_SEGUNDOS)

    def consultar(self, codigo_moeda: str) -> CotacaoCambio:
        """Obtém uma cotação ou levanta ``ErroCambioProvider`` controlado."""
        codigo = self._validar_codigo_moeda(codigo_moeda)
        url = self._montar_url(codigo)

        try:
            response = self._http_get(
                url,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ErroCambioProvider(
                CategoriaErroCambio.TIMEOUT,
                "A consulta de câmbio excedeu o tempo limite.",
            ) from exc
        except httpx.RequestError as exc:
            raise ErroCambioProvider(
                CategoriaErroCambio.TRANSPORTE,
                "Não foi possível acessar o provider de câmbio.",
            ) from exc

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ErroCambioProvider(
                CategoriaErroCambio.HTTP,
                "O provider de câmbio retornou um status HTTP de erro.",
                status_http=exc.response.status_code,
            ) from exc

        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise self._resposta_invalida("JSON inválido.") from exc

        return self._validar_payload(payload, codigo)

    @staticmethod
    def _validar_codigo_moeda(codigo_moeda: str) -> str:
        if not isinstance(codigo_moeda, str):
            raise ValueError("O código da moeda deve ser uma string canônica.")
        if codigo_moeda not in MOEDAS_SUPORTADAS:
            raise ValueError("Código de moeda não permitido.")
        return codigo_moeda

    @staticmethod
    def _montar_url(codigo: str) -> str:
        base = _AWESOMEAPI_LAST_BASE_URL.rstrip("/")
        partes = urlsplit(base)
        if partes.scheme != "https" or not partes.netloc:
            raise RuntimeError("A URL controlada da AwesomeAPI deve usar HTTPS.")
        return f"{base}/{codigo}-{_MOEDA_DESTINO}"

    @classmethod
    def _validar_payload(cls, payload: object, codigo: str) -> CotacaoCambio:
        if not isinstance(payload, dict):
            raise cls._resposta_invalida("O objeto raiz não é válido.")

        chave_par = f"{codigo}{_MOEDA_DESTINO}"
        if chave_par not in payload:
            raise cls._resposta_invalida("A chave do par está ausente.")

        item = payload[chave_par]
        if not isinstance(item, dict):
            raise cls._resposta_invalida("O item da cotação não é válido.")

        if item.get("code") != codigo:
            raise cls._resposta_invalida("A moeda de origem diverge da solicitada.")
        if item.get("codein") != _MOEDA_DESTINO:
            raise cls._resposta_invalida("A moeda de destino deve ser BRL.")

        nome = item.get("name")
        if not isinstance(nome, str) or not nome.strip():
            raise cls._resposta_invalida("O nome do par não é válido.")

        compra = cls._numero_finito(item, "bid", positivo=True)
        venda = cls._numero_finito(item, "ask", positivo=True)
        variacao = cls._numero_finito(item, "pctChange", positivo=False)
        timestamp = cls._timestamp_fonte(item)
        data_atualizacao = cls._data_atualizacao_fonte(item)

        return CotacaoCambio(
            moeda_origem=codigo,
            moeda_destino=_MOEDA_DESTINO,
            nome=nome.strip(),
            cotacao_compra=compra,
            cotacao_venda=venda,
            variacao_pct=variacao,
            timestamp_fonte=timestamp,
            data_atualizacao_fonte=data_atualizacao,
            provider=_PROVIDER,
        )

    @classmethod
    def _numero_finito(
        cls,
        item: dict,
        campo: str,
        *,
        positivo: bool,
    ) -> Decimal:
        if campo not in item:
            raise cls._resposta_invalida(f"O campo {campo} está ausente.")

        valor = item[campo]
        if isinstance(valor, bool) or not isinstance(
            valor,
            (str, int, float, Decimal),
        ):
            raise cls._resposta_invalida(f"O campo {campo} não é numérico.")
        if isinstance(valor, str) and not valor.strip():
            raise cls._resposta_invalida(f"O campo {campo} não é numérico.")
        if isinstance(valor, float) and not math.isfinite(valor):
            raise cls._resposta_invalida(f"O campo {campo} não é finito.")

        try:
            numero = Decimal(str(valor).strip())
        except (InvalidOperation, ValueError) as exc:
            raise cls._resposta_invalida(f"O campo {campo} não é numérico.") from exc

        if not numero.is_finite():
            raise cls._resposta_invalida(f"O campo {campo} não é finito.")
        if positivo and numero <= 0:
            raise cls._resposta_invalida(f"O campo {campo} deve ser positivo.")
        return numero

    @classmethod
    def _timestamp_fonte(cls, item: dict) -> int:
        if "timestamp" not in item:
            raise cls._resposta_invalida("O timestamp está ausente.")

        valor = item["timestamp"]
        if isinstance(valor, bool):
            raise cls._resposta_invalida("O timestamp não é válido.")
        if isinstance(valor, int):
            timestamp = valor
        elif isinstance(valor, str) and re.fullmatch(r"-?\d+", valor):
            timestamp = int(valor)
        else:
            raise cls._resposta_invalida("O timestamp não é válido.")

        try:
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise cls._resposta_invalida("O timestamp não é válido.") from exc
        return timestamp

    @classmethod
    def _data_atualizacao_fonte(cls, item: dict) -> datetime:
        if "create_date" not in item:
            raise cls._resposta_invalida("A data de atualização está ausente.")

        valor = item["create_date"]
        if not isinstance(valor, str):
            raise cls._resposta_invalida("A data de atualização não é válida.")
        try:
            data = datetime.strptime(valor, _FORMATO_CREATE_DATE)
        except ValueError as exc:
            raise cls._resposta_invalida("A data de atualização não é válida.") from exc
        if data.strftime(_FORMATO_CREATE_DATE) != valor:
            raise cls._resposta_invalida("A data de atualização não é válida.")
        return data

    @staticmethod
    def _resposta_invalida(mensagem: str) -> ErroCambioProvider:
        return ErroCambioProvider(
            CategoriaErroCambio.RESPOSTA_INVALIDA,
            mensagem,
        )
