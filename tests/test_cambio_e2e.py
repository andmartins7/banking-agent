import hashlib
import os
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

os.environ.setdefault("GOOGLE_API_KEY", "test-only-not-a-real-key")

import config
import orchestrator
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


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.erro = None

    def consultar(self, codigo_moeda):
        self.calls.append(codigo_moeda)
        if self.erro is not None:
            raise self.erro
        return CotacaoCambio(
            moeda_origem=codigo_moeda,
            moeda_destino="BRL",
            nome=f"{codigo_moeda}/Real Brasileiro",
            cotacao_compra=Decimal("5.123400"),
            cotacao_venda=Decimal("5.133400"),
            variacao_pct=Decimal("-0.5200"),
            timestamp_fonte=1785502800,
            data_atualizacao_fonte=datetime(2026, 7, 31, 10, 0, 0),
            provider="AwesomeAPI",
        )


class SessionToolContext:
    def __init__(self, state):
        self.state = state


class CambioRunnerControlado:
    """Emula o ciclo ADK, executando a tool real e emitindo eventos reais."""

    def __init__(
        self,
        service,
        *,
        codigo_moeda=None,
        texto_modelo="Resposta final controlada do modelo.",
    ):
        self.service = service
        self.codigo_moeda = codigo_moeda
        self.texto_modelo = texto_modelo
        self.calls = []
        self.tool_calls = []
        self.tool_results = []
        self.events = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.codigo_moeda is not None:
            session = orchestrator._run_async(self.service.get_session(
                app_name=config.APP_NAME,
                user_id=kwargs["user_id"],
                session_id=kwargs["session_id"],
            ))
            resultado = cambio_tools.buscar_cotacao(
                self.codigo_moeda,
                SessionToolContext(session.state),
            )
            self.tool_calls.append(self.codigo_moeda)
            self.tool_results.append(resultado)
            evento_tool = Event(
                author="agente_cambio",
                content=Content(
                    role="user",
                    parts=[Part.from_function_response(
                        name="buscar_cotacao",
                        response=resultado,
                    )],
                ),
            )
            self.events.append(evento_tool)
            yield evento_tool

        evento_final = Event(
            author="agente_cambio",
            content=Content(
                role="model",
                parts=[Part(text=self.texto_modelo)],
            ),
        )
        self.events.append(evento_final)
        yield evento_final


