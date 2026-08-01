import copy
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
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_DECLINED,
    CREDIT_INTERVIEW_INTERRUPTED,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    criar_estado_inicial,
    oferecer_entrevista_credito,
    registrar_resposta_entrevista,
)
import tools.credito_tools as credito_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP_A = "2026-07-05T10:20:30.123456+00:00"
TIMESTAMP_B = "2026-07-05T10:21:30.123456+00:00"


class FakeEvent:
    def __init__(self, text):
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])

    def is_final_response(self):
        return True


class InitialCreditRunner:
    """Simula somente o turno LLM que invoca as tools iniciais de crédito."""

    def __init__(self, service, solicitacoes):
        self.service = service
        self.solicitacoes = solicitacoes
        self.calls = []
        self.registros = {}
        self.processamentos = {}

    def run(self, **kwargs):
        self.calls.append(kwargs)
        session_id = kwargs["session_id"]
        configuracao = self.solicitacoes[session_id]
        session = orchestrator._run_async(
            self.service.get_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )
        estado_antes = copy.deepcopy(session.state)
        contexto = SimpleNamespace(state=copy.deepcopy(session.state))

        with patch(
            "tools.credito_tools._gerar_timestamp_utc",
            return_value=configuracao["timestamp"],
        ):
            registro = credito_tools.registrar_solicitacao(
                configuracao["novo_limite"],
                contexto,
            )
        processamento = credito_tools.processar_solicitacao(
            registro["data_hora"],
            contexto,
        )
        self.registros[session_id] = registro
        self.processamentos[session_id] = processamento

        state_delta = {
            chave: valor
            for chave, valor in contexto.state.items()
            if estado_antes.get(chave) != valor
        }
        if state_delta:
            event = Event(
                author="runner_fake_credito",
                actions=EventActions(state_delta=state_delta),
            )
            orchestrator._run_async(
                self.service.append_event(session=session, event=event)
            )

        yield FakeEvent(
            "Vou transferir para o agente de entrevista para decidir seu pedido."
        )


