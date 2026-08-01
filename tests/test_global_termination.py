import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import orchestrator
from google.adk.sessions import InMemorySessionService
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_ATTEMPTS,
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_READY,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    MENSAGEM_ATENDIMENTO_ENCERRADO,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    criar_estado_inicial,
    encerrar_estado_atendimento,
    oferecer_entrevista_credito,
    registrar_resposta_entrevista,
)
import tools.auth_tools as auth_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP = "2026-07-04T10:20:30.123456+00:00"


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("Runner não deveria ser chamado")


class FakeToolContext:
    def __init__(self, state=None):
        self.state = state if state is not None else criar_estado_inicial()


class GlobalTerminationTests(unittest.TestCase):
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
        self._write_csv(
            self.clientes_path,
            ["cpf", "score_credito", "limite_credito"],
            [{
                "cpf": CPF_A,
                "score_credito": "500",
                "limite_credito": "1000.00",
            }],
        )
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [{"limite_maximo": "5000.00", "score_minimo": "700"}],
        )
        self._write_csv(
            self.solicitacoes_path,
            [
                "cpf_cliente",
                "data_hora_solicitacao",
                "limite_atual",
                "novo_limite_solicitado",
                "status_pedido",
            ],
            [{
                "cpf_cliente": CPF_A,
                "data_hora_solicitacao": TIMESTAMP,
                "limite_atual": "1000.00",
                "novo_limite_solicitado": "3000.00",
                "status_pedido": "rejeitado",
            }],
        )

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
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _hashes_temporarios(self):
        return {
            self.clientes_path: self._hash(self.clientes_path),
            self.score_path: self._hash(self.score_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }

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
    def _estado_autenticado(cpf=CPF_A):
        state = criar_estado_inicial()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf
        return state

    def _estado_oferecido(self):
        state = self._estado_autenticado()
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = TIMESTAMP
        return oferecer_entrevista_credito(state)

    def _estado_coletando(self):
        return aceitar_entrevista_credito(self._estado_oferecido())

    def _estado_antes_da_quinta(self):
        state = self._estado_coletando()
        for resposta in [5000.0, "formal", 2000.0, 1]:
            state = registrar_resposta_entrevista(state, resposta)
        return state

    def _estado_pronto(self):
        return registrar_resposta_entrevista(
            self._estado_antes_da_quinta(),
            "nao",
        )

    def _estado_completed_pending(self):
        return concluir_processamento_entrevista(self._estado_pronto())

    def test_01_vocabulario_explicito_encerra(self):
        mensagens = [
            "encerrar",
            "quero encerrar",
            "finalizar atendimento",
            "quero sair",
            "tchau",
            "até mais",
            "   PODE ENCERRAR!!!   ",
        ]
        for indice, mensagem in enumerate(mensagens):
            with self.subTest(mensagem=mensagem):
                session_id = f"termina-{indice}"
                self._criar_sessao(session_id, self._estado_autenticado())

                resposta = orchestrator.processar_mensagem(
                    session_id,
                    mensagem,
                )

                self.assertTrue(self._estado(session_id)[CONVERSATION_ENDED])
                self.assertEqual(MENSAGEM_ATENDIMENTO_ENCERRADO, resposta)
        self.assertEqual([], self.runner.calls)

    def test_02_negativas_e_texto_desconhecido_nao_encerram(self):
        mensagens = [
            "não quero encerrar",
            "não encerre",
            "quero continuar",
            "não quero sair",
            "talvez depois",
        ]
        for mensagem in mensagens:
            with self.subTest(mensagem=mensagem):
                self.assertEqual(
                    "not_terminate",
                    orchestrator._classificar_encerramento(mensagem),
                )

    def test_03_texto_nao_reconhecido_segue_fluxo_normal(self):
        state = self._estado_autenticado()
        self._criar_sessao("normal", state)
        runner = unittest.mock.MagicMock()
        runner.run.return_value = []

        with patch.object(orchestrator, "_runner", runner):
            orchestrator.processar_mensagem("normal", "quero continuar")

        self.assertFalse(self._estado("normal")[CONVERSATION_ENDED])
        runner.run.assert_called_once()

    def test_04_encerramento_tem_precedencia_sobre_todos_os_fluxos(self):
        self._criar_sessao("prioridade", self._estado_autenticado())

        with patch.object(
            orchestrator,
            "_recuperar_retorno_pendente",
        ) as retorno, patch.object(
            orchestrator,
            "_processar_resposta_oferta",
        ) as oferta, patch.object(
            orchestrator,
            "_processar_coleta_entrevista",
        ) as coleta:
            resposta = orchestrator.processar_mensagem(
                "prioridade",
                "encerrar",
            )

        retorno.assert_not_called()
        oferta.assert_not_called()
        coleta.assert_not_called()
        self.assertEqual([], self.runner.calls)
        self.assertEqual(MENSAGEM_ATENDIMENTO_ENCERRADO, resposta)

    def test_05_encerramento_em_offered_nao_aceita_nem_recusa(self):
        state = self._estado_oferecido()
        self._criar_sessao("offered", state)

        orchestrator.processar_mensagem("offered", "quero encerrar")

        persistido = self._estado("offered")
        self.assertTrue(persistido[CONVERSATION_ENDED])
        self.assertEqual(CREDIT_INTERVIEW_OFFERED, persistido[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(TIMESTAMP, persistido[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual([], self.runner.calls)

    def test_06_encerramento_collecting_nao_registra_nem_incrementa(self):
        state = self._estado_coletando()
        respostas_antes = dict(state[CREDIT_INTERVIEW_RESPONSES])
        tentativas_antes = dict(state[CREDIT_INTERVIEW_ATTEMPTS])
        self._criar_sessao("collecting", state)

        with patch.object(
            orchestrator,
            "validar_resposta_entrevista",
        ) as validar:
            orchestrator.processar_mensagem("collecting", "encerrar")

        persistido = self._estado("collecting")
        validar.assert_not_called()
        self.assertEqual(respostas_antes, persistido[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(tentativas_antes, persistido[CREDIT_INTERVIEW_ATTEMPTS])
        self.assertEqual("renda_mensal", persistido[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual([], self.runner.calls)

    def test_07_encerramento_antes_da_quinta_nao_processa_score(self):
        state = self._estado_antes_da_quinta()
        self._criar_sessao("antes-quinta", state)

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar:
            orchestrator.processar_mensagem("antes-quinta", "finalizar")

        processar.assert_not_called()
        self.assertEqual(state[CREDIT_INTERVIEW_RESPONSES], self._estado(
            "antes-quinta"
        )[CREDIT_INTERVIEW_RESPONSES])

    def test_08_encerramento_ready_nao_processa_financeiramente(self):
        state = self._estado_pronto()
        self.assertEqual(CREDIT_INTERVIEW_READY, state[CREDIT_INTERVIEW_STATUS])
        self._criar_sessao("ready", state)

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar:
            orchestrator.processar_mensagem("ready", "tchau")

        processar.assert_not_called()
        self.assertEqual(CREDIT_INTERVIEW_READY, self._estado(
            "ready"
        )[CREDIT_INTERVIEW_STATUS])

    def test_09_completed_pending_encerra_antes_da_reanalise(self):
        state = self._estado_completed_pending()
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state[CREDIT_INTERVIEW_STATUS])
        self._criar_sessao("pending", state)
        antes = self._hashes_temporarios()

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            orchestrator.processar_mensagem("pending", "quero sair")

        persistido = self._estado("pending")
        reanalisar.assert_not_called()
        self.assertTrue(persistido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP, persistido[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertEqual([], self.runner.calls)

    def test_10_encerramento_fora_da_entrevista_persiste_delta_oficial(self):
        self._criar_sessao("fora", self._estado_autenticado())

        resposta = orchestrator.processar_mensagem("fora", "encerrar")

        session = self._sessao("fora")
        self.assertEqual(MENSAGEM_ATENDIMENTO_ENCERRADO, resposta)
        self.assertEqual(
            {CONVERSATION_ENDED: True},
            session.events[-1].actions.state_delta,
        )
        self.assertEqual([], self.runner.calls)

    def test_11_sessao_encerrada_e_segunda_mensagem_sao_idempotentes(self):
        self._criar_sessao("idempotente", self._estado_autenticado())
        orchestrator.processar_mensagem("idempotente", "encerrar")
        eventos_apos_primeira = len(self._sessao("idempotente").events)

        with patch.object(
            orchestrator,
            "_recuperar_retorno_pendente",
        ) as retorno, patch.object(
            orchestrator,
            "_processar_resposta_oferta",
        ) as oferta, patch.object(
            orchestrator,
            "_processar_coleta_entrevista",
        ) as coleta:
            resposta = orchestrator.processar_mensagem(
                "idempotente",
                "qualquer mensagem",
            )

        self.assertEqual(MENSAGEM_ATENDIMENTO_ENCERRADO, resposta)
        self.assertEqual(eventos_apos_primeira, len(
            self._sessao("idempotente").events
        ))
        retorno.assert_not_called()
        oferta.assert_not_called()
        coleta.assert_not_called()
        self.assertEqual([], self.runner.calls)

    def test_12_duas_sessoes_permanecem_isoladas(self):
        self._criar_sessao("usuario-a", self._estado_autenticado(CPF_A))
        self._criar_sessao("usuario-b", self._estado_autenticado(CPF_B))

        orchestrator.processar_mensagem("usuario-a", "tchau")

        self.assertTrue(self._estado("usuario-a")[CONVERSATION_ENDED])
        self.assertFalse(self._estado("usuario-b")[CONVERSATION_ENDED])
        self.assertEqual(CPF_B, self._estado("usuario-b")[AUTHENTICATED_CPF])

    def test_13_classificador_e_puro(self):
        with patch.object(orchestrator, "_run_async") as executar, patch.object(
            orchestrator._runner,
            "run",
        ) as runner, patch(
            "tools.credito_tools.pd.read_csv"
        ) as credito_csv, patch(
            "tools.score_tools.pd.read_csv"
        ) as score_csv:
            resultado = orchestrator._classificar_encerramento("ENCERRAR!!!")

        self.assertEqual("terminate", resultado)
        executar.assert_not_called()
        runner.assert_not_called()
        credito_csv.assert_not_called()
        score_csv.assert_not_called()

    def test_14_tool_publica_reutiliza_nucleo_compartilhado(self):
        context = FakeToolContext(self._estado_autenticado())
        original = encerrar_estado_atendimento

        with patch.object(
            auth_tools,
            "encerrar_estado_atendimento",
            wraps=original,
        ) as nucleo:
            resultado = auth_tools.encerrar_atendimento(context)

        nucleo.assert_called_once_with(context.state)
        self.assertTrue(context.state[CONVERSATION_ENDED])
        self.assertEqual({
            "encerrado": True,
            "mensagem": MENSAGEM_ATENDIMENTO_ENCERRADO,
        }, resultado)

    def test_15_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        self._criar_sessao("csv-real", self._estado_completed_pending())

        orchestrator.processar_mensagem("csv-real", "encerrar")

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
