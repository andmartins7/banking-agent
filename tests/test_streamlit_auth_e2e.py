import copy
import csv
import hashlib
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PLACEHOLDER_API_KEY = "test-only-not-a-real-key"
_MISSING = object()
_previous_api_key = os.environ.get("GOOGLE_API_KEY", _MISSING)
os.environ["GOOGLE_API_KEY"] = PLACEHOLDER_API_KEY
with patch("dotenv.load_dotenv", return_value=False):
    import config
    import orchestrator
    from google.adk.events import Event, EventActions
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part
    from session_state import (
        AUTHENTICATED,
        AUTHENTICATED_CPF,
        AUTH_ATTEMPTS,
        CONVERSATION_ENDED,
    )
    from streamlit.testing.v1 import AppTest
    import tools.auth_tools as auth_tools
    import tools.cambio_provider as cambio_provider
    import tools.cambio_tools as cambio_tools
    import tools.credito_tools as credito_tools
    import tools.score_tools as score_tools

if _previous_api_key is _MISSING:
    os.environ.pop("GOOGLE_API_KEY", None)
else:
    os.environ["GOOGLE_API_KEY"] = _previous_api_key


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
VALID_CPF = "11111111111"
VALID_BIRTH = "01/01/1990"
WRONG_BIRTH = "31/12/1999"
OTHER_CPF = "22222222222"
ASK_CPF = "Para começar, informe seu CPF."
ASK_BIRTH = "Agora informe sua data de nascimento."
AUTH_SUCCESS = "Identidade confirmada com sucesso."
AUTH_FAILURE = "Não foi possível confirmar os dados informados."


class ControlledAuthenticationRunner:
    """Controla apenas turnos do LLM e delega toda autenticação à tool real."""

    def __init__(self, service):
        self.service = service
        self.stages = {}
        self.pending_cpfs = {}
        self.calls = []
        self.tool_calls = []
        self.tool_results = []
        self.events = []

    @staticmethod
    def _message_text(content):
        if content and content.parts:
            return content.parts[0].text or ""
        return ""

    def _append(self, session, event):
        orchestrator._run_async(
            self.service.append_event(session=session, event=event)
        )
        self.events.append(event)

    def _final_event(self, session, text):
        event = Event(
            author="runner_controlado_auth",
            content=Content(role="model", parts=[Part(text=text)]),
        )
        self._append(session, event)
        return event

    def _authenticate(self, session, session_id, cpf, birth):
        function_call_event = Event(
            author="runner_controlado_auth",
            content=Content(
                role="model",
                parts=[
                    Part.from_function_call(
                        name="autenticar_cliente",
                        args={"cpf": cpf, "data_nascimento": birth},
                    )
                ],
            ),
        )
        self._append(session, function_call_event)
        yield function_call_event

        state_before = copy.deepcopy(session.state)
        tool_context = SimpleNamespace(state=copy.deepcopy(session.state))
        result = auth_tools.autenticar_cliente(
            cpf,
            birth,
            tool_context=tool_context,
        )
        state_delta = {
            key: value
            for key, value in tool_context.state.items()
            if state_before.get(key) != value
        }

        self.tool_calls.append(
            {
                "session_id": session_id,
                "cpf_length": len(str(cpf)),
                "birth_supplied": bool(birth),
            }
        )
        self.tool_results.append(
            {
                "autenticado": bool(result.get("autenticado", False)),
                "tentativas_realizadas": result.get("tentativas_realizadas"),
                "encerrado": bool(result.get("encerrado", False)),
            }
        )

        function_response_event = Event(
            author="runner_controlado_auth",
            content=Content(
                role="user",
                parts=[
                    Part.from_function_response(
                        name="autenticar_cliente",
                        response=result,
                    )
                ],
            ),
            actions=EventActions(state_delta=state_delta),
        )
        self._append(session, function_response_event)
        yield function_response_event

        final_text = AUTH_SUCCESS if result.get("autenticado") else AUTH_FAILURE
        yield self._final_event(session, final_text)

    def run(self, **kwargs):
        self.calls.append(kwargs)
        session_id = kwargs["session_id"]
        message = self._message_text(kwargs["new_message"])
        session = orchestrator._run_async(
            self.service.get_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )
        if session is None:
            raise AssertionError("Sessão real não encontrada pelo Runner controlado.")

        user_event = Event(author="user", content=kwargs["new_message"])
        self._append(session, user_event)

        stage = self.stages.get(session_id, "initial")
        if stage == "initial":
            self.stages[session_id] = "cpf"
            yield self._final_event(session, ASK_CPF)
            return
        if stage == "cpf":
            self.pending_cpfs[session_id] = message
            self.stages[session_id] = "birth"
            yield self._final_event(session, ASK_BIRTH)
            return
        if stage != "birth":
            raise AssertionError(f"Estágio semântico inesperado: {stage}")

        cpf = self.pending_cpfs.pop(session_id)
        self.stages[session_id] = "cpf"
        yield from self._authenticate(session, session_id, cpf, message)