class CreditInterviewE2ETests(unittest.TestCase):
    def setUp(self):
        self.service = InMemorySessionService()
        service_patch = patch.object(
            orchestrator,
            "_session_service",
            self.service,
        )
        service_patch.start()
        self.addCleanup(service_patch.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.clientes_path = base / "clientes.csv"
        self.score_path = base / "score_limite.csv"
        self.solicitacoes_path = base / "solicitacoes.csv"
        self._write_clientes()
        self._write_score()
        self._write_solicitacoes([])

        csv_patches = [
            patch("tools.score_tools.CSV_CLIENTES", self.clientes_path),
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

    def _write_clientes(self):
        self._write_csv(
            self.clientes_path,
            [
                "cpf",
                "nome",
                "data_nascimento",
                "score_credito",
                "limite_credito",
            ],
            [
                {
                    "cpf": CPF_A,
                    "nome": "Cliente A",
                    "data_nascimento": "01/01/1990",
                    "score_credito": "500",
                    "limite_credito": "1000.00",
                },
                {
                    "cpf": CPF_B,
                    "nome": "Cliente B",
                    "data_nascimento": "02/02/1992",
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

    def _criar_sessao(self, session_id, cpf=CPF_A, state=None):
        if state is None:
            state = criar_estado_inicial()
            state[AUTHENTICATED] = True
            state[AUTHENTICATED_CPF] = cpf
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

    def _cliente(self, cpf):
        return next(
            linha
            for linha in self._read_csv(self.clientes_path)
            if linha["cpf"] == cpf
        )

    @staticmethod
    def _solicitacao_fixture(
        cpf=CPF_A,
        timestamp=TIMESTAMP_A,
        limite_atual="1000.00",
        novo_limite="3000.00",
        status="rejeitado",
    ):
        return {
            "cpf_cliente": cpf,
            "data_hora_solicitacao": timestamp,
            "limite_atual": limite_atual,
            "novo_limite_solicitado": novo_limite,
            "status_pedido": status,
        }

    @staticmethod
    def _estado_completed_pending(cpf=CPF_A, timestamp=TIMESTAMP_A):
        state = criar_estado_inicial()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = timestamp
        state = aceitar_entrevista_credito(oferecer_entrevista_credito(state))
        for resposta in [10000.0, "formal", 1000.0, 0, "nao"]:
            state = registrar_resposta_entrevista(state, resposta)
        return concluir_processamento_entrevista(state)

    @staticmethod
    def _assert_handoffs_invisiveis(mensagens):
        proibidos = [
            "agente de triagem",
            "agente de crédito",
            "agente de entrevista",
            "transferir",
            "transferência",
            "redirecionar",
            "retornar ao agente",
            "mudança de atendente",
        ]
        texto = "\n".join(mensagens).casefold()
        for proibido in proibidos:
            if proibido in texto:
                raise AssertionError(
                    f"Mensagem externa revelou transição interna: {proibido}"
                )

    def _runner(self, configuracoes):
        runner = InitialCreditRunner(self.service, configuracoes)
        patcher = patch.object(orchestrator, "_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return runner

    def test_01_fluxo_principal_reanalisa_e_aprova_o_mesmo_pedido(self):
        session_id = "e2e-principal"
        self._criar_sessao(session_id)
        runner = self._runner({
            session_id: {
                "novo_limite": 3000.0,
                "timestamp": TIMESTAMP_A,
            },
        })
        politica_original = credito_tools._avaliar_politica_credito
        reanalisar_original = orchestrator.reanalisar_solicitacao_autorizada
        mensagens = []

        with patch(
            "tools.credito_tools._avaliar_politica_credito",
            wraps=politica_original,
        ) as politica, patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
            wraps=reanalisar_original,
        ) as reanalisar:
            mensagens.append(orchestrator.processar_mensagem(
                session_id,
                "Solicito aumento para R$ 3.000.",
            ))
            self.assertEqual(1, len(runner.calls))
            self.assertEqual("rejeitado", runner.processamentos[
                session_id
            ]["status_pedido"])

            mensagens.append(orchestrator.processar_mensagem(session_id, "sim"))
            self.assertEqual(CREDIT_INTERVIEW_COLLECTING, self._estado(
                session_id
            )[CREDIT_INTERVIEW_STATUS])

            for resposta in ["10000", "formal", "1000", "0", "não"]:
                mensagens.append(orchestrator.processar_mensagem(
                    session_id,
                    resposta,
                ))
                self.assertEqual(1, len(runner.calls))

        state = self._estado(session_id)
        solicitacoes = self._read_csv(self.solicitacoes_path)
        cliente = self._cliente(CPF_A)
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIsNone(state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(CPF_A, state[AUTHENTICATED_CPF])
        self.assertEqual(1, len(solicitacoes))
        self.assertEqual(CPF_A, solicitacoes[0]["cpf_cliente"])
        self.assertEqual(TIMESTAMP_A, solicitacoes[0]["data_hora_solicitacao"])
        self.assertEqual("3000.0", solicitacoes[0]["novo_limite_solicitado"])
        self.assertEqual("aprovado", solicitacoes[0]["status_pedido"])
        self.assertEqual(800, int(cliente["score_credito"]))
        self.assertEqual(3000.0, float(cliente["limite_credito"]))
        reanalisar.assert_called_once_with(CPF_A, TIMESTAMP_A)
        self.assertEqual(2, politica.call_count)
        self.assertEqual((500, 3000.0), politica.call_args_list[0].args[:2])
        self.assertEqual((800, 3000.0), politica.call_args_list[1].args[:2])
        self.assertEqual(orchestrator._MSG_OFERTA_ENTREVISTA, mensagens[0])
        self._assert_handoffs_invisiveis(mensagens)

    def test_02_recusa_nao_coleta_nem_reanalisa(self):
        session_id = "e2e-recusa"
        self._criar_sessao(session_id)
        runner = self._runner({
            session_id: {
                "novo_limite": 3000.0,
                "timestamp": TIMESTAMP_A,
            },
        })
        mensagens = [orchestrator.processar_mensagem(
            session_id,
            "Quero aumentar meu limite.",
        )]

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            mensagens.append(orchestrator.processar_mensagem(session_id, "não"))

        state = self._estado(session_id)
        solicitacoes = self._read_csv(self.solicitacoes_path)
        reanalisar.assert_not_called()
        self.assertEqual(CREDIT_INTERVIEW_DECLINED, state[CREDIT_INTERVIEW_STATUS])
        self.assertIsNone(state[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual({}, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(1, len(solicitacoes))
        self.assertEqual("rejeitado", solicitacoes[0]["status_pedido"])
        self.assertIsNone(orchestrator._obter_oferta_pendente(session_id))
        self.assertEqual(1, len(runner.calls))
        self._assert_handoffs_invisiveis(mensagens)

    def test_03_duas_invalidas_interrompem_sem_score_ou_novo_pedido(self):
        session_id = "e2e-fallback"
        self._criar_sessao(session_id)
        runner = self._runner({
            session_id: {
                "novo_limite": 3000.0,
                "timestamp": TIMESTAMP_A,
            },
        })
        mensagens = [orchestrator.processar_mensagem(
            session_id,
            "Quero aumentar meu limite.",
        )]
        mensagens.append(orchestrator.processar_mensagem(session_id, "sim"))
        score_antes = self._cliente(CPF_A)["score_credito"]

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar, patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            mensagens.append(orchestrator.processar_mensagem(
                session_id,
                "renda desconhecida",
            ))
            mensagens.append(orchestrator.processar_mensagem(
                session_id,
                "continua inválida",
            ))

        state = self._estado(session_id)
        solicitacoes = self._read_csv(self.solicitacoes_path)
        processar.assert_not_called()
        reanalisar.assert_not_called()
        self.assertEqual(CREDIT_INTERVIEW_INTERRUPTED, state[
            CREDIT_INTERVIEW_STATUS
        ])
        self.assertEqual(score_antes, self._cliente(CPF_A)["score_credito"])
        self.assertEqual(1, len(solicitacoes))
        self.assertEqual("rejeitado", solicitacoes[0]["status_pedido"])
        self.assertEqual(1, len(runner.calls))
        self._assert_handoffs_invisiveis(mensagens)

    def test_04_encerramento_durante_coleta_interrompe_todo_processamento(self):
        session_id = "e2e-encerra-coleta"
        self._criar_sessao(session_id)
        runner = self._runner({
            session_id: {
                "novo_limite": 3000.0,
                "timestamp": TIMESTAMP_A,
            },
        })
        orchestrator.processar_mensagem(session_id, "Aumentar limite.")
        orchestrator.processar_mensagem(session_id, "sim")
        orchestrator.processar_mensagem(session_id, "10000")
        respostas_antes = dict(self._estado(session_id)[
            CREDIT_INTERVIEW_RESPONSES
        ])
        score_antes = self._cliente(CPF_A)["score_credito"]

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar, patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            mensagem = orchestrator.processar_mensagem(
                session_id,
                "quero encerrar",
            )

        state = self._estado(session_id)
        processar.assert_not_called()
        reanalisar.assert_not_called()
        self.assertTrue(state[CONVERSATION_ENDED])
        self.assertEqual(respostas_antes, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(score_antes, self._cliente(CPF_A)["score_credito"])
        self.assertEqual("rejeitado", self._read_csv(
            self.solicitacoes_path
        )[0]["status_pedido"])
        self.assertEqual(1, len(runner.calls))
        self._assert_handoffs_invisiveis([mensagem])

    def test_05_encerramento_completed_pending_preserva_reanalise_pendente(self):
        self._write_solicitacoes([
            self._solicitacao_fixture(),
        ])
        state = self._estado_completed_pending()
        session_id = "e2e-encerra-pending"
        self._criar_sessao(session_id, state=state)
        antes = {
            self.clientes_path: self._hash(self.clientes_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }
        runner = self._runner({})

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            mensagem = orchestrator.processar_mensagem(
                session_id,
                "quero encerrar",
            )

        persistido = self._estado(session_id)
        depois = {path: self._hash(path) for path in antes}
        reanalisar.assert_not_called()
        self.assertTrue(persistido[CONVERSATION_ENDED])
        self.assertTrue(persistido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP_A, persistido[
            CREDIT_INTERVIEW_REQUEST_TIMESTAMP
        ])
        self.assertEqual(antes, depois)
        self.assertEqual([], runner.calls)
        self._assert_handoffs_invisiveis([mensagem])

    def test_06_duas_sessoes_nao_cruzam_estado_score_ou_pedido(self):
        self._criar_sessao("e2e-a", CPF_A)
        self._criar_sessao("e2e-b", CPF_B)
        runner = self._runner({
            "e2e-a": {"novo_limite": 3000.0, "timestamp": TIMESTAMP_A},
            "e2e-b": {"novo_limite": 4000.0, "timestamp": TIMESTAMP_B},
        })
        orchestrator.processar_mensagem("e2e-a", "limite a")
        orchestrator.processar_mensagem("e2e-b", "limite b")
        orchestrator.processar_mensagem("e2e-a", "sim")
        orchestrator.processar_mensagem("e2e-b", "não")
        for resposta in ["10000", "formal", "1000", "0", "não"]:
            orchestrator.processar_mensagem("e2e-a", resposta)

        state_a = self._estado("e2e-a")
        state_b = self._estado("e2e-b")
        solicitacoes = self._read_csv(self.solicitacoes_path)
        pedido_a = next(row for row in solicitacoes if row["cpf_cliente"] == CPF_A)
        pedido_b = next(row for row in solicitacoes if row["cpf_cliente"] == CPF_B)
        self.assertEqual(CPF_A, state_a[AUTHENTICATED_CPF])
        self.assertEqual(CPF_B, state_b[AUTHENTICATED_CPF])
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state_a[
            CREDIT_INTERVIEW_STATUS
        ])
        self.assertEqual(CREDIT_INTERVIEW_DECLINED, state_b[
            CREDIT_INTERVIEW_STATUS
        ])
        self.assertEqual({}, state_b[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(TIMESTAMP_B, state_b[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual("aprovado", pedido_a["status_pedido"])
        self.assertEqual("rejeitado", pedido_b["status_pedido"])
        self.assertEqual(TIMESTAMP_A, pedido_a["data_hora_solicitacao"])
        self.assertEqual(TIMESTAMP_B, pedido_b["data_hora_solicitacao"])
        self.assertEqual(800, int(self._cliente(CPF_A)["score_credito"]))
        self.assertEqual(500, int(self._cliente(CPF_B)["score_credito"]))
        self.assertEqual(3000.0, float(self._cliente(CPF_A)["limite_credito"]))
        self.assertEqual(2000.0, float(self._cliente(CPF_B)["limite_credito"]))
        self.assertEqual(2, len(runner.calls))

    def test_07_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        session_id = "e2e-csv-real"
        self._criar_sessao(session_id)
        self._runner({
            session_id: {
                "novo_limite": 3000.0,
                "timestamp": TIMESTAMP_A,
            },
        })

        orchestrator.processar_mensagem(session_id, "aumentar limite")
        orchestrator.processar_mensagem(session_id, "não")

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
