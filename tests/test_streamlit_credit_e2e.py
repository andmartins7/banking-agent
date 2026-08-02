import copy
import csv
import inspect
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part
from session_state import (
    CREDIT_INTERVIEW_COLLECTING,
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_FIELDS,
    CREDIT_INTERVIEW_NOT_OFFERED,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
)

import tests.test_streamlit_auth_e2e as auth_e2e


CREDIT_INTENT = "quero consultar meu limite"
INCREASE_INTENT = "quero aumentar meu limite"
ASK_NEW_LIMIT = "Informe o novo limite desejado."
AUTH_REQUIRED = "Para consultar seu limite, conclua primeiro a autenticação."
LIMIT_A = 2500.0
LIMIT_B = 3200.0
APPROVED_LIMIT_INPUT = "3000.00"
APPROVED_LIMIT = 3000.0
REAL_CONSULT_LIMIT = auth_e2e.credito_tools.consultar_limite
REAL_REGISTER_REQUEST = auth_e2e.credito_tools.registrar_solicitacao
REAL_PROCESS_REQUEST = auth_e2e.credito_tools.processar_solicitacao
REAL_CREDIT_POLICY = auth_e2e.credito_tools._avaliar_politica_credito
REAL_PUBLISH_APPROVAL = auth_e2e.credito_tools._publicar_aprovacao
REAL_VALIDATE_INTERVIEW_RESPONSE = (
    auth_e2e.orchestrator.validar_resposta_entrevista
)
REAL_PROCESS_INTERVIEW = (
    auth_e2e.orchestrator.processar_entrevista_credito_autorizada
)
REAL_REANALYZE_REQUEST = (
    auth_e2e.orchestrator.reanalisar_solicitacao_autorizada
)
REAL_SCORE_FORMULA = auth_e2e.score_tools._calcular_score_oficial


