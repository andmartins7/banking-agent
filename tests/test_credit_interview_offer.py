import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
import orchestrator
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_COLLECTING,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_DECLINED,
    CREDIT_INTERVIEW_NOT_OFFERED,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_STATUS,
    criar_estado_inicial,
    oferecer_entrevista_credito,
    recusar_entrevista_credito,
)
import tools.credito_tools as credito_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP_A = "2026-07-03T10:20:30.123456+00:00"
TIMESTAMP_B = "2026-07-03T10:21:30.123456+00:00"


class FakeEvent:
    def __init__(self, text):
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])

    def is_final_response(self):
        return True


class FakeRunner:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.callback is not None:
            self.callback()
        yield FakeEvent("texto livre produzido pelo modelo")


class FakeToolContext:
    def __init__(self, state=None):
        self.state = state if state is not None else criar_estado_inicial()
        self.state[AUTHENTICATED] = True
        self.state[AUTHENTICATED_CPF] = CPF_A


class CreditInterviewOfferTests(unittest.TestCase):
    def setUp(self):
        self.service = InMemorySessionService()
        self.runner = FakeRunner()
        service_patch = patch.object(
            orchestrator,
            "_session_service",
            self.service,
        )
        runner_patch = patch.object(orchestrator, "_runner", self.runner)
        service_patch.start()
        runner_patch.start()
        self.addCleanup(service_patch.stop)
        self.addCleanup(runner_patch.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.clientes_path = base / "clientes.csv"
        self.score_path = base / "score_limite.csv"
        self.solicitacoes_path = base / "solicitacoes.csv"
        self._write_clientes()
        self._write_score()
        self._write_solicitacoes([self._solicitacao()])

        csv_patches = [
            patch("tools.credito_tools.CSV_CLIENTES", self.clientes_path),
            patch("tools.credito_tools.CSV_SCORE_LIMITE", self.score_path),
            patch(
                "tools.credito_tools.CSV_SOLICITACOES",
                self.solicitacoes_path,
            ),
        ]
        for item in csv_patches:
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def _write_csv(path, fieldnames, rows):
        with Path(path).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_csv(path):
        with Path(path).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _write_clientes(self, score_a="500"):
        self._write_csv(
            self.clientes_path,
            ["cpf", "score_credito", "limite_credito"],
            [
                {
                    "cpf": CPF_A,
                    "score_credito": str(score_a),
                    "limite_credito": "1000.00",
                },
                {
                    "cpf": CPF_B,
                    "score_credito": "500",
                    "limite_credito": "2000.00",
                },
            ],
        )

    def _write_score(self):
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [{"limite_maximo": "5000.00", "score_minimo": "700"}],
        )

    def _solicitacao(self, timestamp=TIMESTAMP_A):
        return {
            "cpf_cliente": CPF_A,
            "data_hora_solicitacao": timestamp,
            "limite_atual": "1000.00",
            "novo_limite_solicitado": "3000.00",
            "status_pedido": "pendente",
        }

    def _write_solicitacoes(self, rows):
        self._write_csv(
            self.solicitacoes_path,
            [
                "cpf_cliente",
                "data_hora_solicitacao",
                "limite_atual",
                "novo_limite_solicitado",
                "status_pedido",
            ],
            rows,
        )

    def _criar_sessao(self, session_id, state):
        orchestrator._run_async(
            self.service.create_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
                state=state,
            )
        )

    def _sessao(self, session_id):
        return orchestrator._run_async(
            self.service.get_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )

    def _estado(self, session_id):
        return self._sessao(session_id).state

    @staticmethod
    def _estado_oferecido(cpf=CPF_A, timestamp=TIMESTAMP_A):
        state = criar_estado_inicial()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = timestamp
        return oferecer_entrevista_credito(state)

    def _hashes_temporarios(self):
        return {
            self.clientes_path: self._hash(self.clientes_path),
            self.score_path: self._hash(self.score_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }

    def test_01_rejeicao_persistida_associa_pedido_e_oferece_entrevista(self):
        context = FakeToolContext()

        resultado = credito_tools.processar_solicitacao(TIMESTAMP_A, context)

        self.assertTrue(resultado["processado"])
        self.assertEqual("rejeitado", resultado["status_pedido"])
        self.assertTrue(resultado["oferecer_entrevista"])
        self.assertEqual(
            CREDIT_INTERVIEW_OFFERED,
            context.state[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual(
            TIMESTAMP_A,
            context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP],
        )
        self.assertEqual(
            "rejeitado",
            self._read_csv(self.solicitacoes_path)[0]["status_pedido"],
        )

    def test_02_aprovacao_nao_produz_offered(self):
        self._write_clientes(score_a="800")
        context = FakeToolContext()

        resultado = credito_tools.processar_solicitacao(TIMESTAMP_A, context)

        self.assertEqual("aprovado", resultado["status_pedido"])
        self.assertEqual(
            CREDIT_INTERVIEW_NOT_OFFERED,
            context.state[CREDIT_INTERVIEW_STATUS],
        )
        self.assertFalse(resultado["oferecer_entrevista"])

    def test_03_falha_de_persistencia_nao_produz_offered(self):
        context = FakeToolContext()
        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=OSError("falha injetada"),
        ):
            resultado = credito_tools.processar_solicitacao(
                TIMESTAMP_A,
                context,
            )

        self.assertFalse(resultado["processado"])
        self.assertEqual(
            CREDIT_INTERVIEW_NOT_OFFERED,
            context.state[CREDIT_INTERVIEW_STATUS],
        )
        self.assertIsNone(context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])

    def test_04_estado_declined_nao_recebe_nova_oferta(self):
        state = self._estado_oferecido()
        state = recusar_entrevista_credito(state)
        context = FakeToolContext(state)

        resultado = credito_tools.processar_solicitacao(TIMESTAMP_A, context)

        self.assertEqual(CREDIT_INTERVIEW_DECLINED, context.state[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(resultado["oferecer_entrevista"])

    def test_05_oferta_pos_runner_substitui_texto_do_modelo(self):
        state = criar_estado_inicial()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = CPF_A
        self._criar_sessao("oferta-pos-runner", state)

        def persistir_oferta():
            session = self._sessao("oferta-pos-runner")
            event = Event(
                author="tool_credito",
                actions=EventActions(state_delta={
                    CREDIT_INTERVIEW_STATUS: CREDIT_INTERVIEW_OFFERED,
                    CREDIT_INTERVIEW_REQUEST_TIMESTAMP: TIMESTAMP_A,
                }),
            )
            orchestrator._run_async(
                self.service.append_event(session=session, event=event)
            )

        runner = FakeRunner(callback=persistir_oferta)
        with patch.object(orchestrator, "_runner", runner):
            resposta = orchestrator.processar_mensagem(
                "oferta-pos-runner",
                "quero aumentar meu limite",
            )

        self.assertEqual(orchestrator._MSG_OFERTA_ENTREVISTA, resposta)
        self.assertNotIn("texto livre", resposta)
        self.assertEqual(1, len(runner.calls))

    def test_06_classificador_produz_somente_tres_resultados(self):
        casos = {
            "sim": "accepted",
            "quero": "accepted",
            "aceito": "accepted",
            "pode continuar": "accepted",
            "vamos": "accepted",
            "não": "declined",
            "nao quero": "declined",
            "prefiro não": "declined",
            "agora não": "declined",
            "sim, mas não quero": "ambiguous",
            "talvez": "ambiguous",
        }
        for resposta, esperado in casos.items():
            with self.subTest(resposta=resposta):
                self.assertEqual(
                    esperado,
                    orchestrator._classificar_resposta_oferta(resposta),
                )

    def test_07_aceite_normalizado_inicia_primeiro_campo_sem_runner(self):
        self._criar_sessao("aceite", self._estado_oferecido())
        antes = self._hashes_temporarios()

        resposta = orchestrator.processar_mensagem(
            "aceite",
            "   PODE CONTINUAR!!!   ",
        )

        state = self._estado("aceite")
        self.assertEqual(CREDIT_INTERVIEW_COLLECTING, state[CREDIT_INTERVIEW_STATUS])
        self.assertEqual("renda_mensal", state[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual({}, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(orchestrator._PERGUNTAS_ENTREVISTA["renda_mensal"], resposta)
        self.assertEqual([], self.runner.calls)
        self.assertEqual(antes, self._hashes_temporarios())

    def test_08_sim_aceita_sem_runner(self):
        self._criar_sessao("sim", self._estado_oferecido())

        orchestrator.processar_mensagem("sim", "sim")

        self.assertEqual(
            CREDIT_INTERVIEW_COLLECTING,
            self._estado("sim")[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual([], self.runner.calls)

    def test_09_nao_com_e_sem_acento_recusam(self):
        for indice, resposta in enumerate(("não", "nao")):
            session_id = f"recusa-{indice}"
            self._criar_sessao(session_id, self._estado_oferecido())

            retorno = orchestrator.processar_mensagem(session_id, resposta)

            state = self._estado(session_id)
            self.assertEqual(CREDIT_INTERVIEW_DECLINED, state[CREDIT_INTERVIEW_STATUS])
            self.assertIsNone(state[CREDIT_INTERVIEW_CURRENT_FIELD])
            self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
            self.assertIn("encerrar", retorno)
        self.assertEqual([], self.runner.calls)

    def test_10_recusa_natural_nao_vira_aceite_por_substring(self):
        self._criar_sessao("nao-quero", self._estado_oferecido())

        resposta = orchestrator.processar_mensagem(
            "nao-quero",
            "  NÃO QUERO. ",
        )

        state = self._estado("nao-quero")
        self.assertEqual("declined", orchestrator._classificar_resposta_oferta("não quero"))
        self.assertEqual(CREDIT_INTERVIEW_DECLINED, state[CREDIT_INTERVIEW_STATUS])
        self.assertNotIn("deseja continuar", resposta.lower())
        self.assertEqual([], self.runner.calls)

    def test_11_ambiguidade_preserva_estado_timestamp_e_zero_runner(self):
        self._criar_sessao("ambigua", self._estado_oferecido())
        antes = self._hashes_temporarios()

        resposta = orchestrator.processar_mensagem(
            "ambigua",
            "sim, mas não quero",
        )

        state = self._estado("ambigua")
        self.assertEqual(CREDIT_INTERVIEW_OFFERED, state[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual({}, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertIn("sim ou não", resposta)
        self.assertEqual([], self.runner.calls)
        self.assertEqual(antes, self._hashes_temporarios())

    def test_12_conversation_ended_impede_transicao_e_runner(self):
        state = self._estado_oferecido()
        state[CONVERSATION_ENDED] = True
        self._criar_sessao("encerrada", state)

        resposta = orchestrator.processar_mensagem("encerrada", "sim")

        self.assertEqual(state, self._estado("encerrada"))
        self.assertEqual([], self.runner.calls)
        self.assertIn("encerrado", resposta)

    def test_13_duas_sessoes_nao_compartilham_decisao(self):
        self._criar_sessao("sessao-a", self._estado_oferecido())
        self._criar_sessao(
            "sessao-b",
            self._estado_oferecido(CPF_B, TIMESTAMP_B),
        )

        orchestrator.processar_mensagem("sessao-a", "sim")
        orchestrator.processar_mensagem("sessao-b", "não")

        state_a = self._estado("sessao-a")
        state_b = self._estado("sessao-b")
        self.assertEqual(CREDIT_INTERVIEW_COLLECTING, state_a[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(CREDIT_INTERVIEW_DECLINED, state_b[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(TIMESTAMP_A, state_a[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(TIMESTAMP_B, state_b[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual([], self.runner.calls)

    def test_14_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        self._criar_sessao("csvs", self._estado_oferecido())

        orchestrator.processar_mensagem("csvs", "sim")

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_15_sessao_encerrada_nao_e_ofertada_pela_tool(self):
        state = criar_estado_inicial()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = CPF_A
        state[CONVERSATION_ENDED] = True
        context = FakeToolContext(state)
        antes = self._hashes_temporarios()

        resultado = credito_tools.processar_solicitacao(TIMESTAMP_A, context)

        self.assertFalse(resultado["processado"])
        self.assertEqual(
            CREDIT_INTERVIEW_NOT_OFFERED,
            context.state[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual(antes, self._hashes_temporarios())

    def test_16_pedido_invalido_nao_produz_offered(self):
        context = FakeToolContext()
        antes = self._hashes_temporarios()

        resultado = credito_tools.processar_solicitacao(TIMESTAMP_B, context)

        self.assertFalse(resultado["processado"])
        self.assertEqual(
            CREDIT_INTERVIEW_NOT_OFFERED,
            context.state[CREDIT_INTERVIEW_STATUS],
        )
        self.assertIsNone(context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(antes, self._hashes_temporarios())


if __name__ == "__main__":
    unittest.main()
