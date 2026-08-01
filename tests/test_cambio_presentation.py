import hashlib
import inspect
import unittest
from unittest.mock import patch

from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

import config
import orchestrator
from session_state import criar_estado_inicial
import tools.cambio_tools as cambio_tools


def resultado_sucesso():
    return {
        "sucesso": True,
        "moeda_origem": "USD",
        "moeda_destino": "BRL",
        "nome": "Dólar Americano/Real Brasileiro",
        "cotacao_compra": "5.123400",
        "cotacao_venda": "5.133400",
        "variacao_pct": "-0.5200",
        "timestamp_fonte": 1785502800,
        "data_atualizacao_fonte": "2026-07-31 10:00:00",
        "provider": "AwesomeAPI",
        "categoria_erro": None,
        "erro": None,
    }


def evento_resposta_tool(nome, resultado):
    return Event(
        author="agente_cambio",
        content=Content(
            role="user",
            parts=[Part.from_function_response(name=nome, response=resultado)],
        ),
    )


def evento_texto(texto):
    return Event(
        author="agente_cambio",
        content=Content(role="model", parts=[Part(text=texto)]),
    )


class FakeRunner:
    def __init__(self, eventos):
        self.eventos = eventos
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        yield from self.eventos


class FakeToolContext:
    def __init__(self):
        self.state = criar_estado_inicial()