class ControlledCreditRunner(auth_e2e.ControlledAuthenticationRunner):
    """Seleciona a tool; autorização e consulta permanecem no backend real."""

    def __init__(self, service):
        super().__init__(service)
        self.credit_calls = []
        self.credit_results = []
        self.credit_contexts = []
        self.increase_stages = set()
        self.increase_calls = []
        self.increase_results = []
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
            invocation_id=f"credit-{self.tool_context_count}",
            agent=auth_e2e.orchestrator.agente_triagem,
            user_content=user_content,
            session=session,
        )
        return ToolContext(
            invocation,
            function_call_id=f"credit-call-{self.tool_context_count}",
        )

    def _function_call(self, session, name, args):
        event = Event(
            author="runner_controlado_credito",
            content=Content(
                role="model",
                parts=[Part.from_function_call(name=name, args=args)],
            ),
        )
        self._append(session, event)
        return event

    def _function_response(self, session, name, result, tool_context):
        event = Event(
            author="runner_controlado_credito",
            content=Content(
                role="user",
                parts=[
                    Part.from_function_response(
                        name=name,
                        response=result,
                    )
                ],
            ),
            actions=tool_context.actions,
        )
        self._append(session, event)
        return event

    def _consult_limit(self, session, session_id, user_content):
        function_call_event = Event(
            author="runner_controlado_credito",
            content=Content(
                role="model",
                parts=[
                    Part.from_function_call(
                        name="consultar_limite",
                        args={},
                    )
                ],
            ),
        )
        self._append(session, function_call_event)
        yield function_call_event

        state_before = copy.deepcopy(session.state)
        tool_context = self._tool_context(session, user_content)
        result = auth_e2e.credito_tools.consultar_limite(
            tool_context=tool_context,
        )

        self.credit_calls.append(
            {
                "session_id": session_id,
                "function_args": {},
            }
        )
        self.credit_contexts.append(tool_context)
        self.credit_results.append(copy.deepcopy(result))

        function_response_event = Event(
            author="runner_controlado_credito",
            content=Content(
                role="user",
                parts=[
                    Part.from_function_response(
                        name="consultar_limite",
                        response=result,
                    )
                ],
            ),
            actions=tool_context.actions,
        )
        self._append(session, function_response_event)
        yield function_response_event

        if result.get("erro"):
            final_text = "Não foi possível consultar seu limite neste momento."
        else:
            final_text = (
                "Seu limite atual é "
                f"R$ {float(result['limite_atual']):.2f}."
            )
        if session.state != state_before:
            raise AssertionError("A consulta de limite alterou o estado bancário.")
        yield self._final_event(session, final_text)

    def _request_increase(self, session, session_id, user_content, raw_value):
        register_args = {"novo_limite_solicitado": raw_value}
        yield self._function_call(
            session,
            "registrar_solicitacao",
            register_args,
        )
        register_context = self._tool_context(session, user_content)
        register_result = auth_e2e.credito_tools.registrar_solicitacao(
            raw_value,
            tool_context=register_context,
        )
        self.increase_calls.append(
            {
                "name": "registrar_solicitacao",
                "session_id": session_id,
                "args": copy.deepcopy(register_args),
            }
        )
        self.increase_results.append(copy.deepcopy(register_result))
        yield self._function_response(
            session,
            "registrar_solicitacao",
            register_result,
            register_context,
        )

        if not register_result.get("registrado") or register_result.get("erro"):
            yield self._final_event(
                session,
                "Não foi possível registrar sua solicitação.",
            )
            return

        process_args = {
            "data_hora_solicitacao": register_result["data_hora"],
        }
        yield self._function_call(
            session,
            "processar_solicitacao",
            process_args,
        )
        process_context = self._tool_context(session, user_content)
        process_result = auth_e2e.credito_tools.processar_solicitacao(
            register_result["data_hora"],
            tool_context=process_context,
        )
        self.increase_calls.append(
            {
                "name": "processar_solicitacao",
                "session_id": session_id,
                "args": copy.deepcopy(process_args),
            }
        )
        self.increase_results.append(copy.deepcopy(process_result))
        yield self._function_response(
            session,
            "processar_solicitacao",
            process_result,
            process_context,
        )

        if process_result.get("erro"):
            final_text = "Não foi possível processar sua solicitação."
        elif process_result.get("status_pedido") == "aprovado":
            final_text = (
                "Sua solicitação foi aprovada. Seu novo limite é "
                f"R$ {float(process_result['novo_limite']):.2f}."
            )
        else:
            final_text = "Sua solicitação não foi aprovada."
        yield self._final_event(session, final_text)

    def run(self, **kwargs):
        session_id = kwargs["session_id"]
        message = self._message_text(kwargs["new_message"])
        is_credit_intent = message.strip().casefold() == CREDIT_INTENT.casefold()
        is_increase_intent = (
            message.strip().casefold() == INCREASE_INTENT.casefold()
        )
        is_increase_value = session_id in self.increase_stages

        if not (is_credit_intent or is_increase_intent or is_increase_value):
            yield from super().run(**kwargs)
            return

        self.calls.append(kwargs)
        session = self._session(session_id)
        user_event = Event(author="user", content=kwargs["new_message"])
        self._append(session, user_event)

        if not session.state.get(auth_e2e.AUTHENTICATED, False):
            yield self._final_event(session, AUTH_REQUIRED)
            return

        if is_increase_intent:
            self.increase_stages.add(session_id)
            yield self._final_event(session, ASK_NEW_LIMIT)
            return

        if is_increase_value:
            self.increase_stages.remove(session_id)
            yield from self._request_increase(
                session,
                session_id,
                kwargs["new_message"],
                message.strip(),
            )
            return

        yield from self._consult_limit(session, session_id, kwargs["new_message"])


