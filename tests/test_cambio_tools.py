from datetime import datetime
from decimal import Decimal
import hashlib
import inspect
import json
import unittest
from unittest.mock import patch

from google.adk.tools import FunctionTool

from agents.cambio import agente_cambio
from config import CSV_CLIENTES, CSV_SCORE_LIMITE, CSV_SOLICITACOES
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    criar_estado_inicial,
)
import tools.cambio_tools as cambio_tools
from tools.cambio_provider import (
    CategoriaErroCambio,
    CotacaoCambio,
    ErroCambioProvider,
)


CPF_TESTE = "11111111111"


class FakeToolContext:
    def __init__(self, *, autenticado=True, encerrado=False):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = autenticado
        self.state[AUTHENTICATED_CPF] = CPF_TESTE if autenticado else None
        self.state[CONVERSATION_ENDED] = encerrado


def cotacao_valida(codigo="USD"):
    return CotacaoCambio(
        moeda_origem=codigo,
        moeda_destino="BRL",
        nome=f"{codigo}/Real Brasileiro",
        cotacao_compra=Decimal("5.123400"),
        cotacao_venda=Decimal("5.133400"),
        variacao_pct=Decimal("-0.5200"),
        timestamp_fonte=1785502800,
        data_atualizacao_fonte=datetime(2026, 7, 31, 10, 0, 0),
        provider="AwesomeAPI",
    )


class FakeProvider:
    def __init__(self, *, resultado=None, erro=None):
        self.resultado = resultado or cotacao_valida()
        self.erro = erro
        self.calls = []

    def consultar(self, codigo_moeda):
        self.calls.append(codigo_moeda)
        if self.erro is not None:
            raise self.erro
        if self.resultado.moeda_origem != codigo_moeda:
            return cotacao_valida(codigo_moeda)
        return self.resultado