class CambioPresentationTests(unittest.TestCase):
    def setUp(self):
        self.service = InMemorySessionService()
        service_patch = patch.object(
            orchestrator,
            "_session_service",
            self.service,
        )
        service_patch.start()
        self.addCleanup(service_patch.stop)

        network_patch = patch(
            "tools.cambio_provider.httpx.get",
            side_effect=AssertionError("Acesso HTTP real proibido nos testes."),
        )
        self.http_get = network_patch.start()
        self.addCleanup(network_patch.stop)

    def criar_sessao(self, session_id):
        orchestrator.criar_sessao(session_id)

    def processar(self, session_id, eventos, mensagem="consultar dólar"):
        self.criar_sessao(session_id)
        runner = FakeRunner(eventos)
        with patch.object(orchestrator, "_runner", runner):
            resposta = orchestrator.processar_mensagem(session_id, mensagem)
        return resposta, runner

    def test_renderer_sucesso_preserva_todos_os_valores_exatos(self):
        mensagem = cambio_tools.renderizar_resultado_cotacao(resultado_sucesso())

        self.assertIn("USD/BRL", mensagem)
        self.assertIn("5.123400", mensagem)
        self.assertIn("5.133400", mensagem)
        self.assertIn("-0.5200", mensagem)
        self.assertIn("AwesomeAPI", mensagem)
        self.assertIn("2026-07-31 10:00:00", mensagem)
        self.assertIn("1785502800", mensagem)

    def test_renderer_nao_converte_valores_financeiros_para_float(self):
        fonte = inspect.getsource(cambio_tools.renderizar_resultado_cotacao)
        self.assertNotIn("float(", fonte)
        mensagem = cambio_tools.renderizar_resultado_cotacao(resultado_sucesso())
        self.assertIn("Compra: 5.123400 BRL", mensagem)
        self.assertIn("Venda: 5.133400 BRL", mensagem)

    def test_renderer_e_puro_sem_provider_autorizacao_ou_rede(self):
        with (
            patch.object(
                cambio_tools,
                "AwesomeApiProvider",
                side_effect=AssertionError("Provider não pode ser acessado."),
            ) as provider,
            patch.object(
                cambio_tools,
                "obter_cpf_autorizado",
                side_effect=AssertionError("Sessão não pode ser acessada."),
            ) as autorizar,
        ):
            mensagem = cambio_tools.renderizar_resultado_cotacao(
                resultado_sucesso()
            )

        self.assertIn("USD/BRL", mensagem)
        provider.assert_not_called()
        autorizar.assert_not_called()
        self.http_get.assert_not_called()

    def test_renderer_nao_inventa_timezone(self):
        mensagem = cambio_tools.renderizar_resultado_cotacao(resultado_sucesso())
        self.assertIn("Fuso horário da data textual: não informado pela fonte", mensagem)
        self.assertNotIn("horário de Brasília", mensagem)
        self.assertNotIn("UTC", mensagem)
        self.assertNotIn("horário local", mensagem)

    def test_resultado_incompleto_nao_produz_sucesso_parcial(self):
        campos = (
            "moeda_origem",
            "moeda_destino",
            "nome",
            "cotacao_compra",
            "cotacao_venda",
            "variacao_pct",
            "timestamp_fonte",
            "data_atualizacao_fonte",
            "provider",
        )
        for campo in campos:
            with self.subTest(campo=campo):
                resultado = resultado_sucesso()
                del resultado[campo]
                mensagem = cambio_tools.renderizar_resultado_cotacao(resultado)
                self.assertIn("apresentar a cotação com segurança", mensagem)
                self.assertNotIn("Compra:", mensagem)
                self.assertNotIn("Venda:", mensagem)
                self.assertNotIn("Variação:", mensagem)

    def test_renderer_rejeita_valores_financeiros_invalidos_sem_parcial(self):
        for campo, valor in (
            ("cotacao_compra", "0"),
            ("cotacao_venda", "NaN"),
            ("variacao_pct", "Infinity"),
            ("cotacao_compra", 5.12),
        ):
            with self.subTest(campo=campo, valor=valor):
                resultado = resultado_sucesso()
                resultado[campo] = valor
                mensagem = cambio_tools.renderizar_resultado_cotacao(resultado)
                self.assertIn("apresentar a cotação com segurança", mensagem)
                self.assertNotIn("Compra:", mensagem)

    def test_renderer_falhas_nunca_mostra_cotacao(self):
        categorias = (
            "autorizacao",
            "entrada_invalida",
            "timeout",
            "transporte",
            "http",
            "resposta_invalida",
        )
        for categoria in categorias:
            with self.subTest(categoria=categoria):
                mensagem = cambio_tools.renderizar_resultado_cotacao({
                    "sucesso": False,
                    "categoria_erro": categoria,
                    "erro": "Falha controlada do serviço.",
                })
                self.assertIn(categoria, mensagem)
                self.assertIn("Falha controlada do serviço", mensagem)
                self.assertNotIn("Compra:", mensagem)
                self.assertNotIn("Venda:", mensagem)
                self.assertNotIn("Variação:", mensagem)
                self.assertNotIn("cotação estimada", mensagem)

    def test_renderer_estrutura_inesperada_e_conservador(self):
        casos = (
            None,
            [],
            {},
            {"sucesso": "true"},
            {"sucesso": False, "categoria_erro": "desconhecida", "erro": "x"},
        )
        for resultado in casos:
            with self.subTest(resultado=resultado):
                mensagem = cambio_tools.renderizar_resultado_cotacao(resultado)
                self.assertIn("apresentar a cotação com segurança", mensagem)
                self.assertNotIn("Compra:", mensagem)

    def test_evento_adk_expoe_nome_e_resposta_da_tool(self):
        resultado = resultado_sucesso()
        evento = evento_resposta_tool("buscar_cotacao", resultado)

        self.assertFalse(evento.is_final_response())
        respostas = evento.get_function_responses()
        self.assertEqual(1, len(respostas))
        self.assertEqual("buscar_cotacao", respostas[0].name)
        self.assertEqual(resultado, respostas[0].response)

    def test_resultado_da_tool_substitui_texto_financeiro_do_modelo(self):
        eventos = [
            evento_resposta_tool("buscar_cotacao", resultado_sucesso()),
            evento_texto(
                "Compra 99, venda 100, variação 77%, timestamp inventado 123."
            ),
        ]
        resposta, runner = self.processar("cambio-sucesso", eventos)

        self.assertIn("Compra: 5.123400 BRL", resposta)
        self.assertIn("Venda: 5.133400 BRL", resposta)
        self.assertIn("Variação: -0.5200%", resposta)
        self.assertIn("1785502800", resposta)
        self.assertNotIn("99", resposta)
        self.assertNotIn("100", resposta)
        self.assertNotIn("77%", resposta)
        self.assertNotIn("inventado", resposta)
        self.assertEqual(1, len(runner.calls))

    def test_falha_da_tool_substitui_cotacao_inventada_pelo_modelo(self):
        falha = {
            "sucesso": False,
            "categoria_erro": "timeout",
            "erro": "Não foi possível consultar a cotação no momento.",
        }
        eventos = [
            evento_resposta_tool("buscar_cotacao", falha),
            evento_texto("Mesmo com timeout, a compra estimada é 9,99."),
        ]
        resposta, _ = self.processar("cambio-timeout", eventos)

        self.assertIn("timeout", resposta)
        self.assertIn(falha["erro"], resposta)
        self.assertNotIn("9,99", resposta)
        self.assertNotIn("Compra:", resposta)

    def test_resultado_de_outra_tool_nao_aciona_renderer_cambio(self):
        eventos = [
            evento_resposta_tool("consultar_limite", {"limite": 1000}),
            evento_texto("Resposta normal do modelo para outra tool."),
        ]
        resposta, _ = self.processar("outra-tool", eventos)
        self.assertEqual("Resposta normal do modelo para outra tool.", resposta)

    def test_turno_sem_buscar_cotacao_preserva_resposta_do_modelo(self):
        resposta, _ = self.processar(
            "sem-tool",
            [evento_texto("Comportamento anterior preservado.")],
        )
        self.assertEqual("Comportamento anterior preservado.", resposta)

    def test_autenticacao_da_tool_continua_obrigatoria(self):
        contexto = FakeToolContext()
        with patch.object(cambio_tools, "AwesomeApiProvider") as provider:
            resultado = cambio_tools.buscar_cotacao("USD", contexto)
        self.assertFalse(resultado["sucesso"])
        self.assertEqual("autorizacao", resultado["categoria_erro"])
        provider.assert_not_called()
        self.http_get.assert_not_called()

    def test_encerramento_global_continua_antes_do_runner(self):
        self.criar_sessao("encerramento")
        runner = FakeRunner([evento_texto("modelo não deveria executar")])
        with patch.object(orchestrator, "_runner", runner):
            resposta = orchestrator.processar_mensagem("encerramento", "tchau")

        self.assertEqual(orchestrator.MENSAGEM_ATENDIMENTO_ENCERRADO, resposta)
        self.assertEqual([], runner.calls)

    def test_renderer_orquestrador_nao_acessam_rede_e_preservam_csvs(self):
        caminhos = (
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        )
        antes = {
            caminho: hashlib.sha256(caminho.read_bytes()).hexdigest()
            for caminho in caminhos
        }
        eventos = [
            evento_resposta_tool("buscar_cotacao", resultado_sucesso()),
            evento_texto("texto ignorado"),
        ]

        self.processar("integridade", eventos)

        depois = {
            caminho: hashlib.sha256(caminho.read_bytes()).hexdigest()
            for caminho in caminhos
        }
        self.assertEqual(antes, depois)
        self.http_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