class CambioE2ETests(unittest.TestCase):
    def setUp(self):
        self.service = InMemorySessionService()
        self.provider = FakeProvider()

        service_patch = patch.object(
            orchestrator,
            "_session_service",
            self.service,
        )
        service_patch.start()
        self.addCleanup(service_patch.stop)

        provider_patch = patch.object(
            cambio_tools,
            "AwesomeApiProvider",
            side_effect=lambda: self.provider,
        )
        provider_patch.start()
        self.addCleanup(provider_patch.stop)

        network_patch = patch(
            "tools.cambio_provider.httpx.get",
            side_effect=AssertionError("Rede real proibida no E2E de Câmbio."),
        )
        self.http_get = network_patch.start()
        self.addCleanup(network_patch.stop)

        self.csvs = (
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        )
        self.hashes_csv_antes = self._hashes_csv()
        self.addCleanup(self._confirmar_csvs_intactos)

    def _hashes_csv(self):
        return {
            caminho: hashlib.sha256(caminho.read_bytes()).hexdigest()
            for caminho in self.csvs
        }

    def _confirmar_csvs_intactos(self):
        self.assertEqual(self.hashes_csv_antes, self._hashes_csv())

    def criar_sessao(self, session_id, *, autenticada):
        state = criar_estado_inicial()
        state[AUTHENTICATED] = autenticada
        state[AUTHENTICATED_CPF] = CPF_TESTE if autenticada else None
        orchestrator._run_async(self.service.create_session(
            app_name=config.APP_NAME,
            user_id=session_id,
            session_id=session_id,
            state=state,
        ))

    def estado(self, session_id):
        session = orchestrator._run_async(self.service.get_session(
            app_name=config.APP_NAME,
            user_id=session_id,
            session_id=session_id,
        ))
        return session.state

    def processar(self, session_id, runner, mensagem="quero cotação do dólar"):
        with patch.object(orchestrator, "_runner", runner):
            return orchestrator.processar_mensagem(session_id, mensagem)

    def assert_handoff_invisivel(self, mensagem):
        normalizada = mensagem.casefold()
        termos = (
            "agente de câmbio",
            "transferência",
            "transferir",
            "handoff",
            "redirecionamento",
            "troca de atendente",
            "retorno para outro agente",
        )
        for termo in termos:
            with self.subTest(termo=termo):
                self.assertNotIn(termo, normalizada)

    def assert_sem_cotacao(self, mensagem):
        self.assertNotIn("Compra:", mensagem)
        self.assertNotIn("Venda:", mensagem)
        self.assertNotIn("Variação:", mensagem)
        self.assertNotIn("cotação estimada", mensagem.casefold())
        self.assertNotIn("0.0", mensagem)

    def test_01_sucesso_e2e_preserva_tool_e_supera_texto_do_modelo(self):
        self.criar_sessao("sucesso", autenticada=True)
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda="USD",
            texto_modelo=(
                "Vou transferir ao agente de Câmbio. Compra 99, venda 100, "
                "variação 77%, horário UTC e timestamp 123."
            ),
        )

        mensagem = self.processar("sucesso", runner)

        self.assertEqual(["USD"], runner.tool_calls)
        self.assertEqual(["USD"], self.provider.calls)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(1, len(runner.tool_results))
        self.assertTrue(runner.tool_results[0]["sucesso"])
        self.assertFalse(runner.events[0].is_final_response())
        self.assertEqual(
            runner.tool_results[0],
            runner.events[0].get_function_responses()[0].response,
        )
        self.assertIn("USD/BRL", mensagem)
        self.assertIn("Compra: 5.123400 BRL", mensagem)
        self.assertIn("Venda: 5.133400 BRL", mensagem)
        self.assertIn("Variação: -0.5200%", mensagem)
        self.assertIn("Fonte: AwesomeAPI", mensagem)
        self.assertIn("2026-07-31 10:00:00", mensagem)
        self.assertIn("1785502800", mensagem)
        self.assertIn("fuso horário", mensagem.casefold())
        self.assertIn("não informado pela fonte", mensagem.casefold())
        self.assertNotIn("99", mensagem)
        self.assertNotIn("100", mensagem)
        self.assertNotIn("77%", mensagem)
        self.assertNotIn("UTC", mensagem)
        self.assertNotIn("timestamp 123", mensagem.casefold())
        self.assert_handoff_invisivel(mensagem)
        self.http_get.assert_not_called()

    def test_02_timeout_e2e_bloqueia_fallback_do_modelo(self):
        self.criar_sessao("timeout", autenticada=True)
        self.provider.erro = ErroCambioProvider(
            CategoriaErroCambio.TIMEOUT,
            "detalhe interno do timeout",
        )
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda="USD",
            texto_modelo="Compra estimada 9,99; venda 10,00 após o timeout.",
        )

        mensagem = self.processar("timeout", runner)

        self.assertEqual(["USD"], self.provider.calls)
        self.assertIn("timeout", mensagem)
        self.assert_sem_cotacao(mensagem)
        self.assertNotIn("9,99", mensagem)
        self.assertNotIn("10,00", mensagem)
        self.assertNotIn("detalhe interno", mensagem)
        self.assert_handoff_invisivel(mensagem)

    def test_03_transporte_e2e_nao_apresenta_cotacao(self):
        self.criar_sessao("transporte", autenticada=True)
        self.provider.erro = ErroCambioProvider(
            CategoriaErroCambio.TRANSPORTE,
            "host interno indisponível",
        )
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda="EUR",
            texto_modelo="A cotação alternativa é 8,88.",
        )

        mensagem = self.processar("transporte", runner)

        self.assertEqual(["EUR"], self.provider.calls)
        self.assertIn("transporte", mensagem)
        self.assert_sem_cotacao(mensagem)
        self.assertNotIn("8,88", mensagem)

    def test_04_resposta_invalida_e2e_e_conservadora(self):
        self.criar_sessao("invalida", autenticada=True)
        self.provider.erro = ErroCambioProvider(
            CategoriaErroCambio.RESPOSTA_INVALIDA,
            "payload parcial com compra 7,77",
        )
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda="USD",
            texto_modelo="Use a compra parcial 7,77 como fallback.",
        )

        mensagem = self.processar("invalida", runner)

        self.assertEqual(["USD"], self.provider.calls)
        self.assertIn("resposta_invalida", mensagem)
        self.assert_sem_cotacao(mensagem)
        self.assertNotIn("7,77", mensagem)
        self.assertNotIn("payload parcial", mensagem)

    def test_05_moeda_invalida_nao_chega_ao_provider_ou_rede(self):
        self.criar_sessao("moeda-invalida", autenticada=True)
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda="https://exemplo.invalid/ZZZ",
            texto_modelo="A moeda vale 6,66.",
        )

        mensagem = self.processar("moeda-invalida", runner)

        self.assertEqual([], self.provider.calls)
        self.assertEqual(1, len(runner.tool_calls))
        self.assertIn("entrada_invalida", mensagem)
        self.assert_sem_cotacao(mensagem)
        self.assertNotIn("6,66", mensagem)
        self.assertNotIn("exemplo.invalid", mensagem)
        self.http_get.assert_not_called()

    def test_06_nao_autenticado_e_bloqueado_sem_expor_estado_ou_cpf(self):
        self.criar_sessao("nao-autenticada", autenticada=False)
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda="USD",
            texto_modelo=f"CPF {CPF_TESTE}: compra 5,55.",
        )

        mensagem = self.processar("nao-autenticada", runner)

        self.assertEqual([], self.provider.calls)
        self.assertIn("autorizacao", mensagem)
        self.assert_sem_cotacao(mensagem)
        self.assertNotIn(CPF_TESTE, mensagem)
        self.assertNotIn("authenticated", mensagem.casefold())
        self.assertNotIn("conversation_ended", mensagem.casefold())
        self.http_get.assert_not_called()

    def test_07_encerramento_antes_da_consulta_bloqueia_runner_e_provider(self):
        self.criar_sessao("encerrar-antes", autenticada=True)
        runner = CambioRunnerControlado(self.service, codigo_moeda="USD")

        mensagem = self.processar("encerrar-antes", runner, mensagem="tchau")

        self.assertEqual(orchestrator.MENSAGEM_ATENDIMENTO_ENCERRADO, mensagem)
        self.assertEqual([], runner.calls)
        self.assertEqual([], runner.tool_calls)
        self.assertEqual([], self.provider.calls)
        self.assertTrue(self.estado("encerrar-antes")[CONVERSATION_ENDED])

    def test_08_encerramento_apos_sucesso_nao_faz_nova_consulta(self):
        self.criar_sessao("encerrar-depois", autenticada=True)
        runner = CambioRunnerControlado(self.service, codigo_moeda="USD")

        primeira = self.processar("encerrar-depois", runner)
        segunda = self.processar("encerrar-depois", runner, mensagem="encerrar")

        self.assertIn("USD/BRL", primeira)
        self.assertEqual(orchestrator.MENSAGEM_ATENDIMENTO_ENCERRADO, segunda)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(["USD"], runner.tool_calls)
        self.assertEqual(["USD"], self.provider.calls)
        self.assertTrue(self.estado("encerrar-depois")[CONVERSATION_ENDED])

    def test_09_duas_sessoes_nao_cruzam_autorizacao_resultado_ou_estado(self):
        self.criar_sessao("sessao-a", autenticada=True)
        self.criar_sessao("sessao-b", autenticada=False)
        runner_a = CambioRunnerControlado(self.service, codigo_moeda="USD")
        runner_b = CambioRunnerControlado(
            self.service,
            codigo_moeda="EUR",
            texto_modelo="EUR vale 44,44 em outra sessão.",
        )

        mensagem_a = self.processar("sessao-a", runner_a)
        mensagem_b = self.processar("sessao-b", runner_b)

        self.assertIn("USD/BRL", mensagem_a)
        self.assertIn("1785502800", mensagem_a)
        self.assertIn("autorizacao", mensagem_b)
        self.assertNotIn("USD/BRL", mensagem_b)
        self.assertNotIn("EUR/BRL", mensagem_b)
        self.assertNotIn("1785502800", mensagem_b)
        self.assertNotIn("44,44", mensagem_b)
        self.assertEqual(["USD"], self.provider.calls)
        self.assertTrue(self.estado("sessao-a")[AUTHENTICATED])
        self.assertFalse(self.estado("sessao-b")[AUTHENTICATED])
        self.assertFalse(self.estado("sessao-a")[CONVERSATION_ENDED])
        self.assertFalse(self.estado("sessao-b")[CONVERSATION_ENDED])

    def test_10_fluxo_sem_cambio_preserva_runner_normal(self):
        self.criar_sessao("sem-cambio", autenticada=True)
        runner = CambioRunnerControlado(
            self.service,
            codigo_moeda=None,
            texto_modelo="Resposta normal sem consulta de câmbio.",
        )

        mensagem = self.processar("sem-cambio", runner, mensagem="olá")

        self.assertEqual("Resposta normal sem consulta de câmbio.", mensagem)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual([], runner.tool_calls)
        self.assertEqual([], self.provider.calls)
        self.http_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
