import copy
import inspect
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.sessions.state import State
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

import tests.test_streamlit_auth_e2e as auth_e2e
import tests.test_streamlit_credit_e2e as credit_e2e
from session_state import (
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_COLLECTING,
    CREDIT_INTERVIEW_STATUS,
)
from tools.cambio_provider import (
    CategoriaErroCambio,
    CotacaoCambio,
    ErroCambioProvider,
)


FX_INTENT = "Qual a cotação do dólar?"
INVALID_FX_INTENT = "Qual a cotação de ZZZ?"
FX_AUTH_REQUIRED = "Para consultar câmbio, conclua primeiro a autenticação."
REAL_FX_TOOL = auth_e2e.cambio_tools.buscar_cotacao
REAL_FX_RENDERER = auth_e2e.orchestrator.renderizar_resultado_cotacao


class FakeFxProvider:
    """Substitui somente a fronteira externa e registra a moeda consultada."""

    def __init__(self):
        self.calls = []
        self.error = None

    def consultar(self, codigo_moeda):
        self.calls.append(codigo_moeda)
        if self.error is not None:
            raise self.error
        return CotacaoCambio(
            moeda_origem=codigo_moeda,
            moeda_destino="BRL",
            nome="Dólar Americano/Real Brasileiro",
            cotacao_compra=Decimal("5.123400"),
            cotacao_venda=Decimal("5.133400"),
            variacao_pct=Decimal("-0.5200"),
            timestamp_fonte=1785502800,
            data_atualizacao_fonte=datetime(2026, 7, 31, 10, 0, 0),
            provider="AwesomeAPI",
        )


