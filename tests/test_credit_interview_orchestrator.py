import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
import orchestrator
from google.adk.sessions import InMemorySessionService
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_ATTEMPTS,
    CREDIT_INTERVIEW_COLLECTING,
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_DECLINED,
    CREDIT_INTERVIEW_INTERRUPTED,
    CREDIT_INTERVIEW_NOT_OFFERED,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_READY,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    criar_estado_inicial,
    oferecer_entrevista_credito,
    registrar_resposta_entrevista,
)


class FakeEvent:
    def __init__(self, text):
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])

    def is_final_response(self):
        return True


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        yield FakeEvent("resposta do runner")


class CreditInterviewOrchestratorTests(unittest.TestCase):
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
        self.clientes_path = Path(self.temp_dir.name) / "clientes.csv"
        self._write_clientes()
        csv_patch = patch("tools.score_tools.CSV_CLIENTES", self.clientes_path)
        csv_patch.start()
        self.addCleanup(csv_patch.stop)

    @staticmethod
    def _estado_em_coleta():
        return aceitar_entrevista_credito(
            oferecer_entrevista_credito(criar_estado_inicial())
        )

    @staticmethod
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _write_clientes(self):
        with self.clientes_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["cpf", "score_credito"],
            )
            writer.writeheader()
            writer.writerows([
                {"cpf": "11111111111", "score_credito": "500"},
                {"cpf": "22222222222", "score_credito": "700"},
            ])

    def _scores(self):
        with self.clientes_path.open(encoding="utf-8", newline="") as handle:
            return {
                row["cpf"]: int(row["score_credito"])
                for row in csv.DictReader(handle)
            }

    def _criar_sessao(self, session_id, state=None):
        if state is None:
            state = criar_estado_inicial()
        orchestrator._run_async(
            self.service.create_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
                state=state,
            )
        )

    def _obter_estado(self, session_id):
        session = orchestrator._run_async(
            self.service.get_session(
                app_name=orchestrator.APP_NAME,
                user_id=session_id,
                session_id=session_id,
            )
        )
        return session.state

    def _estado_antes_da_ultima_resposta(self, cpf="11111111111"):
        state = self._estado_em_coleta()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf
        for valor in [5000.0, "formal", 2000.0, 1]:
            state = registrar_resposta_entrevista(state, valor)
        return state

    def _estado_pronto(self, cpf="11111111111", valores=None):
        if valores is None:
            valores = [5000.0, "formal", 2000.0, 1, "nao"]
        state = self._estado_em_coleta()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf
        for valor in valores:
            state = registrar_resposta_entrevista(state, valor)
        return state

    def test_fora_de_collecting_mantem_caminho_do_runner(self):
        self._criar_sessao("fora-coleta")

        resposta = orchestrator.processar_mensagem("fora-coleta", "olá")

        self.assertEqual("resposta do runner", resposta)
        self.assertEqual(1, len(self.runner.calls))

    def test_collecting_nao_chama_runner(self):
        self._criar_sessao("coleta", self._estado_em_coleta())

        orchestrator.processar_mensagem("coleta", "5000")

        self.assertEqual([], self.runner.calls)

    def test_campo_usado_vem_exclusivamente_do_estado(self):
        state = registrar_resposta_entrevista(self._estado_em_coleta(), 5000.0)
        self._criar_sessao("campo-estado", state)
        original = orchestrator.validar_resposta_entrevista

        with patch.object(
            orchestrator,
            "validar_resposta_entrevista",
            wraps=original,
        ) as validar:
            orchestrator.processar_mensagem("campo-estado", "CLT")

        validar.assert_called_once_with("tipo_emprego", "CLT")

    def test_resposta_valida_avanca_exatamente_um_campo(self):
        self._criar_sessao("avanco", self._estado_em_coleta())

        resposta = orchestrator.processar_mensagem("avanco", "5000")
        state = self._obter_estado("avanco")

        self.assertEqual("tipo_emprego", state[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertIn("situação de emprego", resposta)

    def test_valor_armazenado_e_exclusivamente_o_normalizado(self):
        state = registrar_resposta_entrevista(self._estado_em_coleta(), 5000.0)
        self._criar_sessao("normalizado", state)

        orchestrator.processar_mensagem("normalizado", "CLT")
        state = self._obter_estado("normalizado")

        self.assertEqual("formal", state[CREDIT_INTERVIEW_RESPONSES]["tipo_emprego"])
        self.assertNotIn("CLT", state[CREDIT_INTERVIEW_RESPONSES].values())

    def test_primeira_invalida_mantem_campo_e_incrementa_tentativa(self):
        self._criar_sessao("invalida-uma", self._estado_em_coleta())

        resposta = orchestrator.processar_mensagem("invalida-uma", "mil reais")
        state = self._obter_estado("invalida-uma")

        self.assertEqual(CREDIT_INTERVIEW_COLLECTING, state[CREDIT_INTERVIEW_STATUS])
        self.assertEqual("renda_mensal", state[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual(1, state[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])
        self.assertEqual({}, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertIn("Tente novamente", resposta)

    def test_segunda_invalida_interrompe_sem_resposta_artificial(self):
        state = self._estado_em_coleta()
        self._criar_sessao("invalida-duas", state)
        orchestrator.processar_mensagem("invalida-duas", "inválida")

        resposta = orchestrator.processar_mensagem("invalida-duas", "inválida")
        state = self._obter_estado("invalida-duas")

        self.assertEqual(CREDIT_INTERVIEW_INTERRUPTED, state[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(2, state[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])
        self.assertEqual({}, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertIn("interrompida", resposta)
        self.assertEqual([], self.runner.calls)

    def test_invalidade_posterior_preserva_respostas_validas(self):
        state = self._estado_em_coleta()
        state = registrar_resposta_entrevista(state, 5000.0)
        state = registrar_resposta_entrevista(state, "formal")
        self._criar_sessao("preserva", state)

        orchestrator.processar_mensagem("preserva", "inválida")
        state = self._obter_estado("preserva")

        self.assertEqual(
            {"renda_mensal": 5000.0, "tipo_emprego": "formal"},
            state[CREDIT_INTERVIEW_RESPONSES],
        )

    def test_quinta_valida_processa_uma_vez_e_conclui(self):
        self._criar_sessao("pronta", self._estado_antes_da_ultima_resposta())
        original = orchestrator.processar_entrevista_credito_autorizada

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
            wraps=original,
        ) as processar:
            resposta = orchestrator.processar_mensagem("pronta", "não tenho")

        state = self._obter_estado("pronta")
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state[CREDIT_INTERVIEW_STATUS])
        self.assertTrue(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual("nao", state[CREDIT_INTERVIEW_RESPONSES]["tem_dividas"])
        self.assertIn("perfil financeiro foi atualizado", resposta)
        processar.assert_called_once_with(
            cpf="11111111111",
            renda_mensal=5000.0,
            tipo_emprego="formal",
            despesas_fixas=2000.0,
            num_dependentes=1,
            tem_dividas="nao",
        )
        self.assertEqual(555, self._scores()["11111111111"])
        self.assertEqual([], self.runner.calls)

    def test_segunda_mensagem_apos_completed_nao_reprocessa(self):
        self._criar_sessao("idempotente", self._estado_antes_da_ultima_resposta())
        original = orchestrator.processar_entrevista_credito_autorizada

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
            wraps=original,
        ) as processar:
            orchestrator.processar_mensagem("idempotente", "não")
            score_apos_sucesso = self._hash(self.clientes_path)
            orchestrator.processar_mensagem("idempotente", "outra mensagem")

        self.assertEqual(1, processar.call_count)
        self.assertEqual(score_apos_sucesso, self._hash(self.clientes_path))

    def test_completed_preexistente_nao_processa(self):
        state = concluir_processamento_entrevista(self._estado_pronto())
        self._criar_sessao("ja-concluida", state)
        antes = self._hash(self.clientes_path)

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar:
            orchestrator.processar_mensagem("ja-concluida", "mensagem")

        processar.assert_not_called()
        self.assertEqual(antes, self._hash(self.clientes_path))

    def test_falha_controlada_preserva_ready_respostas_e_retorno_falso(self):
        state = self._estado_antes_da_ultima_resposta()
        respostas_esperadas = dict(state[CREDIT_INTERVIEW_RESPONSES])
        respostas_esperadas["tem_dividas"] = "nao"
        self._criar_sessao("falha", state)
        falha = {
            "processado": False,
            "perfil_atualizado": False,
            "retornar_credito": False,
            "campo_invalido": None,
            "erro": "Falha controlada.",
        }

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
            return_value=falha,
        ):
            resposta = orchestrator.processar_mensagem("falha", "não")

        state = self._obter_estado("falha")
        self.assertEqual(CREDIT_INTERVIEW_READY, state[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(respostas_esperadas, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertIn("Falha controlada", resposta)
        self.assertEqual([], self.runner.calls)

    def test_ready_sem_um_campo_nao_processa_e_retorna_erro_controlado(self):
        state = self._estado_pronto()
        del state[CREDIT_INTERVIEW_RESPONSES]["despesas_fixas"]
        self._criar_sessao("incompleta", state)

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar:
            resposta = orchestrator.processar_mensagem("incompleta", "mensagem")

        persistido = self._obter_estado("incompleta")
        processar.assert_not_called()
        self.assertEqual(CREDIT_INTERVIEW_READY, persistido[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(persistido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIn("faltam respostas obrigatórias", resposta)
        self.assertEqual([], self.runner.calls)

    def test_ready_encerrada_nao_processa(self):
        state = self._estado_pronto()
        state[CONVERSATION_ENDED] = True
        self._criar_sessao("pronta-encerrada", state)

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar:
            orchestrator.processar_mensagem("pronta-encerrada", "mensagem")

        processar.assert_not_called()
        self.assertEqual(state, self._obter_estado("pronta-encerrada"))

    def test_estados_diferentes_de_ready_nao_processam_financeiramente(self):
        statuses = [
            CREDIT_INTERVIEW_NOT_OFFERED,
            CREDIT_INTERVIEW_OFFERED,
            CREDIT_INTERVIEW_COLLECTING,
            CREDIT_INTERVIEW_DECLINED,
            CREDIT_INTERVIEW_INTERRUPTED,
            CREDIT_INTERVIEW_COMPLETED,
        ]

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
        ) as processar:
            for indice, status in enumerate(statuses):
                session_id = f"status-{indice}"
                state = criar_estado_inicial()
                state[CREDIT_INTERVIEW_STATUS] = status
                if status == CREDIT_INTERVIEW_COLLECTING:
                    state[CREDIT_INTERVIEW_CURRENT_FIELD] = "renda_mensal"
                self._criar_sessao(session_id, state)
                orchestrator.processar_mensagem(session_id, "inválida")

        processar.assert_not_called()

    def test_duas_sessoes_prontas_processam_somente_seus_cpfs(self):
        state_a = self._estado_pronto("11111111111")
        state_b = self._estado_pronto(
            "22222222222",
            [1500.0, "autonomo", 1200.0, 3, "sim"],
        )
        self._criar_sessao("processa-a", state_a)
        self._criar_sessao("processa-b", state_b)
        original = orchestrator.processar_entrevista_credito_autorizada

        with patch.object(
            orchestrator,
            "processar_entrevista_credito_autorizada",
            wraps=original,
        ) as processar:
            orchestrator.processar_mensagem("processa-a", "processar")
            orchestrator.processar_mensagem("processa-b", "processar")

        self.assertEqual(2, processar.call_count)
        self.assertEqual(
            {"11111111111": 555, "22222222222": 167},
            self._scores(),
        )
        self.assertEqual(
            CREDIT_INTERVIEW_COMPLETED,
            self._obter_estado("processa-a")[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual(
            CREDIT_INTERVIEW_COMPLETED,
            self._obter_estado("processa-b")[CREDIT_INTERVIEW_STATUS],
        )

    def test_processamento_final_nao_altera_csvs_reais(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        self._criar_sessao("csv-final", self._estado_pronto())

        orchestrator.processar_mensagem("csv-final", "processar")

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_coleta_nao_altera_csvs(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        self._criar_sessao("csv", self._estado_em_coleta())

        orchestrator.processar_mensagem("csv", "5000")

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_conversation_ended_impede_avanco(self):
        state = self._estado_em_coleta()
        state[CONVERSATION_ENDED] = True
        self._criar_sessao("encerrada", state)

        orchestrator.processar_mensagem("encerrada", "5000")
        persistido = self._obter_estado("encerrada")

        self.assertEqual(state, persistido)

    def test_duas_sessoes_nao_compartilham_respostas_ou_tentativas(self):
        self._criar_sessao("sessao-a", self._estado_em_coleta())
        self._criar_sessao("sessao-b", self._estado_em_coleta())

        orchestrator.processar_mensagem("sessao-a", "5000")
        orchestrator.processar_mensagem("sessao-b", "inválida")
        state_a = self._obter_estado("sessao-a")
        state_b = self._obter_estado("sessao-b")

        self.assertEqual({"renda_mensal": 5000.0}, state_a[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual({}, state_b[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(0, state_a[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])
        self.assertEqual(1, state_b[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])


if __name__ == "__main__":
    unittest.main()