class StreamlitCreditE2ETests(unittest.TestCase):
    def setUp(self):
        self.harness = auth_e2e.StreamlitAuthenticationE2ETests(
            "test_authentication_success_crosses_real_ui_orchestrator_state_and_tool"
        )
        self.addCleanup(self.harness.doCleanups)
        self.harness.setUp()
        self.harness._write_csv(
            self.harness.score_path,
            ["limite_maximo", "score_minimo"],
            [
                {"limite_maximo": "3000.00", "score_minimo": "600"},
                {"limite_maximo": "5000.00", "score_minimo": "700"},
            ],
        )

        self.runner = ControlledCreditRunner(self.harness.service)
        self.harness.runner = self.runner
        self.runner_patch = patch.object(
            auth_e2e.orchestrator,
            "_runner",
            self.runner,
        )
        self.runner_patch.start()
        self.addCleanup(self.runner_patch.stop)

        self.credit_patch = patch.object(
            auth_e2e.credito_tools,
            "consultar_limite",
            wraps=REAL_CONSULT_LIMIT,
        )
        self.credit_tool = self.credit_patch.start()
        self.addCleanup(self.credit_patch.stop)

        self.register_patch = patch.object(
            auth_e2e.credito_tools,
            "registrar_solicitacao",
            wraps=REAL_REGISTER_REQUEST,
        )
        self.register_tool = self.register_patch.start()
        self.addCleanup(self.register_patch.stop)

        self.process_patch = patch.object(
            auth_e2e.credito_tools,
            "processar_solicitacao",
            wraps=REAL_PROCESS_REQUEST,
        )
        self.process_tool = self.process_patch.start()
        self.addCleanup(self.process_patch.stop)

        self.policy_patch = patch.object(
            auth_e2e.credito_tools,
            "_avaliar_politica_credito",
            wraps=REAL_CREDIT_POLICY,
        )
        self.policy = self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

        self.publish_patch = patch.object(
            auth_e2e.credito_tools,
            "_publicar_aprovacao",
            wraps=REAL_PUBLISH_APPROVAL,
        )
        self.publish_approval = self.publish_patch.start()
        self.addCleanup(self.publish_patch.stop)

        self.validate_response_patch = patch.object(
            auth_e2e.orchestrator,
            "validar_resposta_entrevista",
            wraps=REAL_VALIDATE_INTERVIEW_RESPONSE,
        )
        self.validate_response = self.validate_response_patch.start()
        self.addCleanup(self.validate_response_patch.stop)

        self.process_interview_patch = patch.object(
            auth_e2e.orchestrator,
            "processar_entrevista_credito_autorizada",
            wraps=REAL_PROCESS_INTERVIEW,
        )
        self.process_interview = self.process_interview_patch.start()
        self.addCleanup(self.process_interview_patch.stop)

        self.reanalyze_patch = patch.object(
            auth_e2e.orchestrator,
            "reanalisar_solicitacao_autorizada",
            wraps=REAL_REANALYZE_REQUEST,
        )
        self.reanalyze = self.reanalyze_patch.start()
        self.addCleanup(self.reanalyze_patch.stop)

    def _authenticate_a(self, app):
        state = self.harness._authenticate(
            app,
            auth_e2e.VALID_CPF,
            auth_e2e.VALID_BIRTH,
        )
        self.assertTrue(state[auth_e2e.AUTHENTICATED])
        self.assertEqual(
            auth_e2e.VALID_CPF,
            state[auth_e2e.AUTHENTICATED_CPF],
        )
        return state

    def _assert_safe_credit_output(self, app):
        rendered = "\n".join(self.harness._assistant_values(app))
        for forbidden in (
            auth_e2e.VALID_CPF,
            auth_e2e.OTHER_CPF,
            auth_e2e.VALID_BIRTH,
            "02/02/1992",
            "650",
            "720",
            "800",
            "Cliente Fictício Vertical",
            "Outro Cliente Fictício",
            "agente_credito",
            "handoff",
            "Traceback",
        ):
            self.assertNotIn(forbidden, rendered)

    @staticmethod
    def _read_csv(path):
        with Path(path).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _by_key(rows, key):
        return {row[key]: row for row in rows}

    def test_authenticated_ui_consults_only_session_clients_limit(self):
        app_a = self.harness._new_app()
        session_id_a = app_a.session_state["session_id"]
        self._authenticate_a(app_a)
        reads_after_auth = self.harness.read_csv.call_count

        self.harness._submit(app_a, CREDIT_INTENT)

        state_after = self.harness._session(app_a).state
        self.assertTrue(state_after[auth_e2e.AUTHENTICATED])
        self.assertEqual(
            auth_e2e.VALID_CPF,
            state_after[auth_e2e.AUTHENTICATED_CPF],
        )
        self.assertEqual(1, self.credit_tool.call_count)
        self.assertEqual(1, len(self.runner.credit_calls))
        self.assertEqual(
            session_id_a,
            self.runner.credit_calls[0]["session_id"],
        )
        self.assertEqual({}, self.runner.credit_calls[0]["function_args"])
        self.assertEqual(
            {"tool_context"},
            set(inspect.signature(REAL_CONSULT_LIMIT).parameters),
        )
        self.assertNotIn("cpf", self.credit_tool.call_args.kwargs)
        self.assertIsInstance(
            self.credit_tool.call_args.kwargs["tool_context"],
            ToolContext,
        )
        self.assertEqual(LIMIT_A, self.runner.credit_results[0]["limite_atual"])
        self.assertNotEqual(LIMIT_B, self.runner.credit_results[0]["limite_atual"])
        self.assertIsNone(self.runner.credit_results[0]["erro"])
        self.assertEqual(reads_after_auth + 1, self.harness.read_csv.call_count)
        self.assertEqual(
            self.harness.clients_path,
            self.harness.read_csv.call_args.args[0],
        )

        rendered = "\n".join(self.harness._assistant_values(app_a))
        self.assertIn(f"{LIMIT_A:.2f}", rendered)
        self.assertNotIn(f"{LIMIT_B:.2f}", rendered)
        self._assert_safe_credit_output(app_a)
        self.harness.httpx_get.assert_not_called()
        self.harness.provider.assert_not_called()

        function_calls = [
            event
            for event in self.runner.events
            for call in event.get_function_calls()
            if call.name == "consultar_limite"
        ]
        function_responses = [
            event
            for event in self.runner.events
            for response in event.get_function_responses()
            if response.name == "consultar_limite"
        ]
        self.assertEqual(1, len(function_calls))
        self.assertEqual({}, function_calls[0].get_function_calls()[0].args)
        self.assertEqual(1, len(function_responses))
        self.assertEqual({}, function_responses[0].actions.state_delta)

        app_b = self.harness._new_app()
        session_id_b = app_b.session_state["session_id"]
        self.assertNotEqual(session_id_a, session_id_b)
        self.assertFalse(
            self.harness._session(app_b).state[auth_e2e.AUTHENTICATED]
        )
        self.harness._submit(app_b, CREDIT_INTENT)
        self.assertEqual(1, self.credit_tool.call_count)
        self.assertIn(AUTH_REQUIRED, self.harness._assistant_values(app_b))
        self.assertNotIn(f"{LIMIT_A:.2f}", "\n".join(self.harness._assistant_values(app_b)))
        self.assertNotIn(f"{LIMIT_B:.2f}", "\n".join(self.harness._assistant_values(app_b)))

    def test_pre_auth_credit_intent_reveals_no_financial_data(self):
        app = self.harness._new_app()
        state_before = copy.deepcopy(self.harness._session(app).state)
        self.assertFalse(state_before[auth_e2e.AUTHENTICATED])
        self.assertIsNone(state_before[auth_e2e.AUTHENTICATED_CPF])

        self.harness._submit(app, CREDIT_INTENT)

        state_after = self.harness._session(app).state
        self.assertEqual(state_before, state_after)
        self.credit_tool.assert_not_called()
        self.harness.auth_tool.assert_not_called()
        self.harness.read_csv.assert_not_called()
        rendered = "\n".join(self.harness._assistant_values(app))
        self.assertIn(AUTH_REQUIRED, rendered)
        self.assertNotIn(f"{LIMIT_A:.2f}", rendered)
        self.assertNotIn(f"{LIMIT_B:.2f}", rendered)
        self._assert_safe_credit_output(app)
        self.harness.httpx_get.assert_not_called()
        self.harness.provider.assert_not_called()

    def test_authenticated_ui_persists_real_approved_limit_increase(self):
        app_b = self.harness._new_app()
        self.harness._authenticate(
            app_b,
            auth_e2e.OTHER_CPF,
            "02/02/1992",
        )
        session_b_before = copy.deepcopy(self.harness._session(app_b).state)

        clients_before = self._read_csv(self.harness.clients_path)
        by_cpf_before = self._by_key(clients_before, "cpf")
        self.assertEqual([], self._read_csv(self.harness.requests_path))

        app_a = self.harness._new_app()
        session_id_a = app_a.session_state["session_id"]
        self._authenticate_a(app_a)
        self.harness._submit(app_a, INCREASE_INTENT)
        self.assertIn(ASK_NEW_LIMIT, self.harness._assistant_values(app_a))

        self.harness._submit(app_a, APPROVED_LIMIT_INPUT)

        self.assertEqual(1, self.register_tool.call_count)
        self.assertEqual(1, self.process_tool.call_count)
        self.assertEqual(1, self.policy.call_count)
        self.assertEqual(1, self.publish_approval.call_count)
        self.credit_tool.assert_not_called()
        self.assertEqual(
            ["registrar_solicitacao", "processar_solicitacao"],
            [call["name"] for call in self.runner.increase_calls],
        )
        self.assertTrue(
            all(
                call["session_id"] == session_id_a
                for call in self.runner.increase_calls
            )
        )
        self.assertEqual(
            {"novo_limite_solicitado": APPROVED_LIMIT_INPUT},
            self.runner.increase_calls[0]["args"],
        )
        timestamp = self.runner.increase_results[0]["data_hora"]
        self.assertEqual(
            {"data_hora_solicitacao": timestamp},
            self.runner.increase_calls[1]["args"],
        )
        self.assertEqual(
            {"novo_limite_solicitado", "tool_context"},
            set(inspect.signature(REAL_REGISTER_REQUEST).parameters),
        )
        self.assertEqual(
            {"data_hora_solicitacao", "tool_context"},
            set(inspect.signature(REAL_PROCESS_REQUEST).parameters),
        )
        for call in self.runner.increase_calls:
            self.assertTrue(
                {"cpf", "score", "status_pedido", "limite_atual"}.isdisjoint(
                    call["args"]
                )
            )
        self.assertIsInstance(
            self.register_tool.call_args.kwargs["tool_context"],
            ToolContext,
        )
        self.assertIsInstance(
            self.process_tool.call_args.kwargs["tool_context"],
            ToolContext,
        )

        register_result, process_result = self.runner.increase_results
        self.assertTrue(register_result["registrado"])
        self.assertEqual("pendente", register_result["status_pedido"])
        self.assertEqual(APPROVED_LIMIT, register_result["novo_limite_solicitado"])
        self.assertIsNone(register_result["erro"])
        self.assertTrue(process_result["processado"])
        self.assertEqual("aprovado", process_result["status_pedido"])
        self.assertTrue(process_result["limite_atualizado"])
        self.assertEqual(APPROVED_LIMIT, process_result["novo_limite"])
        self.assertFalse(process_result["oferecer_entrevista"])
        self.assertIsNone(process_result["erro"])

        requests = self._read_csv(self.harness.requests_path)
        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual(
            {
                "cpf_cliente",
                "data_hora_solicitacao",
                "limite_atual",
                "novo_limite_solicitado",
                "status_pedido",
            },
            set(request),
        )
        self.assertEqual(auth_e2e.VALID_CPF, request["cpf_cliente"])
        self.assertEqual(LIMIT_A, float(request["limite_atual"]))
        self.assertEqual(
            APPROVED_LIMIT,
            float(request["novo_limite_solicitado"]),
        )
        self.assertEqual("aprovado", request["status_pedido"])
        persisted_instant = datetime.fromisoformat(
            request["data_hora_solicitacao"]
        )
        self.assertEqual(timedelta(0), persisted_instant.utcoffset())
        self.assertEqual(timestamp, request["data_hora_solicitacao"])

        clients_after = self._read_csv(self.harness.clients_path)
        by_cpf_after = self._by_key(clients_after, "cpf")
        self.assertEqual(2, len(clients_after))
        self.assertEqual(
            APPROVED_LIMIT,
            float(by_cpf_after[auth_e2e.VALID_CPF]["limite_credito"]),
        )
        client_a_before = dict(by_cpf_before[auth_e2e.VALID_CPF])
        client_a_after = dict(by_cpf_after[auth_e2e.VALID_CPF])
        client_a_before.pop("limite_credito")
        client_a_after.pop("limite_credito")
        self.assertEqual(client_a_before, client_a_after)
        self.assertEqual(
            by_cpf_before[auth_e2e.OTHER_CPF],
            by_cpf_after[auth_e2e.OTHER_CPF],
        )

        state_a = self.harness._session(app_a).state
        self.assertTrue(state_a[auth_e2e.AUTHENTICATED])
        self.assertEqual(
            auth_e2e.VALID_CPF,
            state_a[auth_e2e.AUTHENTICATED_CPF],
        )
        self.assertFalse(state_a[auth_e2e.CONVERSATION_ENDED])
        self.assertEqual(
            CREDIT_INTERVIEW_NOT_OFFERED,
            state_a[CREDIT_INTERVIEW_STATUS],
        )
        self.assertIsNone(state_a[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(session_b_before, self.harness._session(app_b).state)

        rendered = "\n".join(self.harness._assistant_values(app_a))
        self.assertIn("aprovada", rendered.casefold())
        self.assertIn(f"{APPROVED_LIMIT:.2f}", rendered)
        self._assert_safe_credit_output(app_a)
        self.harness.httpx_get.assert_not_called()
        self.harness.provider.assert_not_called()

        expected_temp_paths = {
            self.harness.clients_path,
            self.harness.score_path,
            self.harness.requests_path,
        }
        read_paths = {
            Path(call.args[0])
            for call in self.harness.read_csv.call_args_list
        }
        self.assertTrue(read_paths.issubset(expected_temp_paths))
        self.assertTrue(read_paths.isdisjoint(self.harness.real_csvs))

        increase_names = {"registrar_solicitacao", "processar_solicitacao"}
        function_calls = [
            call
            for event in self.runner.events
            for call in event.get_function_calls()
            if call.name in increase_names
        ]
        function_responses = [
            event
            for event in self.runner.events
            for response in event.get_function_responses()
            if response.name in increase_names
        ]
        self.assertEqual(
            ["registrar_solicitacao", "processar_solicitacao"],
            [call.name for call in function_calls],
        )
        self.assertEqual(2, len(function_responses))
        self.assertTrue(
            all(isinstance(event.actions, EventActions) for event in function_responses)
        )

    def test_rejected_increase_completes_interview_and_reanalyzes_same_request(self):
        self.harness._write_csv(
            self.harness.score_path,
            ["limite_maximo", "score_minimo"],
            [{"limite_maximo": "5000.00", "score_minimo": "700"}],
        )
        interview_answers = {
            "renda_mensal": "10000",
            "tipo_emprego": "formal",
            "despesas_fixas": "1000",
            "num_dependentes": "0",
            "tem_dividas": "não",
        }
        expected_score = REAL_SCORE_FORMULA(
            10000.0,
            "formal",
            1000.0,
            0,
            "nao",
        )
        self.assertEqual(800, expected_score)
        self.assertTrue(0 <= expected_score <= 1000)

        app_b = self.harness._new_app()
        self.harness._authenticate(
            app_b,
            auth_e2e.OTHER_CPF,
            "02/02/1992",
        )
        session_b_before = copy.deepcopy(self.harness._session(app_b).state)
        clients_before = self._by_key(
            self._read_csv(self.harness.clients_path),
            "cpf",
        )

        app_a = self.harness._new_app()
        self._authenticate_a(app_a)
        state_authenticated = self.harness._session(app_a).state
        self.assertEqual(
            650,
            int(clients_before[auth_e2e.VALID_CPF]["score_credito"]),
        )
        self.assertTrue(state_authenticated[auth_e2e.AUTHENTICATED])

        self.harness._submit(app_a, INCREASE_INTENT)
        self.assertIn(ASK_NEW_LIMIT, self.harness._assistant_values(app_a))
        self.harness._submit(app_a, APPROVED_LIMIT_INPUT)

        rejected_requests = self._read_csv(self.harness.requests_path)
        self.assertEqual(1, len(rejected_requests))
        rejected_request = dict(rejected_requests[0])
        request_timestamp = rejected_request["data_hora_solicitacao"]
        datetime.fromisoformat(request_timestamp)
        self.assertEqual(auth_e2e.VALID_CPF, rejected_request["cpf_cliente"])
        self.assertEqual(LIMIT_A, float(rejected_request["limite_atual"]))
        self.assertEqual(
            APPROVED_LIMIT,
            float(rejected_request["novo_limite_solicitado"]),
        )
        self.assertEqual("rejeitado", rejected_request["status_pedido"])

        clients_after_rejection = self._by_key(
            self._read_csv(self.harness.clients_path),
            "cpf",
        )
        self.assertEqual(
            LIMIT_A,
            float(
                clients_after_rejection[auth_e2e.VALID_CPF]["limite_credito"]
            ),
        )
        self.assertEqual(
            clients_before[auth_e2e.OTHER_CPF],
            clients_after_rejection[auth_e2e.OTHER_CPF],
        )
        state_offered = self.harness._session(app_a).state
        self.assertEqual(
            CREDIT_INTERVIEW_OFFERED,
            state_offered[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual(
            request_timestamp,
            state_offered[CREDIT_INTERVIEW_REQUEST_TIMESTAMP],
        )
        self.assertIn(
            auth_e2e.orchestrator._MSG_OFERTA_ENTREVISTA,
            self.harness._assistant_values(app_a),
        )

        runner_calls_before_interview = len(self.runner.calls)
        self.harness._submit(app_a, "sim")
        state_collecting = self.harness._session(app_a).state
        self.assertEqual(
            CREDIT_INTERVIEW_COLLECTING,
            state_collecting[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual(
            CREDIT_INTERVIEW_FIELDS[0],
            state_collecting[CREDIT_INTERVIEW_CURRENT_FIELD],
        )

        real_questions = auth_e2e.orchestrator._PERGUNTAS_ENTREVISTA
        for index, field in enumerate(CREDIT_INTERVIEW_FIELDS):
            self.assertIn(
                real_questions[field],
                self.harness._assistant_values(app_a),
            )
            self.harness._submit(app_a, interview_answers[field])
            state = self.harness._session(app_a).state
            if index < len(CREDIT_INTERVIEW_FIELDS) - 1:
                next_field = CREDIT_INTERVIEW_FIELDS[index + 1]
                self.assertEqual(
                    CREDIT_INTERVIEW_COLLECTING,
                    state[CREDIT_INTERVIEW_STATUS],
                )
                self.assertEqual(
                    next_field,
                    state[CREDIT_INTERVIEW_CURRENT_FIELD],
                )
                self.assertEqual(
                    index + 1,
                    len(state[CREDIT_INTERVIEW_RESPONSES]),
                )

        self.assertEqual(runner_calls_before_interview, len(self.runner.calls))
        self.assertEqual(5, self.validate_response.call_count)
        self.assertEqual(1, self.process_interview.call_count)
        self.reanalyze.assert_called_once_with(
            auth_e2e.VALID_CPF,
            request_timestamp,
        )
        self.assertEqual(2, self.policy.call_count)
        self.assertEqual(
            (650, APPROVED_LIMIT),
            self.policy.call_args_list[0].args[:2],
        )
        self.assertEqual(
            (expected_score, APPROVED_LIMIT),
            self.policy.call_args_list[1].args[:2],
        )
        self.assertEqual(1, self.publish_approval.call_count)

        final_requests = self._read_csv(self.harness.requests_path)
        self.assertEqual(1, len(final_requests))
        final_request = final_requests[0]
        self.assertEqual(
            request_timestamp,
            final_request["data_hora_solicitacao"],
        )
        self.assertEqual("aprovado", final_request["status_pedido"])
        self.assertEqual(
            rejected_request["cpf_cliente"],
            final_request["cpf_cliente"],
        )
        self.assertEqual(
            rejected_request["novo_limite_solicitado"],
            final_request["novo_limite_solicitado"],
        )

        clients_final = self._by_key(
            self._read_csv(self.harness.clients_path),
            "cpf",
        )
        self.assertEqual(
            expected_score,
            int(clients_final[auth_e2e.VALID_CPF]["score_credito"]),
        )
        self.assertEqual(
            APPROVED_LIMIT,
            float(clients_final[auth_e2e.VALID_CPF]["limite_credito"]),
        )
        self.assertEqual(
            clients_before[auth_e2e.OTHER_CPF],
            clients_final[auth_e2e.OTHER_CPF],
        )
        self.assertEqual(session_b_before, self.harness._session(app_b).state)

        state_final = self.harness._session(app_a).state
        self.assertEqual(
            CREDIT_INTERVIEW_COMPLETED,
            state_final[CREDIT_INTERVIEW_STATUS],
        )
        self.assertFalse(state_final[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIsNone(state_final[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertTrue(state_final[auth_e2e.AUTHENTICATED])
        self.assertEqual(
            auth_e2e.VALID_CPF,
            state_final[auth_e2e.AUTHENTICATED_CPF],
        )
        self.assertFalse(state_final[auth_e2e.CONVERSATION_ENDED])

        rendered = "\n".join(self.harness._assistant_values(app_a))
        self.assertIn(
            auth_e2e.orchestrator._MSG_ENTREVISTA_CONCLUIDA,
            rendered,
        )
        self.assertIn(
            auth_e2e.orchestrator._MSG_REANALISE_APROVADA,
            rendered,
        )
        self._assert_safe_credit_output(app_a)
        self.harness.httpx_get.assert_not_called()
        self.harness.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
