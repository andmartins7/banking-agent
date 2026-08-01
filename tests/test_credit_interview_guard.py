import copy
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_DECLINED,
    CREDIT_INTERVIEW_INTERRUPTED,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    criar_estado_inicial,
    oferecer_entrevista_credito,
    recusar_entrevista_credito,
    registrar_resposta_entrevista,
)
import tools.score_tools as score_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP = "2026-07-01T10:20:30.123456+00:00"
OUTRO_TIMESTAMP = "2026-07-01T10:21:30.123456+00:00"
RESPOSTAS = {
    "renda_mensal": 5000.0,
    "tipo_emprego": "formal",
    "despesas_fixas": 2000.0,
    "num_dependentes": 1,
    "tem_dividas": "nao",
}


class FakeToolContext:
    def __init__(self, state):
        self.state = state


class CreditInterviewGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.clientes_path = self.base / "clientes.csv"
        self.solicitacoes_path = self.base / "solicitacoes.csv"
        self._write_clientes()
        self._write_solicitacoes()

        clientes_patch = patch(
            "tools.score_tools.CSV_CLIENTES",
            self.clientes_path,
        )
        solicitacoes_patch = patch(
            "tools.credito_tools.CSV_SOLICITACOES",
            self.solicitacoes_path,
        )
        clientes_patch.start()
        solicitacoes_patch.start()
        self.addCleanup(clientes_patch.stop)
        self.addCleanup(solicitacoes_patch.stop)

    @staticmethod
    def _write_csv(path, fieldnames, rows):
        with Path(path).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

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
            [{
                "cpf": CPF_A,
                "nome": "Cliente A",
                "data_nascimento": "01/01/1990",
                "score_credito": "600",
                "limite_credito": "1000.00",
            }],
        )

    def _solicitacao(
        self,
        *,
        cpf=CPF_A,
        timestamp=TIMESTAMP,
        status="rejeitado",
    ):
        return {
            "cpf_cliente": cpf,
            "data_hora_solicitacao": timestamp,
            "limite_atual": "1000.00",
            "novo_limite_solicitado": "3000.00",
            "status_pedido": status,
        }

    def _write_solicitacoes(self, rows=None):
        self._write_csv(
            self.solicitacoes_path,
            [
                "cpf_cliente",
                "data_hora_solicitacao",
                "limite_atual",
                "novo_limite_solicitado",
                "status_pedido",
            ],
            [self._solicitacao()] if rows is None else rows,
        )

    @staticmethod
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _hashes(self):
        return {
            self.clientes_path: self._hash(self.clientes_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }

    @staticmethod
    def _estado_autenticado():
        state = criar_estado_inicial()
        state[AUTHENTICATED] = True
        state[AUTHENTICATED_CPF] = CPF_A
        return state

    def _estado_pronto(self):
        state = self._estado_autenticado()
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = TIMESTAMP
        state = oferecer_entrevista_credito(state)
        state = aceitar_entrevista_credito(state)
        for resposta in RESPOSTAS.values():
            state = registrar_resposta_entrevista(state, resposta)
        self.assertEqual(
            "ready_for_processing",
            state[CREDIT_INTERVIEW_STATUS],
        )
        return state

    @staticmethod
    def _argumentos(**overrides):
        argumentos = dict(RESPOSTAS)
        argumentos.update(overrides)
        return argumentos

    def _processar(self, state, **overrides):
        return score_tools.processar_entrevista_credito(
            **self._argumentos(**overrides),
            tool_context=FakeToolContext(state),
        )

    def _assert_bloqueado(self, state, **overrides):
        estado_antes = copy.deepcopy(state)
        hashes_antes = self._hashes()
        with (
            patch(
                "tools.score_tools.processar_entrevista_credito_autorizada"
            ) as processar,
            patch(
                "tools.credito_tools.reanalisar_solicitacao_autorizada"
            ) as reanalisar,
        ):
            resultado = self._processar(state, **overrides)

        self.assertFalse(resultado["processado"])
        self.assertFalse(resultado["perfil_atualizado"])
        self.assertFalse(resultado["retornar_credito"])
        self.assertIsNotNone(resultado["erro"])
        self.assertNotIn(CPF_A, repr(resultado))
        self.assertEqual(estado_antes, state)
        self.assertEqual(hashes_antes, self._hashes())
        processar.assert_not_called()
        reanalisar.assert_not_called()
        return resultado

    def test_01_sessao_apenas_autenticada_not_offered_e_bloqueada(self):
        self._assert_bloqueado(self._estado_autenticado())

    def test_01b_sessao_ausente_e_bloqueada_antes_de_io(self):
        hashes_antes = self._hashes()
        with (
            patch("tools.credito_tools.pd.read_csv") as ler,
            patch(
                "tools.score_tools.processar_entrevista_credito_autorizada"
            ) as processar,
        ):
            resultado = score_tools.processar_entrevista_credito(
                **self._argumentos(),
                tool_context=None,
            )

        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        self.assertEqual(hashes_antes, self._hashes())
        ler.assert_not_called()
        processar.assert_not_called()

    def test_02_estados_anteriores_ou_terminais_sao_bloqueados(self):
        oferecido = oferecer_entrevista_credito(self._estado_autenticado())
        coletando = aceitar_entrevista_credito(oferecido)
        recusado = recusar_entrevista_credito(oferecido)
        interrompido = copy.deepcopy(coletando)
        interrompido[CREDIT_INTERVIEW_STATUS] = CREDIT_INTERVIEW_INTERRUPTED
        concluido = concluir_processamento_entrevista(self._estado_pronto())

        for nome, state in {
            CREDIT_INTERVIEW_OFFERED: oferecido,
            "collecting": coletando,
            CREDIT_INTERVIEW_DECLINED: recusado,
            CREDIT_INTERVIEW_INTERRUPTED: interrompido,
            CREDIT_INTERVIEW_COMPLETED: concluido,
        }.items():
            with self.subTest(status=nome):
                self._assert_bloqueado(state)

    def test_03_ready_sem_timestamp_e_bloqueado(self):
        state = self._estado_pronto()
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = None
        self._assert_bloqueado(state)

    def test_04_ready_com_timestamp_invalido_e_bloqueado_sem_io(self):
        state = self._estado_pronto()
        state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = "timestamp-controlado-pelo-llm"
        with patch("tools.credito_tools.pd.read_csv") as ler:
            self._assert_bloqueado(state)
        ler.assert_not_called()

    def test_05_ready_sem_uma_das_cinco_respostas_e_bloqueado(self):
        for campo in RESPOSTAS:
            with self.subTest(campo=campo):
                state = self._estado_pronto()
                del state[CREDIT_INTERVIEW_RESPONSES][campo]
                self._assert_bloqueado(state)

        state = self._estado_pronto()
        state[CREDIT_INTERVIEW_RESPONSES]["campo_extra"] = "não autorizado"
        self._assert_bloqueado(state)

    def test_06_resposta_do_estado_nao_normalizada_e_bloqueada(self):
        state = self._estado_pronto()
        state[CREDIT_INTERVIEW_RESPONSES]["tipo_emprego"] = "CLT"
        self._assert_bloqueado(state, tipo_emprego="CLT")

    def test_07_pedido_inexistente_e_bloqueado(self):
        self._write_solicitacoes([])
        self._assert_bloqueado(self._estado_pronto())

    def test_08_pedido_duplicado_por_cpf_e_timestamp_e_bloqueado(self):
        pedido = self._solicitacao()
        self._write_solicitacoes([pedido, pedido])
        resultado = self._assert_bloqueado(self._estado_pronto())
        self.assertIn("integridade", resultado["erro"].lower())

    def test_09_pedido_de_outro_cpf_e_bloqueado(self):
        self._write_solicitacoes([self._solicitacao(cpf=CPF_B)])
        self._assert_bloqueado(self._estado_pronto())

    def test_10_outro_timestamp_nao_pode_ser_selecionado(self):
        self._write_solicitacoes([
            self._solicitacao(timestamp=OUTRO_TIMESTAMP),
        ])
        self._assert_bloqueado(self._estado_pronto())

    def test_11_pedido_aprovado_ou_pendente_e_bloqueado(self):
        for status in ["aprovado", "pendente"]:
            with self.subTest(status=status):
                self._write_solicitacoes([
                    self._solicitacao(status=status),
                ])
                self._assert_bloqueado(self._estado_pronto())

    def test_12_sessao_encerrada_e_bloqueada_antes_de_io(self):
        state = self._estado_pronto()
        state[CONVERSATION_ENDED] = True
        with patch("tools.credito_tools.pd.read_csv") as ler:
            self._assert_bloqueado(state)
        ler.assert_not_called()

    def test_13_retorno_pendente_bloqueia_novo_processamento(self):
        state = self._estado_pronto()
        state[CREDIT_INTERVIEW_RETURN_PENDING] = True
        self._assert_bloqueado(state)

    def test_14_renda_artificialmente_alta_do_llm_nao_altera_score(self):
        score_antes = self._hash(self.clientes_path)
        self._assert_bloqueado(
            self._estado_pronto(),
            renda_mensal=1_000_000_000.0,
        )
        self.assertEqual(score_antes, self._hash(self.clientes_path))

    def test_15_emprego_e_divida_do_llm_nao_substituem_estado(self):
        state = self._estado_pronto()
        self._assert_bloqueado(
            state,
            tipo_emprego="desempregado",
            tem_dividas="sim",
        )

    def test_16_fluxo_valido_usa_estado_e_preserva_score_esperado(self):
        state = self._estado_pronto()
        solicitacoes_antes = self._hash(self.solicitacoes_path)

        resultado = self._processar(state)

        self.assertTrue(resultado["processado"])
        self.assertTrue(resultado["perfil_atualizado"])
        self.assertTrue(resultado["retornar_credito"])
        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, state[CREDIT_INTERVIEW_STATUS])
        self.assertTrue(state[CREDIT_INTERVIEW_RETURN_PENDING])
        with self.clientes_path.open(encoding="utf-8", newline="") as handle:
            cliente = next(csv.DictReader(handle))
        self.assertEqual(555, int(cliente["score_credito"]))
        self.assertEqual(solicitacoes_antes, self._hash(self.solicitacoes_path))

    def test_17_segunda_chamada_apos_sucesso_nao_processa_novamente(self):
        state = self._estado_pronto()
        original = score_tools.processar_entrevista_credito_autorizada
        with patch(
            "tools.score_tools.processar_entrevista_credito_autorizada",
            wraps=original,
        ) as processar:
            primeiro = self._processar(state)
            hash_apos_primeiro = self._hash(self.clientes_path)
            segundo = self._processar(state)

        self.assertTrue(primeiro["processado"])
        self.assertFalse(segundo["processado"])
        self.assertEqual(1, processar.call_count)
        self.assertEqual(hash_apos_primeiro, self._hash(self.clientes_path))


if __name__ == "__main__":
    unittest.main()