class ControlledFxRunner(auth_e2e.ControlledAuthenticationRunner):
    """Escolhe Câmbio e deixa autorização, provider e apresentação reais."""

    def __init__(self, service):
        super().__init__(service)
        self.fx_calls = []
        self.fx_results = []
        self.fx_contexts = []
        self.tool_context_count = 0

    def _session(self, session_id):
        session = auth_e2e.orchestrator._run_async(
            self.service.get_session(
                app_name=auth_e2e.orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )
        if session is None:
            raise AssertionError("Sessão real não encontrada pelo Runner controlado.")
        return session

    def _tool_context(self, session, user_content):
        self.tool_context_count += 1
        invocation = InvocationContext(
            session_service=self.service,
            invocation_id=f"fx-{self.tool_context_count}",
            agent=auth_e2e.orchestrator.agente_triagem,
            user_content=user_content,
            session=session,
        )
        return ToolContext(
            invocation,
            function_call_id=f"fx-call-{self.tool_context_count}",
        )

    def _run_fx(self, session, session_id, user_content, codigo_moeda):
        function_args = {"codigo_moeda": codigo_moeda}
        function_call = Event(
            author="runner_controlado_fx",
            content=Content(
                role="model",
                parts=[
                    Part.from_function_call(
                        name="buscar_cotacao",
                        args=function_args,
                    )
                ],
            ),
        )
        self._append(session, function_call)
        yield function_call

        state_before = copy.deepcopy(session.state)
        tool_context = self._tool_context(session, user_content)
        result = auth_e2e.cambio_tools.buscar_cotacao(
            codigo_moeda,
            tool_context=tool_context,
        )
        self.fx_calls.append({
            "session_id": session_id,
            "args": copy.deepcopy(function_args),
        })
        self.fx_results.append(copy.deepcopy(result))
        self.fx_contexts.append(tool_context)

        function_response = Event(
            author="runner_controlado_fx",
            content=Content(
                role="user",
                parts=[
                    Part.from_function_response(
                        name="buscar_cotacao",
                        response=result,
                    )
                ],
            ),
            actions=tool_context.actions,
        )
        self._append(session, function_response)
        yield function_response

        if session.state != state_before:
            raise AssertionError("A consulta de câmbio alterou o estado bancário.")
        yield self._final_event(
            session,
            "Resposta intermediária controlada do Runner.",
        )

    def run(self, **kwargs):
        message = self._message_text(kwargs["new_message"])
        normalized = message.strip().casefold()
        codigo_moeda = None
        if normalized == FX_INTENT.casefold():
            codigo_moeda = "USD"
        elif normalized == INVALID_FX_INTENT.casefold():
            codigo_moeda = "ZZZ"

        if codigo_moeda is None:
            yield from super().run(**kwargs)
            return

        self.calls.append(kwargs)
        session_id = kwargs["session_id"]
        session = self._session(session_id)
        self._append(session, Event(author="user", content=kwargs["new_message"]))

        if not session.state.get(auth_e2e.AUTHENTICATED, False):
            yield self._final_event(session, FX_AUTH_REQUIRED)
            return

        yield from self._run_fx(
            session,
            session_id,
            kwargs["new_message"],
            codigo_moeda,
        )


class StreamlitFxE2ETests(unittest.TestCase):
    def setUp(self):
        self.harness = auth_e2e.StreamlitAuthenticationE2ETests(
            "test_authentication_success_crosses_real_ui_orchestrator_state_and_tool"
        )
        self.addCleanup(self.harness.doCleanups)
        self.harness.setUp()

        self.fake_provider = FakeFxProvider()
        self.runner = ControlledFxRunner(self.harness.service)
        self._install_runner(self.runner)

        provider_patch = patch.object(
            auth_e2e.cambio_tools,
            "AwesomeApiProvider",
            return_value=self.fake_provider,
        )
        self.provider_factory = provider_patch.start()
        self.addCleanup(provider_patch.stop)
        tool_patch = patch.object(
            auth_e2e.cambio_tools,
            "buscar_cotacao",
            wraps=REAL_FX_TOOL,
        )
        self.fx_tool = tool_patch.start()
        self.addCleanup(tool_patch.stop)
        renderer_patch = patch.object(
            auth_e2e.orchestrator,
            "renderizar_resultado_cotacao",
            wraps=REAL_FX_RENDERER,
        )
        self.fx_renderer = renderer_patch.start()
        self.addCleanup(renderer_patch.stop)

    def _install_runner(self, runner):
        self.runner = runner
        self.harness.runner = runner
        runner_patch = patch.object(auth_e2e.orchestrator, "_runner", runner)
        runner_patch.start()
        self.addCleanup(runner_patch.stop)

    def _authenticate(self, app):
        state = self.harness._authenticate(
            app,
            auth_e2e.VALID_CPF,
            auth_e2e.VALID_BIRTH,
        )
        self.assertTrue(state[auth_e2e.AUTHENTICATED])
        return state

    def _assistant_text(self, app):
        return "\n".join(self.harness._assistant_values(app))

    def _assert_safe_output(self, app):
        rendered = self._assistant_text(app).casefold()
        forbidden = (
            auth_e2e.VALID_CPF,
            auth_e2e.OTHER_CPF,
            auth_e2e.VALID_BIRTH,
            "02/02/1992",
            "650",
            "2500.00",
            "3200.00",
            "cliente fictício vertical",
            "outro cliente fictício",
            "traceback",
            "agente de triagem",
            "agente de crédito",
            "agente de entrevista",
            "agente de câmbio",
            "agente_triagem",
            "agente_credito",
            "agente_entrevista",
            "agente_cambio",
            "transferindo",
            "handoff",
        )
        for value in forbidden:
            with self.subTest(forbidden=value):
                self.assertNotIn(value.casefold(), rendered)
        self.assertTrue(
            all(
                message.name in {"assistant", "user"}
                for message in app.chat_message
            )
        )

    def _assert_ended_and_blocked(self, app, blocked_message):
        chat_input = app.chat_input[0]
        runner_calls_before = len(self.runner.calls)
        tool_calls_before = self.fx_tool.call_count
        provider_calls_before = list(self.fake_provider.calls)

        self.harness._submit(app, "encerrar")

        self.assertTrue(app.session_state["ended"])
        self.assertTrue(
            self.harness._session(app).state[CONVERSATION_ENDED]
        )
        self.assertTrue(
            any("Atendimento encerrado" in item.value for item in app.info)
        )
        self.assertEqual(runner_calls_before, len(self.runner.calls))
        self.assertEqual(tool_calls_before, self.fx_tool.call_count)
        self.assertEqual(provider_calls_before, self.fake_provider.calls)

        # AppTest 1.40 retém o proxy do último evento após st.rerun; quando
        # st.stop encerra a execução antes do widget, esse proxy deve ser inerte.
        chat_input.set_value(blocked_message).run()
        app.run()

        self.assertEqual(runner_calls_before, len(self.runner.calls))
        self.assertEqual(tool_calls_before, self.fx_tool.call_count)
        self.assertEqual(provider_calls_before, self.fake_provider.calls)
        self.assertNotIn(blocked_message, self.harness._markdown_values(app))

    def test_pre_auth_blocks_quote_without_calling_tool_or_provider(self):
        app = self.harness._new_app()

        self.harness._submit(app, FX_INTENT)

        self.assertIn(FX_AUTH_REQUIRED, self.harness._assistant_values(app))
        self.assertEqual([], self.runner.fx_calls)
        self.assertEqual([], self.fake_provider.calls)
        self.fx_tool.assert_not_called()
        self.provider_factory.assert_not_called()
        rendered_before_auth = self._assistant_text(app)
        self.assertNotIn("Cotação validada", rendered_before_auth)
        self.assertNotIn("Compra:", rendered_before_auth)
        self.assertNotIn("Venda:", rendered_before_auth)
        self.assertNotIn("Variação:", rendered_before_auth)
        state = self._authenticate(app)
        self.assertFalse(state[CONVERSATION_ENDED])
        self._assert_safe_output(app)

    def test_success_uses_real_tool_provider_contract_renderer_and_then_ends(self):
        app = self.harness._new_app()
        self._authenticate(app)
        state_before = copy.deepcopy(self.harness._session(app).state)

        self.harness._submit(app, FX_INTENT)

        self.assertEqual(["USD"], self.fake_provider.calls)
        self.assertEqual(1, self.provider_factory.call_count)
        self.assertEqual(1, self.fx_tool.call_count)
        self.assertEqual(1, self.fx_renderer.call_count)
        self.assertEqual(("USD",), self.fx_tool.call_args.args)
        self.assertEqual(
            {"tool_context": self.runner.fx_contexts[0]},
            self.fx_tool.call_args.kwargs,
        )
        self.assertEqual(
            ("codigo_moeda", "tool_context"),
            tuple(inspect.signature(REAL_FX_TOOL).parameters),
        )
        self.assertIsInstance(self.runner.fx_contexts[0], ToolContext)
        self.assertIsInstance(self.runner.fx_contexts[0].state, State)
        self.assertEqual(state_before, self.harness._session(app).state)
        self.assertEqual(
            self.runner.fx_results[0],
            self.fx_renderer.call_args.args[0],
        )

        expected = REAL_FX_RENDERER(self.runner.fx_results[0])
        self.assertEqual(expected, self.harness._assistant_values(app)[-1])
        self.assertIn("Compra: 5.123400 BRL", expected)
        self.assertIn("Venda: 5.133400 BRL", expected)
        self.assertIn("Variação: -0.5200%", expected)
        self.assertIn("Fonte: AwesomeAPI", expected)
        self.assertIn("2026-07-31 10:00:00", expected)
        self.assertIn("Timestamp Unix informado pela fonte: 1785502800", expected)
        self.assertIn("Fuso horário da data textual: não informado", expected)
        self.assertNotIn("UTC", expected)
        self.assertNotIn("GMT", expected)
        self.assertNotIn("Resposta intermediária", expected)
        self.harness.httpx_get.assert_not_called()
        self._assert_safe_output(app)

        self._assert_ended_and_blocked(app, "nova cotação após encerramento")

    def test_timeout_is_controlled_without_values_and_session_remains_usable(self):
        app = self.harness._new_app()
        self._authenticate(app)
        self.fake_provider.error = ErroCambioProvider(
            CategoriaErroCambio.TIMEOUT,
            "detalhe interno que não pode aparecer",
        )

        self.harness._submit(app, FX_INTENT)

        timeout_message = self.harness._assistant_values(app)[-1]
        self.assertEqual(["USD"], self.fake_provider.calls)
        self.assertIn("Falha na consulta de câmbio (timeout)", timeout_message)
        self.assertIn("Tente novamente em instantes", timeout_message)
        self.assertNotIn("Compra:", timeout_message)
        self.assertNotIn("Venda:", timeout_message)
        self.assertNotIn("Variação:", timeout_message)
        self.assertNotIn("0.00", timeout_message)
        self.assertNotIn("detalhe interno", timeout_message)
        self.assertNotIn("Traceback", timeout_message)
        self.assertTrue(self.harness._session(app).state[auth_e2e.AUTHENTICATED])
        self.assertFalse(self.harness._session(app).state[CONVERSATION_ENDED])

        self.fake_provider.error = None
        self.harness._submit(app, FX_INTENT)
        self.assertEqual(["USD", "USD"], self.fake_provider.calls)
        self.assertIn("Cotação validada — USD/BRL", self._assistant_text(app))
        self.harness.httpx_get.assert_not_called()
        self._assert_safe_output(app)

    def test_invalid_currency_is_rejected_before_provider_consultation(self):
        app = self.harness._new_app()
        self._authenticate(app)

        self.harness._submit(app, INVALID_FX_INTENT)

        message = self.harness._assistant_values(app)[-1]
        self.assertEqual([], self.fake_provider.calls)
        self.assertEqual(1, self.provider_factory.call_count)
        self.assertEqual(1, self.fx_tool.call_count)
        self.assertIn("entrada_invalida", message)
        self.assertIn("Moeda não suportada", message)
        self.assertNotIn("Cotação validada", message)
        self.assertNotIn("Compra:", message)
        self.assertNotIn("Venda:", message)
        self.harness.httpx_get.assert_not_called()
        self._assert_safe_output(app)

    def test_termination_after_authentication_preempts_runner_and_provider(self):
        app = self.harness._new_app()
        self._authenticate(app)

        self._assert_ended_and_blocked(app, "cotação bloqueada após autenticação")

        self.assertEqual([], self.fake_provider.calls)
        self._assert_safe_output(app)

    def test_termination_during_interview_preempts_runner_and_provider(self):
        credit_runner = credit_e2e.ControlledCreditRunner(self.harness.service)
        self._install_runner(credit_runner)
        app = self.harness._new_app()
        self._authenticate(app)
        self.harness._submit(app, credit_e2e.INCREASE_INTENT)
        self.harness._submit(app, credit_e2e.APPROVED_LIMIT_INPUT)
        self.harness._submit(app, "sim")
        self.assertEqual(
            CREDIT_INTERVIEW_COLLECTING,
            self.harness._session(app).state[CREDIT_INTERVIEW_STATUS],
        )

        self._assert_ended_and_blocked(app, "resposta após entrevista encerrada")

        self.assertEqual([], self.fake_provider.calls)
        self._assert_safe_output(app)


if __name__ == "__main__":
    unittest.main()
