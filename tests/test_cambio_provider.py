from datetime import datetime
from decimal import Decimal
import inspect
import unittest
from unittest.mock import patch

import httpx

import tools.cambio_provider as cambio_provider
from config import MOEDAS_SUPORTADAS
from tools.cambio_provider import (
    AwesomeApiProvider,
    CategoriaErroCambio,
    CotacaoCambio,
    ErroCambioProvider,
)


def payload_valido(codigo="USD"):
    return {
        f"{codigo}BRL": {
            "code": codigo,
            "codein": "BRL",
            "name": f"{codigo}/Real Brasileiro",
            "bid": "5.1234",
            "ask": "5.1334",
            "pctChange": "-0.52",
            "timestamp": "1785502800",
            "create_date": "2026-07-31 10:00:00",
        }
    }


class FakeHttpGet:
    def __init__(self, *, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exception is not None:
            raise self.exception
        return self.response


def resposta(status=200, *, payload=None, content=None):
    request = httpx.Request("GET", "https://provider.test/cotacao")
    if content is not None:
        return httpx.Response(status, content=content, request=request)
    return httpx.Response(status, json=payload, request=request)


class CambioProviderTests(unittest.TestCase):
    def setUp(self):
        bloqueio_rede = patch.object(
            cambio_provider.httpx,
            "get",
            side_effect=AssertionError("Acesso HTTP real proibido nos testes."),
        )
        bloqueio_rede.start()
        self.addCleanup(bloqueio_rede.stop)

    def consultar(self, payload=None, *, codigo="USD"):
        fake = FakeHttpGet(
            response=resposta(payload=payload or payload_valido(codigo))
        )
        resultado = AwesomeApiProvider(fake).consultar(codigo)
        return resultado, fake

    def assert_resposta_invalida(self, payload, *, codigo="USD"):
        fake = FakeHttpGet(response=resposta(payload=payload))
        with self.assertRaises(ErroCambioProvider) as contexto:
            AwesomeApiProvider(fake).consultar(codigo)
        self.assertEqual(
            CategoriaErroCambio.RESPOSTA_INVALIDA,
            contexto.exception.categoria,
        )
        return contexto.exception

    def test_sucesso_usd_brl(self):
        resultado, _ = self.consultar()

        self.assertIsInstance(resultado, CotacaoCambio)
        self.assertEqual("USD", resultado.moeda_origem)
        self.assertEqual("BRL", resultado.moeda_destino)
        self.assertEqual(Decimal("5.1234"), resultado.cotacao_compra)
        self.assertEqual(Decimal("5.1334"), resultado.cotacao_venda)
        self.assertEqual(Decimal("-0.52"), resultado.variacao_pct)
        self.assertEqual("AwesomeAPI", resultado.provider)

    def test_sucesso_com_outra_moeda_permitida(self):
        resultado, _ = self.consultar(codigo="EUR")
        self.assertEqual("EUR", resultado.moeda_origem)
        self.assertEqual("BRL", resultado.moeda_destino)

    def test_url_usa_somente_codigo_permitido_e_brl(self):
        _, fake = self.consultar(codigo="GBP")
        self.assertEqual(
            "https://economia.awesomeapi.com.br/json/last/GBP-BRL",
            fake.calls[0][0],
        )
        self.assertIn("GBP", MOEDAS_SUPORTADAS)

    def test_codigo_nao_canonico_ou_nao_permitido_nao_chega_ao_http(self):
        fake = FakeHttpGet(response=resposta(payload=payload_valido()))
        for codigo in ("usd", " USD", "USD/../../host", "ZZZ"):
            with self.subTest(codigo=codigo):
                with self.assertRaises(ValueError):
                    AwesomeApiProvider(fake).consultar(codigo)
        self.assertEqual([], fake.calls)

    def test_timeout_explicito_em_todas_as_fases(self):
        _, fake = self.consultar()
        timeout = fake.calls[0][1]["timeout"]
        self.assertIsInstance(timeout, httpx.Timeout)
        self.assertEqual(5.0, timeout.connect)
        self.assertEqual(5.0, timeout.read)
        self.assertEqual(5.0, timeout.write)
        self.assertEqual(5.0, timeout.pool)

    def test_redirects_explicitamente_desabilitados(self):
        _, fake = self.consultar()
        self.assertIs(False, fake.calls[0][1]["follow_redirects"])

    def test_http_nao_2xx_e_erro_controlado(self):
        for status in (302, 400, 404, 429, 500):
            with self.subTest(status=status):
                fake = FakeHttpGet(response=resposta(status, payload={}))
                with self.assertRaises(ErroCambioProvider) as contexto:
                    AwesomeApiProvider(fake).consultar("USD")
                self.assertEqual(
                    CategoriaErroCambio.HTTP,
                    contexto.exception.categoria,
                )
                self.assertEqual(status, contexto.exception.status_http)

    def test_timeout_e_distinto_de_transporte(self):
        request = httpx.Request("GET", "https://provider.test")
        fake = FakeHttpGet(exception=httpx.ReadTimeout("timeout", request=request))
        with self.assertRaises(ErroCambioProvider) as contexto:
            AwesomeApiProvider(fake).consultar("USD")
        self.assertEqual(CategoriaErroCambio.TIMEOUT, contexto.exception.categoria)

    def test_erro_de_conexao_e_transporte(self):
        request = httpx.Request("GET", "https://provider.test")
        fake = FakeHttpGet(exception=httpx.ConnectError("falha", request=request))
        with self.assertRaises(ErroCambioProvider) as contexto:
            AwesomeApiProvider(fake).consultar("USD")
        self.assertEqual(
            CategoriaErroCambio.TRANSPORTE,
            contexto.exception.categoria,
        )

    def test_json_invalido_e_resposta_invalida(self):
        fake = FakeHttpGet(response=resposta(content=b"{json-invalido"))
        with self.assertRaises(ErroCambioProvider) as contexto:
            AwesomeApiProvider(fake).consultar("USD")
        self.assertEqual(
            CategoriaErroCambio.RESPOSTA_INVALIDA,
            contexto.exception.categoria,
        )

    def test_objeto_raiz_invalido(self):
        for payload in (None, [], "texto"):
            with self.subTest(payload=payload):
                self.assert_resposta_invalida(payload)

    def test_chave_do_par_ausente(self):
        self.assert_resposta_invalida({"EURBRL": payload_valido("EUR")["EURBRL"]})

    def test_objeto_interno_invalido(self):
        for item in (None, [], "texto"):
            with self.subTest(item=item):
                self.assert_resposta_invalida({"USDBRL": item})

    def test_campos_financeiros_ausentes(self):
        for campo in ("bid", "ask", "pctChange"):
            with self.subTest(campo=campo):
                payload = payload_valido()
                del payload["USDBRL"][campo]
                self.assert_resposta_invalida(payload)

    def test_bid_e_ask_rejeitam_zero_e_negativos(self):
        for campo in ("bid", "ask"):
            for valor in ("0", "-0.01"):
                with self.subTest(campo=campo, valor=valor):
                    payload = payload_valido()
                    payload["USDBRL"][campo] = valor
                    self.assert_resposta_invalida(payload)

    def test_numeros_rejeitam_nan_e_infinito(self):
        casos = (
            ("bid", "NaN"),
            ("ask", "Infinity"),
            ("pctChange", "NaN"),
            ("pctChange", "-Infinity"),
        )
        for campo, valor in casos:
            with self.subTest(campo=campo, valor=valor):
                payload = payload_valido()
                payload["USDBRL"][campo] = valor
                self.assert_resposta_invalida(payload)

    def test_numeros_rejeitam_tipos_incompativeis(self):
        for campo in ("bid", "ask", "pctChange"):
            for valor in (None, True, [], {}, ""):
                with self.subTest(campo=campo, valor=valor):
                    payload = payload_valido()
                    payload["USDBRL"][campo] = valor
                    self.assert_resposta_invalida(payload)

    def test_code_divergente(self):
        payload = payload_valido()
        payload["USDBRL"]["code"] = "EUR"
        self.assert_resposta_invalida(payload)

    def test_codein_diferente_de_brl(self):
        payload = payload_valido()
        payload["USDBRL"]["codein"] = "USD"
        self.assert_resposta_invalida(payload)

    def test_name_vazio_ou_invalido(self):
        for valor in ("", "   ", None, 123):
            with self.subTest(valor=valor):
                payload = payload_valido()
                payload["USDBRL"]["name"] = valor
                self.assert_resposta_invalida(payload)

    def test_timestamp_ausente(self):
        payload = payload_valido()
        del payload["USDBRL"]["timestamp"]
        self.assert_resposta_invalida(payload)

    def test_timestamp_invalido(self):
        for valor in ("", "1.5", "abc", None, True, 10**30):
            with self.subTest(valor=valor):
                payload = payload_valido()
                payload["USDBRL"]["timestamp"] = valor
                self.assert_resposta_invalida(payload)

    def test_create_date_ausente(self):
        payload = payload_valido()
        del payload["USDBRL"]["create_date"]
        self.assert_resposta_invalida(payload)

    def test_create_date_invalido(self):
        for valor in (
            "",
            "31/07/2026 10:00:00",
            "2026-07-31T10:00:00Z",
            "2026-7-31 10:00:00",
            None,
        ):
            with self.subTest(valor=valor):
                payload = payload_valido()
                payload["USDBRL"]["create_date"] = valor
                self.assert_resposta_invalida(payload)

    def test_resposta_preserva_timestamp_da_fonte(self):
        resultado, _ = self.consultar()
        self.assertEqual(1785502800, resultado.timestamp_fonte)

    def test_resposta_nao_inventa_timezone_para_create_date(self):
        resultado, _ = self.consultar()
        self.assertEqual(
            datetime(2026, 7, 31, 10, 0, 0),
            resultado.data_atualizacao_fonte,
        )
        self.assertIsNone(resultado.data_atualizacao_fonte.tzinfo)

    def test_provider_nao_acessa_sessao_cpf_ou_tool_context(self):
        fonte = inspect.getsource(cambio_provider)
        self.assertNotIn("ToolContext", fonte)
        self.assertNotIn("session_state", fonte)
        self.assertNotIn("cpf", fonte.casefold())
        self.assertNotIn("print(", fonte)
        self.assertEqual(
            ("self", "codigo_moeda"),
            tuple(inspect.signature(AwesomeApiProvider.consultar).parameters),
        )

    def test_erro_nao_produz_cotacao_com_defaults(self):
        payload = payload_valido()
        payload["USDBRL"]["bid"] = "0"
        erro = self.assert_resposta_invalida(payload)

        self.assertNotIsInstance(erro, CotacaoCambio)
        self.assertFalse(hasattr(erro, "cotacao_compra"))
        self.assertFalse(hasattr(erro, "cotacao_venda"))

    def test_todos_os_testes_usam_limite_http_injetado(self):
        fake = FakeHttpGet(response=resposta(payload=payload_valido()))
        provider = AwesomeApiProvider(fake)
        provider.consultar("USD")
        self.assertEqual(1, len(fake.calls))


if __name__ == "__main__":
    unittest.main()