class CambioToolsTests(unittest.TestCase):
    def setUp(self):
        self.fake_provider = FakeProvider()
        provider_patch = patch.object(
            cambio_tools,
            "AwesomeApiProvider",
            return_value=self.fake_provider,
        )
        self.provider_class = provider_patch.start()
        self.addCleanup(provider_patch.stop)

        network_patch = patch(
            "tools.cambio_provider.httpx.get",
            side_effect=AssertionError("Acesso HTTP real proibido nos testes."),
        )
        self.http_get = network_patch.start()
        self.addCleanup(network_patch.stop)

    def buscar(self, codigo="USD", *, contexto=None):
        return cambio_tools.buscar_cotacao(
            codigo,
            contexto or FakeToolContext(),
        )

    def test_cliente_autenticado_pode_consultar(self):
        resultado = self.buscar()
        self.assertTrue(resultado["sucesso"])
        self.assertEqual(["USD"], self.fake_provider.calls)

    def test_sessao_nao_autenticada_e_bloqueada_antes_do_provider(self):
        resultado = self.buscar(contexto=FakeToolContext(autenticado=False))
        self.assertFalse(resultado["sucesso"])
        self.assertEqual("autorizacao", resultado["categoria_erro"])
        self.provider_class.assert_not_called()
        self.assertEqual([], self.fake_provider.calls)
        self.http_get.assert_not_called()

    def test_sessao_encerrada_e_bloqueada_antes_do_provider(self):
        resultado = self.buscar(contexto=FakeToolContext(encerrado=True))
        self.assertFalse(resultado["sucesso"])
        self.assertEqual("autorizacao", resultado["categoria_erro"])
        self.provider_class.assert_not_called()
        self.assertEqual([], self.fake_provider.calls)
        self.http_get.assert_not_called()

    def test_cpf_nao_e_argumento_nem_aparece_no_retorno(self):
        assinatura = inspect.signature(cambio_tools.buscar_cotacao)
        self.assertNotIn("cpf", assinatura.parameters)

        resultado = self.buscar()
        self.assertNotIn("cpf", json.dumps(resultado).casefold())
        self.assertNotIn(CPF_TESTE, json.dumps(resultado))

    def test_schema_adk_expoe_somente_codigo_moeda(self):
        declaracao = FunctionTool(
            cambio_tools.buscar_cotacao
        )._get_declaration().model_dump(exclude_none=True)
        parametros = declaracao["parameters"]

        self.assertEqual({"codigo_moeda"}, set(parametros["properties"]))
        self.assertEqual(["codigo_moeda"], parametros["required"])
        self.assertNotIn("tool_context", parametros["properties"])
        self.assertNotIn("cpf", parametros["properties"])

    def test_agente_cambio_continua_registrando_a_tool_publica(self):
        self.assertIn(cambio_tools.buscar_cotacao, agente_cambio.tools)
        self.assertEqual(
            ["buscar_cotacao", "encerrar_atendimento"],
            [tool.__name__ for tool in agente_cambio.tools],
        )

    def test_tool_delega_codigo_normalizado_ao_provider(self):
        resultado = self.buscar("  usd  ")
        self.assertTrue(resultado["sucesso"])
        self.assertEqual(["USD"], self.fake_provider.calls)

    def test_sucesso_usd_preserva_contrato_validado(self):
        resultado = self.buscar()

        self.assertEqual(
            {
                "sucesso": True,
                "moeda_origem": "USD",
                "moeda_destino": "BRL",
                "nome": "USD/Real Brasileiro",
                "cotacao_compra": "5.123400",
                "cotacao_venda": "5.133400",
                "variacao_pct": "-0.5200",
                "timestamp_fonte": 1785502800,
                "data_atualizacao_fonte": "2026-07-31 10:00:00",
                "provider": "AwesomeAPI",
                "categoria_erro": None,
                "erro": None,
            },
            resultado,
        )
        json.dumps(resultado)

    def test_sucesso_com_outra_moeda_permitida(self):
        resultado = self.buscar("EUR")
        self.assertTrue(resultado["sucesso"])
        self.assertEqual("EUR", resultado["moeda_origem"])
        self.assertEqual("BRL", resultado["moeda_destino"])
        self.assertEqual(["EUR"], self.fake_provider.calls)

    def test_decimais_sao_strings_exatas_e_json_safe(self):
        resultado = self.buscar()
        self.assertEqual("5.123400", resultado["cotacao_compra"])
        self.assertEqual("5.133400", resultado["cotacao_venda"])
        self.assertEqual("-0.5200", resultado["variacao_pct"])
        self.assertIsInstance(resultado["cotacao_compra"], str)
        json.dumps(resultado)

    def test_timestamp_e_create_date_da_fonte_sao_preservados(self):
        resultado = self.buscar()
        self.assertEqual(1785502800, resultado["timestamp_fonte"])
        self.assertEqual(
            "2026-07-31 10:00:00",
            resultado["data_atualizacao_fonte"],
        )
        self.assertNotIn("Z", resultado["data_atualizacao_fonte"])
        self.assertNotIn("+", resultado["data_atualizacao_fonte"])

    def test_falhas_do_provider_sao_publicas_e_controladas(self):
        mensagens_internas = {
            categoria: f"detalhe interno secreto {categoria.value}"
            for categoria in CategoriaErroCambio
        }
        for categoria, detalhe in mensagens_internas.items():
            with self.subTest(categoria=categoria.value):
                provider = FakeProvider(
                    erro=ErroCambioProvider(
                        categoria,
                        detalhe,
                        status_http=599,
                    )
                )
                resultado = cambio_tools.buscar_cotacao_autorizada(
                    "USD",
                    provider,
                )

                self.assertFalse(resultado["sucesso"])
                self.assertEqual(categoria.value, resultado["categoria_erro"])
                self.assertNotIn(detalhe, resultado["erro"])
                self.assertNotIn("Traceback", resultado["erro"])
                self.assertNotIn("599", resultado["erro"])

    def test_falha_nao_contem_valores_financeiros_artificiais(self):
        campos_financeiros = {
            "cotacao_compra",
            "cotacao_venda",
            "variacao_pct",
        }
        for categoria in CategoriaErroCambio:
            with self.subTest(categoria=categoria.value):
                provider = FakeProvider(
                    erro=ErroCambioProvider(categoria, "detalhe interno")
                )
                resultado = cambio_tools.buscar_cotacao_autorizada(
                    "USD",
                    provider,
                )
                self.assertFalse(resultado["sucesso"])
                self.assertTrue(campos_financeiros.isdisjoint(resultado))

    def test_moeda_desconhecida_nao_consulta_provider(self):
        resultado = self.buscar("ZZZ")
        self.assertFalse(resultado["sucesso"])
        self.assertEqual("entrada_invalida", resultado["categoria_erro"])
        self.provider_class.assert_called_once_with()
        self.assertEqual([], self.fake_provider.calls)
        self.http_get.assert_not_called()

    def test_url_arbitraria_nao_e_aceita_como_codigo(self):
        resultado = self.buscar("https://exemplo.invalid/cotacao")
        self.assertFalse(resultado["sucesso"])
        self.assertEqual("entrada_invalida", resultado["categoria_erro"])
        self.assertEqual([], self.fake_provider.calls)
        self.http_get.assert_not_called()

    def test_tool_nao_duplica_http_parsing_ou_allowlist(self):
        fonte = inspect.getsource(cambio_tools)
        self.assertNotIn("httpx", fonte)
        self.assertNotIn(".json()", fonte)
        self.assertNotIn("raise_for_status", fonte)
        self.assertNotIn("CAMBIO_API_BASE_URL", fonte)
        self.assertNotIn("create_date\"]", fonte)
        self.assertNotIn("print(", fonte)
        self.assertIn("from config import MOEDAS_SUPORTADAS", fonte)

    def test_http_real_permanece_bloqueado(self):
        resultado = self.buscar()
        self.assertTrue(resultado["sucesso"])
        self.http_get.assert_not_called()

    def test_csvs_reais_permanecem_intactos(self):
        caminhos = (CSV_CLIENTES, CSV_SCORE_LIMITE, CSV_SOLICITACOES)
        antes = {
            caminho: hashlib.sha256(caminho.read_bytes()).hexdigest()
            for caminho in caminhos
        }

        self.buscar()

        depois = {
            caminho: hashlib.sha256(caminho.read_bytes()).hexdigest()
            for caminho in caminhos
        }
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
