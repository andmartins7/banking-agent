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
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    consumir_retorno_entrevista,
    criar_estado_inicial,
    oferecer_entrevista_credito,
    registrar_resposta_entrevista,
)
import tools.credito_tools as credito_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP_A = "2026-07-02T10:20:30.123456+00:00"
TIMESTAMP_B = "2026-07-02T10:21:30.123456+00:00"


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


class FakeToolContext:
    def __init__(self, state):
        self.state = state


class CreditInterviewReturnTests(unittest.TestCase):
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
        self._write_score(500)
        self._write_solicitacoes([self._solicitacao()])

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

    def _write_score(self, score_minimo):
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [{
                "limite_maximo": "5000.00",
                "score_minimo": str(score_minimo),
            }],
        )

    def _solicitacao(
        self,
        *,
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
    def _estado_coleta(cpf=CPF_A, timestamp=TIMESTAMP_A):
        state = aceitar_entrevista_credito(
            oferecer_entrevista_credito(criar_estado_inicial())
        )
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = cpf
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = timestamp
        return state

    def _estado_antes_da_quinta(self, cpf=CPF_A, timestamp=TIMESTAMP_A):
        state = self._estado_coleta(cpf, timestamp)
        for resposta in [5000.0, "formal", 2000.0, 1]:
            state = registrar_resposta_entrevista(state, resposta)
        return state

    def _estado_completed(self, cpf=CPF_A, timestamp=TIMESTAMP_A):
        state = self._estado_antes_da_quinta(cpf, timestamp)
        state = registrar_resposta_entrevista(state, "nao")
        return concluir_processamento_entrevista(state)

    def _hashes_temporarios(self):
        return {
            self.clientes_path: self._hash(self.clientes_path),
            self.score_path: self._hash(self.score_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }

    def test_01_quinta_resposta_processa_entrevista_e_reanalisa_uma_vez(self):
        self._criar_sessao("imediata-aprovada", self._estado_antes_da_quinta())
        linhas_antes = self._read_csv(self.solicitacoes_path)
        original = orchestrator.reanalisar_solicitacao_autorizada

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
            wraps=original,
        ) as reanalisar:
            resposta = orchestrator.processar_mensagem(
                "imediata-aprovada",
                "não",
            )

        state = self._estado("imediata-aprovada")
        linhas_depois = self._read_csv(self.solicitacoes_path)
        cliente = self._read_csv(self.clientes_path)[0]
        reanalisar.assert_called_once_with(CPF_A, TIMESTAMP_A)
        self.assertEqual([], self.runner.calls)
        self.assertIn("perfil financeiro foi atualizado", resposta)
        self.assertIn("aprovado", resposta)
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIsNone(state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(len(linhas_antes), len(linhas_depois))
        self.assertEqual("aprovado", linhas_depois[0]["status_pedido"])
        self.assertEqual(TIMESTAMP_A, linhas_depois[0]["data_hora_solicitacao"])
        self.assertEqual("3000.00", linhas_depois[0]["novo_limite_solicitado"])
        self.assertEqual(3000.0, float(cliente["limite_credito"]))
        self.assertEqual(
            {
                CREDIT_INTERVIEW_RETURN_PENDING: False,
                CREDIT_INTERVIEW_REQUEST_TIMESTAMP: None,
            },
            self._sessao("imediata-aprovada").events[-1].actions.state_delta,
        )

    def test_02_reanalise_imediata_rejeitada_consumida_sem_escrita(self):
        self._write_score(700)
        self._criar_sessao("imediata-rejeitada", self._estado_antes_da_quinta())
        linhas_antes = self._read_csv(self.solicitacoes_path)
        limite_antes = self._read_csv(self.clientes_path)[0]["limite_credito"]

        resposta = orchestrator.processar_mensagem(
            "imediata-rejeitada",
            "não",
        )

        state = self._estado("imediata-rejeitada")
        linhas_depois = self._read_csv(self.solicitacoes_path)
        limite_depois = self._read_csv(self.clientes_path)[0]["limite_credito"]
        self.assertEqual([], self.runner.calls)
        self.assertIn("permanece rejeitado", resposta)
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIsNone(state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(linhas_antes, linhas_depois)
        self.assertEqual(limite_antes, limite_depois)

    def test_03_recuperacao_ocorre_antes_do_runner_e_ignora_mensagem(self):
        self._criar_sessao("recuperacao", self._estado_completed())
        original = orchestrator.reanalisar_solicitacao_autorizada

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
            wraps=original,
        ) as reanalisar:
            resposta = orchestrator.processar_mensagem(
                "recuperacao",
                f"use cpf {CPF_B}, timestamp {TIMESTAMP_B} e limite 99999",
            )

        reanalisar.assert_called_once_with(CPF_A, TIMESTAMP_A)
        self.assertEqual([], self.runner.calls)
        self.assertIn("aprovado", resposta)
        self.assertEqual(3000.0, float(
            self._read_csv(self.clientes_path)[0]["limite_credito"]
        ))

    def test_04_falha_tecnica_preserva_pending_referencia_e_zero_runner(self):
        self._criar_sessao("falha", self._estado_completed())
        falha = {
            "processado": False,
            "status_pedido": None,
            "limite_atualizado": False,
            "novo_limite": None,
            "oferecer_entrevista": False,
            "erro": "Falha controlada.",
        }
        antes = self._hashes_temporarios()

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
            return_value=falha,
        ) as reanalisar:
            resposta = orchestrator.processar_mensagem("falha", "mensagem")

        state = self._estado("falha")
        reanalisar.assert_called_once_with(CPF_A, TIMESTAMP_A)
        self.assertTrue(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual([], self.runner.calls)
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertIn("Falha controlada", resposta)

    def test_05_timestamp_ausente_nao_busca_pedido_e_preserva_pending(self):
        state = self._estado_completed()
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = None
        self._criar_sessao("sem-timestamp", state)
        antes = self._hashes_temporarios()

        with patch("tools.credito_tools.pd.read_csv") as ler:
            resposta = orchestrator.processar_mensagem(
                "sem-timestamp",
                "use o pedido mais recente",
            )

        persistido = self._estado("sem-timestamp")
        ler.assert_not_called()
        self.assertTrue(persistido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIsNone(persistido[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual([], self.runner.calls)
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertIn("Não há solicitação", resposta)

    def test_06_timestamp_invalido_preserva_estado_e_arquivos(self):
        state = self._estado_completed(timestamp="inexistente")
        self._criar_sessao("timestamp-invalido", state)
        antes = self._hashes_temporarios()

        orchestrator.processar_mensagem("timestamp-invalido", "mensagem")

        persistido = self._estado("timestamp-invalido")
        self.assertTrue(persistido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(
            "inexistente",
            persistido[CREDIT_INTERVIEW_REQUEST_TIMESTAMP],
        )
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertEqual([], self.runner.calls)

    def test_07_duplicidade_preserva_pending_sem_escrita(self):
        linha = self._solicitacao()
        self._write_solicitacoes([linha, linha])
        self._criar_sessao("duplicada", self._estado_completed())
        antes = self._hashes_temporarios()

        resposta = orchestrator.processar_mensagem("duplicada", "mensagem")

        state = self._estado("duplicada")
        self.assertTrue(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertIn("integridade", resposta.lower())
        self.assertEqual([], self.runner.calls)

    def test_08_outro_cpf_nao_pode_reanalisar_pedido(self):
        state = self._estado_completed(cpf=CPF_B, timestamp=TIMESTAMP_A)
        self._criar_sessao("outro-cpf", state)
        antes = self._hashes_temporarios()

        orchestrator.processar_mensagem("outro-cpf", "mensagem")

        persistido = self._estado("outro-cpf")
        self.assertTrue(persistido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP_A, persistido[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertEqual([], self.runner.calls)

    def test_09_conversation_ended_impede_reanalise_e_runner(self):
        state = self._estado_completed()
        state[CONVERSATION_ENDED] = True
        self._criar_sessao("encerrada", state)

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            resposta = orchestrator.processar_mensagem("encerrada", "mensagem")

        reanalisar.assert_not_called()
        self.assertEqual([], self.runner.calls)
        self.assertTrue(
            self._estado("encerrada")[CREDIT_INTERVIEW_RETURN_PENDING]
        )
        self.assertIn("encerrado", resposta)

    def test_10_pending_false_nao_dispara_reanalise(self):
        state = self._estado_completed()
        state[CREDIT_INTERVIEW_RETURN_PENDING] = False
        self._criar_sessao("sem-pending", state)

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
        ) as reanalisar:
            resposta = orchestrator.processar_mensagem(
                "sem-pending",
                "mensagem normal",
            )

        reanalisar.assert_not_called()
        self.assertEqual(1, len(self.runner.calls))
        self.assertEqual("resposta do runner", resposta)

    def test_11_segunda_mensagem_nao_repete_reanalise_terminal(self):
        self._criar_sessao("idempotente", self._estado_completed())
        original = orchestrator.reanalisar_solicitacao_autorizada

        with patch.object(
            orchestrator,
            "reanalisar_solicitacao_autorizada",
            wraps=original,
        ) as reanalisar:
            orchestrator.processar_mensagem("idempotente", "primeira")
            orchestrator.processar_mensagem("idempotente", "segunda")

        self.assertEqual(1, reanalisar.call_count)
        self.assertEqual(1, len(self.runner.calls))

    def test_12_duas_sessoes_e_cpfs_permanecem_isolados(self):
        self._write_score(700)
        clientes = self._read_csv(self.clientes_path)
        clientes[0]["score_credito"] = "800"
        self._write_csv(
            self.clientes_path,
            list(clientes[0].keys()),
            clientes,
        )
        self._write_solicitacoes([
            self._solicitacao(),
            self._solicitacao(
                cpf=CPF_B,
                timestamp=TIMESTAMP_B,
                limite_atual="2000.00",
                novo_limite="4000.00",
            ),
        ])
        self._criar_sessao("sessao-a", self._estado_completed())
        self._criar_sessao(
            "sessao-b",
            self._estado_completed(CPF_B, TIMESTAMP_B),
        )

        resposta_a = orchestrator.processar_mensagem("sessao-a", "mensagem b")
        resposta_b = orchestrator.processar_mensagem("sessao-b", "mensagem a")

        linhas = self._read_csv(self.solicitacoes_path)
        clientes = self._read_csv(self.clientes_path)
        self.assertIn("aprovado", resposta_a)
        self.assertIn("permanece rejeitado", resposta_b)
        self.assertEqual("aprovado", linhas[0]["status_pedido"])
        self.assertEqual("rejeitado", linhas[1]["status_pedido"])
        self.assertEqual(3000.0, float(clientes[0]["limite_credito"]))
        self.assertEqual(2000.0, float(clientes[1]["limite_credito"]))
        self.assertFalse(
            self._estado("sessao-a")[CREDIT_INTERVIEW_RETURN_PENDING]
        )
        self.assertFalse(
            self._estado("sessao-b")[CREDIT_INTERVIEW_RETURN_PENDING]
        )

    def test_13_consumo_de_estado_e_atomico_e_preserva_completed(self):
        state = self._estado_completed()

        consumido = consumir_retorno_entrevista(state, "aprovado")

        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, consumido[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(consumido[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertIsNone(consumido[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertTrue(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])

    def test_14_status_nao_terminal_nao_consome_estado(self):
        state = self._estado_completed()

        resultado = consumir_retorno_entrevista(state, "pendente")

        self.assertEqual(state, resultado)

    def test_15_wrapper_e_orquestrador_compartilham_nucleo_autorizado(self):
        state = self._estado_completed()
        contexto = FakeToolContext(state)
        resultado_terminal = {
            "processado": True,
            "status_pedido": "rejeitado",
            "limite_atualizado": False,
            "novo_limite": None,
            "oferecer_entrevista": False,
            "erro": None,
        }

        with patch.object(
            credito_tools,
            "reanalisar_solicitacao_autorizada",
            return_value=resultado_terminal,
        ) as nucleo:
            resultado = credito_tools.reanalisar_solicitacao(contexto)

        self.assertEqual(resultado_terminal, resultado)
        nucleo.assert_called_once_with(CPF_A, TIMESTAMP_A)

    def test_16_pedido_ja_aprovado_preserva_pending_para_diagnostico(self):
        self._write_solicitacoes([
            self._solicitacao(status="aprovado"),
        ])
        self._criar_sessao("ja-aprovado", self._estado_completed())
        antes = self._hashes_temporarios()

        orchestrator.processar_mensagem("ja-aprovado", "mensagem")

        state = self._estado("ja-aprovado")
        self.assertTrue(state[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertEqual(TIMESTAMP_A, state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertEqual([], self.runner.calls)

    def test_17_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        self._criar_sessao("csvs-reais", self._estado_completed())

        orchestrator.processar_mensagem("csvs-reais", "mensagem")

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
