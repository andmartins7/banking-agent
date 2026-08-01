import os
import socket
import sys
import unittest
import urllib.request
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
INITIAL_MESSAGE = "iniciar atendimento"
GENERIC_ERROR = (
    "Desculpe, ocorreu um problema técnico. "
    "Por favor, tente novamente em instantes."
)
_MISSING = object()


@contextmanager
def _without_environment_keys(*keys):
    previous = {key: os.environ.get(key, _MISSING) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class FakeOrchestrator:
    """Fronteira mínima e observável usada pelo app Streamlit real."""

    def __init__(self):
        self.created_session_ids = []
        self.process_calls = []
        self.ended_checks = []
        self.ended_by_session = {}
        self.end_after_messages = set()
        self.errors_by_message = {}

        self.module = ModuleType("orchestrator")
        self.module.__file__ = "<fake-orchestrator>"
        self.module.criar_sessao = self.criar_sessao
        self.module.processar_mensagem = self.processar_mensagem
        self.module.sessao_encerrada = self.sessao_encerrada

    @property
    def call_counts(self):
        return {
            "criar_sessao": len(self.created_session_ids),
            "processar_mensagem": len(self.process_calls),
            "sessao_encerrada": len(self.ended_checks),
        }

    def criar_sessao(self, session_id):
        self.created_session_ids.append(session_id)
        self.ended_by_session.setdefault(session_id, False)

    def processar_mensagem(self, session_id, mensagem):
        self.process_calls.append((session_id, mensagem))
        if mensagem in self.errors_by_message:
            raise self.errors_by_message[mensagem]
        if mensagem in self.end_after_messages:
            self.ended_by_session[session_id] = True
        return f"ECHO:{mensagem}"

    def sessao_encerrada(self, session_id):
        self.ended_checks.append(session_id)
        return self.ended_by_session.get(session_id, False)


class StreamlitUiTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOrchestrator()
        self._previous_orchestrator = sys.modules.get("orchestrator", _MISSING)
        self._stack = ExitStack()
        self.addCleanup(self._restore_global_patches)

        self._stack.enter_context(
            patch.dict(sys.modules, {"orchestrator": self.fake.module})
        )
        self._stack.enter_context(
            _without_environment_keys("GOOGLE_API_KEY", "GEMINI_API_KEY")
        )
        self._stack.enter_context(
            patch(
                "socket.socket.connect",
                side_effect=AssertionError("A UI tentou acessar a rede."),
            )
        )
        self._stack.enter_context(
            patch(
                "socket.create_connection",
                side_effect=AssertionError("A UI tentou acessar a rede."),
            )
        )
        self._stack.enter_context(
            patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("A UI tentou acessar a rede."),
            )
        )

    def _restore_global_patches(self):
        self._stack.close()
        if self._previous_orchestrator is _MISSING:
            self.assertNotIn("orchestrator", sys.modules)
        else:
            self.assertIs(
                self._previous_orchestrator,
                sys.modules.get("orchestrator"),
            )

    def _new_app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        self.assertEqual([], list(app.exception))
        return app

    @staticmethod
    def _markdown_values(app):
        return [element.value for element in app.markdown]

    @staticmethod
    def _chat_history(app):
        return [
            (message.name, [item.value for item in message.markdown])
            for message in app.chat_message
        ]

    @staticmethod
    def _submit(app, message):
        app.chat_input[0].set_value(message).run()
        return app

    def test_boot_carrega_app_real_com_identidade_input_e_fake(self):
        app = self._new_app()
        session_id = app.session_state["session_id"]

        self.assertEqual(["🏦 Banco Ágil"], [item.value for item in app.title])
        self.assertEqual(
            ["Atendimento Digital — Seguro, rápido e inteligente."],
            [item.value for item in app.caption],
        )
        self.assertEqual(1, len(app.chat_input))
        self.assertEqual("Digite sua mensagem...", app.chat_input[0].placeholder)
        self.assertEqual(session_id, str(uuid.UUID(session_id)))
        self.assertEqual([session_id], self.fake.created_session_ids)
        self.assertEqual([(session_id, INITIAL_MESSAGE)], self.fake.process_calls)
        self.assertIs(self.fake.module, sys.modules["orchestrator"])
        self.assertEqual("<fake-orchestrator>", sys.modules["orchestrator"].__file__)
        self.assertNotIn("GOOGLE_API_KEY", os.environ)
        self.assertNotIn("GEMINI_API_KEY", os.environ)

    def test_session_id_e_criado_uma_vez_e_preservado_entre_mensagens(self):
        app = self._new_app()
        session_id = app.session_state["session_id"]

        self._submit(app, "primeira mensagem")
        self.assertEqual(session_id, app.session_state["session_id"])
        self._submit(app, "segunda mensagem")

        self.assertEqual(session_id, app.session_state["session_id"])
        self.assertEqual([session_id], self.fake.created_session_ids)
        self.assertEqual(
            [
                (session_id, INITIAL_MESSAGE),
                (session_id, "primeira mensagem"),
                (session_id, "segunda mensagem"),
            ],
            self.fake.process_calls,
        )

    def test_duas_instancias_tem_sessoes_e_historicos_independentes(self):
        first = self._new_app()
        second = self._new_app()
        first_id = first.session_state["session_id"]
        second_id = second.session_state["session_id"]

        self.assertNotEqual(first_id, second_id)
        self.assertEqual([first_id, second_id], self.fake.created_session_ids)

        self._submit(first, "somente na primeira")

        self.assertIn("somente na primeira", self._markdown_values(first))
        self.assertNotIn("somente na primeira", self._markdown_values(second))
        self.assertNotIn(
            "ECHO:somente na primeira",
            self._markdown_values(second),
        )
        self.assertEqual(
            [{"role": "assistant", "content": f"ECHO:{INITIAL_MESSAGE}"}],
            second.session_state["messages"],
        )

    def test_historico_preserva_ordem_e_acrescenta_segunda_interacao(self):
        app = self._new_app()

        self._submit(app, "mensagem um")
        first_history = list(app.session_state["messages"])
        self._submit(app, "mensagem dois")

        self.assertEqual(
            [
                {"role": "assistant", "content": f"ECHO:{INITIAL_MESSAGE}"},
                {"role": "user", "content": "mensagem um"},
                {"role": "assistant", "content": "ECHO:mensagem um"},
                {"role": "user", "content": "mensagem dois"},
                {"role": "assistant", "content": "ECHO:mensagem dois"},
            ],
            app.session_state["messages"],
        )
        self.assertEqual(
            first_history,
            app.session_state["messages"][: len(first_history)],
        )
        self.assertEqual(
            [
                ("assistant", [f"ECHO:{INITIAL_MESSAGE}"]),
                ("user", ["mensagem um"]),
                ("assistant", ["ECHO:mensagem um"]),
                ("user", ["mensagem dois"]),
                ("assistant", ["ECHO:mensagem dois"]),
            ],
            self._chat_history(app),
        )

    def test_submissao_repassa_texto_exato_em_uma_unica_chamada(self):
        app = self._new_app()
        session_id = app.session_state["session_id"]
        message = "Mensagem Exata: crédito 123!?"
        calls_before = self.fake.call_counts["processar_mensagem"]

        self._submit(app, message)

        self.assertEqual(
            calls_before + 1,
            self.fake.call_counts["processar_mensagem"],
        )
        self.assertEqual((session_id, message), self.fake.process_calls[-1])
        self.assertIn(message, self._markdown_values(app))
        self.assertIn(f"ECHO:{message}", self._markdown_values(app))

    def test_encerramento_preserva_historico_e_bloqueia_nova_interacao(self):
        ending_message = "encerrar agora"
        blocked_message = "não deve ser processada"
        self.fake.end_after_messages.add(ending_message)
        app = self._new_app()
        session_id = app.session_state["session_id"]
        chat_input = app.chat_input[0]

        chat_input.set_value(ending_message).run()

        self.assertTrue(app.session_state["ended"])
        self.assertEqual([session_id], self.fake.ended_checks)
        self.assertTrue(self.fake.ended_by_session[session_id])
        self.assertTrue(
            any("Atendimento encerrado" in item.value for item in app.info)
        )
        self.assertIn(ending_message, self._markdown_values(app))
        self.assertIn(f"ECHO:{ending_message}", self._markdown_values(app))

        process_calls_before = list(self.fake.process_calls)
        # AppTest 1.40 retém o proxy do último evento após st.rerun; quando
        # st.stop encerra a execução antes do widget, esse proxy deve ser inerte.
        chat_input.set_value(blocked_message).run()
        app.run()

        self.assertEqual(process_calls_before, self.fake.process_calls)
        self.assertNotIn(blocked_message, self._markdown_values(app))
        self.assertTrue(
            any("Atendimento encerrado" in item.value for item in app.info)
        )

    def test_messages_e_apenas_historico_visual(self):
        app = self._new_app()
        session_id = app.session_state["session_id"]
        process_calls_before = list(self.fake.process_calls)
        visual_history = [
            {"role": "user", "content": "histórico visual isolado"},
            {"role": "assistant", "content": "resposta visual isolada"},
        ]

        app.session_state["messages"] = visual_history
        app.run()

        self.assertEqual(visual_history, app.session_state["messages"])
        self.assertEqual(process_calls_before, self.fake.process_calls)
        self.assertFalse(app.session_state["ended"])
        self.assertFalse(self.fake.ended_by_session[session_id])
        self.assertIn("histórico visual isolado", self._markdown_values(app))
        self.assertIn("resposta visual isolada", self._markdown_values(app))

    def test_erro_tecnico_exibe_resposta_controlada_sem_stack_trace(self):
        technical_message = "provocar erro técnico"
        private_detail = "internal-stack-marker-42"
        self.fake.errors_by_message[technical_message] = RuntimeError(private_detail)
        app = self._new_app()
        calls_before = self.fake.call_counts["processar_mensagem"]

        with patch("builtins.print") as print_mock:
            self._submit(app, technical_message)

        rendered = "\n".join(self._markdown_values(app))
        self.assertEqual([], list(app.exception))
        self.assertEqual(
            calls_before + 1,
            self.fake.call_counts["processar_mensagem"],
        )
        self.assertIn(GENERIC_ERROR, self._markdown_values(app))
        self.assertNotIn(private_detail, rendered)
        self.assertNotIn("Traceback", rendered)
        print_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