class StreamlitAuthenticationE2ETests(unittest.TestCase):
    def setUp(self):
        self._stack = ExitStack()
        self.addCleanup(self._cleanup)
        self.real_csvs = (
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        )
        self.real_hashes_before = self._hashes(self.real_csvs)

        self.base = Path(self._stack.enter_context(tempfile.TemporaryDirectory()))
        self.clients_path = self.base / "clientes.csv"
        self.score_path = self.base / "score_limite.csv"
        self.requests_path = self.base / "solicitacoes_aumento_limite.csv"
        self._write_clients()
        self._write_score()
        self._write_requests()

        self.service = InMemorySessionService()
        self.runner = ControlledAuthenticationRunner(self.service)

        self._stack.enter_context(
            patch.dict(os.environ, {"GOOGLE_API_KEY": PLACEHOLDER_API_KEY})
        )
        self._stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        self._stack.enter_context(
            patch.object(config, "GOOGLE_API_KEY", PLACEHOLDER_API_KEY)
        )
        self._stack.enter_context(
            patch.object(orchestrator, "GOOGLE_API_KEY", PLACEHOLDER_API_KEY)
        )
        self._stack.enter_context(
            patch.object(orchestrator, "_session_service", self.service)
        )
        self._stack.enter_context(patch.object(orchestrator, "_runner", self.runner))

        self._stack.enter_context(
            patch.object(auth_tools, "CSV_CLIENTES", self.clients_path)
        )
        self._stack.enter_context(
            patch.object(score_tools, "CSV_CLIENTES", self.clients_path)
        )
        self._stack.enter_context(
            patch.object(credito_tools, "CSV_CLIENTES", self.clients_path)
        )
        self._stack.enter_context(
            patch.object(credito_tools, "CSV_SCORE_LIMITE", self.score_path)
        )
        self._stack.enter_context(
            patch.object(
                credito_tools,
                "CSV_SOLICITACOES",
                self.requests_path,
            )
        )

        self.read_csv = self._stack.enter_context(
            patch.object(
                auth_tools.pd,
                "read_csv",
                wraps=auth_tools.pd.read_csv,
            )
        )
        self._install_network_guards()
        self.auth_tool = self._stack.enter_context(
            patch.object(
                auth_tools,
                "autenticar_cliente",
                wraps=auth_tools.autenticar_cliente,
            )
        )

    def _cleanup(self):
        self._stack.close()
        self.assertEqual(self.real_hashes_before, self._hashes(self.real_csvs))

    def _install_network_guards(self):
        allowed_hosts = {"127.0.0.1", "::1", "localhost"}
        real_connect = socket.socket.connect
        real_create_connection = socket.create_connection

        def guarded_connect(sock, address):
            host = address[0] if isinstance(address, tuple) else None
            if host in allowed_hosts:
                return real_connect(sock, address)
            raise AssertionError(f"Rede externa proibida: {host!r}")

        def guarded_create_connection(address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else None
            if host in allowed_hosts:
                return real_create_connection(address, *args, **kwargs)
            raise AssertionError(f"Rede externa proibida: {host!r}")

        self._stack.enter_context(
            patch("socket.socket.connect", new=guarded_connect)
        )
        self._stack.enter_context(
            patch("socket.create_connection", new=guarded_create_connection)
        )
        self._stack.enter_context(
            patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("urlopen externo proibido."),
            )
        )
        self.httpx_get = self._stack.enter_context(
            patch.object(
                cambio_provider.httpx,
                "get",
                side_effect=AssertionError("HTTP externo proibido."),
            )
        )
        self.provider = self._stack.enter_context(
            patch.object(
                cambio_tools,
                "AwesomeApiProvider",
                side_effect=AssertionError("Provider real proibido."),
            )
        )

    @staticmethod
    def _write_csv(path, fieldnames, rows):
        with Path(path).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _hashes(paths):
        return {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in paths
        }

    def _write_clients(self, *, duplicated=False):
        valid_client = {
            "cpf": VALID_CPF,
            "nome": "Cliente Fictício Vertical",
            "data_nascimento": VALID_BIRTH,
            "score_credito": "650",
            "limite_credito": "2500.00",
        }
        rows = [
            valid_client,
            {
                "cpf": OTHER_CPF,
                "nome": "Outro Cliente Fictício",
                "data_nascimento": "02/02/1992",
                "score_credito": "720",
                "limite_credito": "3200.00",
            },
        ]
        if duplicated:
            rows.insert(1, dict(valid_client))
        self._write_csv(
            self.clients_path,
            [
                "cpf",
                "nome",
                "data_nascimento",
                "score_credito",
                "limite_credito",
            ],
            rows,
        )

    def _write_score(self):
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [{"limite_maximo": "5000.00", "score_minimo": "700"}],
        )

    def _write_requests(self):
        self._write_csv(
            self.requests_path,
            [
                "cpf_cliente",
                "data_hora_solicitacao",
                "limite_atual",
                "novo_limite_solicitado",
                "status_pedido",
            ],
            [],
        )

    def _new_app(self):
        self.assertIs(sys.modules.get("orchestrator"), orchestrator)
        self.assertIs(orchestrator._runner, self.runner)
        self.assertIs(orchestrator._session_service, self.service)
        self.assertEqual(self.clients_path, auth_tools.CSV_CLIENTES)
        self.assertEqual(self.clients_path, score_tools.CSV_CLIENTES)
        self.assertEqual(self.clients_path, credito_tools.CSV_CLIENTES)
        self.assertEqual(self.score_path, credito_tools.CSV_SCORE_LIMITE)
        self.assertEqual(self.requests_path, credito_tools.CSV_SOLICITACOES)
        self.assertNotIn(self.clients_path, self.real_csvs)

        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        self.assertEqual([], list(app.exception))
        self.assertIn(ASK_CPF, self._markdown_values(app))
        return app

    @staticmethod
    def _markdown_values(app):
        return [element.value for element in app.markdown]

    @staticmethod
    def _assistant_values(app):
        return [
            markdown.value
            for message in app.chat_message
            if message.name == "assistant"
            for markdown in message.markdown
        ]

    @staticmethod
    def _submit(app, message):
        app.chat_input[0].set_value(message).run()
        return app

    def _session(self, app):
        session_id = app.session_state["session_id"]
        session = orchestrator._run_async(
            self.service.get_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )
        self.assertIsNotNone(session)
        return session

    def _authenticate(self, app, cpf, birth):
        self._submit(app, cpf)
        self.assertIn(ASK_BIRTH, self._markdown_values(app))
        self._submit(app, birth)
        return self._session(app).state

    def _assert_safe_assistant_output(self, app):
        rendered = "\n".join(self._assistant_values(app))
        for forbidden in (
            VALID_CPF,
            OTHER_CPF,
            VALID_BIRTH,
            WRONG_BIRTH,
            "650",
            "2500.00",
            "Outro Cliente Fictício",
            "Traceback",
            "agente_triagem",
            "handoff",
        ):
            self.assertNotIn(forbidden, rendered)

    def _assert_reads_only_temporary_clients(self, expected_count):
        self.assertEqual(expected_count, self.read_csv.call_count)
        for call in self.read_csv.call_args_list:
            self.assertEqual(self.clients_path, Path(call.args[0]))
            self.assertNotIn(Path(call.args[0]), self.real_csvs)

    def test_authentication_success_crosses_real_ui_orchestrator_state_and_tool(self):
        app = self._new_app()
        session_id = app.session_state["session_id"]
        state = self._authenticate(app, VALID_CPF, VALID_BIRTH)

        self.assertTrue(state[AUTHENTICATED])
        self.assertEqual(VALID_CPF, state[AUTHENTICATED_CPF])
        self.assertEqual(0, state[AUTH_ATTEMPTS])
        self.assertFalse(state[CONVERSATION_ENDED])
        self.assertFalse(app.session_state["ended"])
        self.assertEqual(1, len(app.chat_input))
        self.assertIn(AUTH_SUCCESS, self._markdown_values(app))
        self.assertEqual(1, self.auth_tool.call_count)
        self.assertEqual(
            (VALID_CPF, VALID_BIRTH),
            self.auth_tool.call_args.args[:2],
        )
        self.assertEqual(1, len(self.runner.tool_calls))
        self.assertEqual(11, self.runner.tool_calls[0]["cpf_length"])
        self.assertTrue(self.runner.tool_results[0]["autenticado"])
        self.assertEqual(0, self.runner.tool_results[0]["tentativas_realizadas"])
        self.assertTrue(
            all(call["session_id"] == session_id for call in self.runner.tool_calls)
        )
        self.assertTrue(
            all(call["session_id"] == session_id for call in self.runner.calls)
        )
        self.assertEqual(
            [
                ("assistant", ASK_CPF),
                ("user", VALID_CPF),
                ("assistant", ASK_BIRTH),
                ("user", VALID_BIRTH),
                ("assistant", AUTH_SUCCESS),
            ],
            [
                (message.name, markdown.value)
                for message in app.chat_message
                for markdown in message.markdown
            ],
        )
        function_calls = [
            event for event in self.runner.events if event.get_function_calls()
        ]
        function_responses = [
            event for event in self.runner.events if event.get_function_responses()
        ]
        self.assertEqual(1, len(function_calls))
        self.assertEqual(1, len(function_responses))
        self.assertEqual(
            "autenticar_cliente",
            function_calls[0].get_function_calls()[0].name,
        )
        self.assertFalse(function_calls[0].is_final_response())
        self.assertFalse(function_responses[0].is_final_response())
        self.assertTrue(function_responses[0].actions.state_delta[AUTHENTICATED])
        self.assertTrue(any(
            event.is_final_response()
            and event.content
            and event.content.parts
            and event.content.parts[0].text == AUTH_SUCCESS
            for event in self.runner.events
        ))
        self._assert_reads_only_temporary_clients(1)
        self._assert_safe_assistant_output(app)
        self.httpx_get.assert_not_called()
        self.provider.assert_not_called()

    def test_three_real_failures_end_ui_and_prevent_a_fourth_attempt(self):
        app = self._new_app()
        stale_input = app.chat_input[0]

        for expected_attempt in (1, 2):
            state = self._authenticate(app, VALID_CPF, WRONG_BIRTH)
            self.assertFalse(state[AUTHENTICATED])
            self.assertEqual(expected_attempt, state[AUTH_ATTEMPTS])
            self.assertFalse(state[CONVERSATION_ENDED])
            self.assertFalse(app.session_state["ended"])
            self.assertEqual(1, len(app.chat_input))

        state = self._authenticate(app, VALID_CPF, WRONG_BIRTH)
        self.assertFalse(state[AUTHENTICATED])
        self.assertEqual(3, state[AUTH_ATTEMPTS])
        self.assertTrue(state[CONVERSATION_ENDED])
        self.assertTrue(app.session_state["ended"])
        self.assertTrue(
            any("Atendimento encerrado" in item.value for item in app.info)
        )
        self.assertEqual(3, self.auth_tool.call_count)
        self.assertEqual(3, len(self.runner.tool_calls))
        self.assertEqual([1, 2, 3], [
            item["tentativas_realizadas"] for item in self.runner.tool_results
        ])
        self.assertEqual([False, False, True], [
            item["encerrado"] for item in self.runner.tool_results
        ])
        self._assert_reads_only_temporary_clients(3)

        runner_calls_before = len(self.runner.calls)
        process_calls_before = len(self.runner.tool_calls)
        auth_calls_before = self.auth_tool.call_count
        csv_reads_before = self.read_csv.call_count
        stale_input.set_value(VALID_CPF).run()
        app.run()

        self.assertEqual(runner_calls_before, len(self.runner.calls))
        self.assertEqual(process_calls_before, len(self.runner.tool_calls))
        self.assertEqual(auth_calls_before, self.auth_tool.call_count)
        self.assertEqual(csv_reads_before, self.read_csv.call_count)
        self.assertNotIn(AUTH_SUCCESS, self._markdown_values(app))
        self._assert_safe_assistant_output(app)

    def test_sessions_and_visual_history_do_not_cross_or_define_auth_state(self):
        positive = self._new_app()
        negative = self._new_app()
        positive_id = positive.session_state["session_id"]
        negative_id = negative.session_state["session_id"]
        self.assertNotEqual(positive_id, negative_id)

        positive_state = self._authenticate(positive, VALID_CPF, VALID_BIRTH)
        negative_state = self._authenticate(negative, VALID_CPF, WRONG_BIRTH)

        self.assertTrue(positive_state[AUTHENTICATED])
        self.assertEqual(0, positive_state[AUTH_ATTEMPTS])
        self.assertFalse(negative_state[AUTHENTICATED])
        self.assertEqual(1, negative_state[AUTH_ATTEMPTS])
        self.assertFalse(negative_state[CONVERSATION_ENDED])

        state_before_visual_edit = copy.deepcopy(self._session(negative).state)
        runner_calls_before = len(self.runner.calls)
        negative.session_state["messages"] = [
            {"role": "assistant", "content": AUTH_SUCCESS}
        ]
        negative.run()

        self.assertEqual(state_before_visual_edit, self._session(negative).state)
        self.assertEqual(runner_calls_before, len(self.runner.calls))
        self.assertFalse(self._session(negative).state[AUTHENTICATED])
        self.assertEqual(1, self._session(negative).state[AUTH_ATTEMPTS])
        self.assertTrue(self._session(positive).state[AUTHENTICATED])

    def test_duplicate_cpf_is_blocked_vertically_without_record_disclosure(self):
        self._write_clients(duplicated=True)
        app = self._new_app()
        state = self._authenticate(app, VALID_CPF, VALID_BIRTH)

        self.assertFalse(state[AUTHENTICATED])
        self.assertIsNone(state[AUTHENTICATED_CPF])
        self.assertEqual(1, state[AUTH_ATTEMPTS])
        self.assertFalse(state[CONVERSATION_ENDED])
        self.assertEqual(1, self.auth_tool.call_count)
        self.assertFalse(self.runner.tool_results[0]["autenticado"])
        self.assertIn(AUTH_FAILURE, self._markdown_values(app))
        self._assert_reads_only_temporary_clients(1)
        self._assert_safe_assistant_output(app)


if __name__ == "__main__":
    unittest.main()
