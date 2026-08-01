import csv
import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents.credito import agente_credito
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    criar_estado_inicial,
)
from tools.credito_tools import processar_solicitacao


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP = "2026-01-02T03:04:05.123456+00:00"


class FakeToolContext:
    def __init__(self, cpf=CPF_A, *, autenticado=True, encerrado=False):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = autenticado
        self.state[AUTHENTICATED_CPF] = cpf
        self.state[CONVERSATION_ENDED] = encerrado


class CreditRequestProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.clientes_path = base / "clientes.csv"
        self.score_path = base / "score_limite.csv"
        self.solicitacoes_path = base / "solicitacoes.csv"
        self.context = FakeToolContext()

        self._write_clientes()
        self._write_score()
        self._write_solicitacoes([self._solicitacao()])

        patches = [
            patch("tools.credito_tools.CSV_CLIENTES", self.clientes_path),
            patch("tools.credito_tools.CSV_SCORE_LIMITE", self.score_path),
            patch("tools.credito_tools.CSV_SOLICITACOES", self.solicitacoes_path),
        ]
        for item in patches:
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

    def _write_clientes(self, rows=None):
        if rows is None:
            rows = [
                {
                    "cpf": CPF_A,
                    "nome": "Cliente A",
                    "data_nascimento": "01/01/1990",
                    "score_credito": "750",
                    "limite_credito": "1000.00",
                },
                {
                    "cpf": CPF_B,
                    "nome": "Cliente B",
                    "data_nascimento": "02/02/1992",
                    "score_credito": "900",
                    "limite_credito": "2000.00",
                },
            ]
        self._write_csv(
            self.clientes_path,
            ["cpf", "nome", "data_nascimento", "score_credito", "limite_credito"],
            rows,
        )

    def _write_score(self):
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [
                {"limite_maximo": "2000.00", "score_minimo": "500"},
                {"limite_maximo": "5000.00", "score_minimo": "700"},
                {"limite_maximo": "10000.00", "score_minimo": "800"},
            ],
        )

    def _solicitacao(
        self,
        *,
        cpf=CPF_A,
        timestamp=TIMESTAMP,
        limite_atual="1000.00",
        novo_limite="3000.00",
        status="pendente",
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

    def _processar(self, context=None):
        return processar_solicitacao(
            TIMESTAMP,
            tool_context=context or self.context,
        )

    def _assert_bloqueado_sem_escrita(self):
        antes = {
            self.clientes_path: self._hash(self.clientes_path),
            self.score_path: self._hash(self.score_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }

        resultado = self._processar()

        depois = {path: self._hash(path) for path in antes}
        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        self.assertEqual(antes, depois)
        return resultado

    def test_01_aprovacao_atualiza_status_e_limite(self):
        resultado = self._processar()

        solicitacao = self._read_csv(self.solicitacoes_path)[0]
        cliente = self._read_csv(self.clientes_path)[0]
        self.assertTrue(resultado["processado"])
        self.assertEqual("aprovado", resultado["status_pedido"])
        self.assertTrue(resultado["limite_atualizado"])
        self.assertEqual("aprovado", solicitacao["status_pedido"])
        self.assertEqual(3000.0, float(cliente["limite_credito"]))

    def test_02_valor_aplicado_e_exatamente_o_registrado(self):
        self._write_solicitacoes([
            self._solicitacao(novo_limite="4321.75"),
        ])

        resultado = self._processar()

        cliente = self._read_csv(self.clientes_path)[0]
        self.assertEqual(4321.75, resultado["novo_limite"])
        self.assertEqual(4321.75, float(cliente["limite_credito"]))

    def test_03_rejeicao_atualiza_somente_status(self):
        self._write_solicitacoes([
            self._solicitacao(novo_limite="8000.00"),
        ])
        clientes_antes = self._hash(self.clientes_path)

        resultado = self._processar()

        solicitacao = self._read_csv(self.solicitacoes_path)[0]
        self.assertTrue(resultado["processado"])
        self.assertEqual("rejeitado", resultado["status_pedido"])
        self.assertFalse(resultado["limite_atualizado"])
        self.assertIsNone(resultado["novo_limite"])
        self.assertTrue(resultado["oferecer_entrevista"])
        self.assertEqual("rejeitado", solicitacao["status_pedido"])
        self.assertEqual(clientes_antes, self._hash(self.clientes_path))

    def test_04_limite_sem_faixa_resulta_em_rejeicao(self):
        self._write_solicitacoes([
            self._solicitacao(novo_limite="12000.00"),
        ])

        resultado = self._processar()

        self.assertTrue(resultado["processado"])
        self.assertEqual("rejeitado", resultado["status_pedido"])
        self.assertIsNone(resultado["erro"])

    def test_05_solicitacao_pendente_e_obrigatoria(self):
        self._write_solicitacoes([
            self._solicitacao(status="cancelado"),
        ])

        resultado = self._assert_bloqueado_sem_escrita()

        self.assertIn("status", resultado["erro"].lower())

    def test_06_solicitacao_aprovada_com_limite_ja_aplicado_nao_e_reprocessada(self):
        clientes = self._read_csv(self.clientes_path)
        clientes[0]["limite_credito"] = "3000.00"
        self._write_clientes(clientes)
        self._write_solicitacoes([
            self._solicitacao(status="aprovado"),
        ])

        with patch("tools.credito_tools._escrever_csv_atomico") as escrever:
            self._assert_bloqueado_sem_escrita()

        escrever.assert_not_called()

    def test_07_solicitacao_rejeitada_nao_e_reprocessada(self):
        self._write_solicitacoes([
            self._solicitacao(status="rejeitado"),
        ])
        self._assert_bloqueado_sem_escrita()

    def test_08_solicitacao_inexistente_e_bloqueada(self):
        self._write_solicitacoes([
            self._solicitacao(timestamp="outro-timestamp"),
        ])
        self._assert_bloqueado_sem_escrita()

    def test_09_duplicidade_de_solicitacao_e_bloqueada(self):
        solicitacao = self._solicitacao()
        self._write_solicitacoes([solicitacao, solicitacao])
        self._assert_bloqueado_sem_escrita()

    def test_10_outro_cpf_nao_pode_ser_processado(self):
        self._write_solicitacoes([
            self._solicitacao(cpf=CPF_B),
        ])
        self._assert_bloqueado_sem_escrita()

    def test_11_snapshot_divergente_bloqueia_operacao(self):
        self._write_solicitacoes([
            self._solicitacao(limite_atual="999.00"),
        ])
        self._assert_bloqueado_sem_escrita()

    def test_12_valor_registrado_invalido_bloqueia_operacao(self):
        self._write_solicitacoes([
            self._solicitacao(novo_limite="nao-numerico"),
        ])
        self._assert_bloqueado_sem_escrita()

    def test_13_resultado_nao_expoe_dados_internos(self):
        resultado = self._processar()
        self.assertEqual(
            {
                "processado",
                "status_pedido",
                "limite_atualizado",
                "novo_limite",
                "oferecer_entrevista",
                "erro",
            },
            set(resultado),
        )
        chaves_proibidas = {
            "cpf",
            "cpf_cliente",
            "score",
            "score_atual",
            "score_minimo",
            "score_minimo_necessario",
            "faixa",
            "limite_maximo_faixa",
        }

        self.assertTrue(chaves_proibidas.isdisjoint(resultado))
        self.assertNotIn(CPF_A, str(resultado))

    def test_14_assinatura_nao_aceita_argumentos_proibidos(self):
        parametros = set(inspect.signature(processar_solicitacao).parameters)
        proibidos = {"cpf", "novo_limite", "score", "status", "limite_atual"}

        self.assertEqual(
            {"data_hora_solicitacao", "tool_context"},
            parametros,
        )
        self.assertTrue(proibidos.isdisjoint(parametros))

    def test_15_agente_expoe_somente_as_quatro_tools_permitidas(self):
        nomes = [tool.__name__ for tool in agente_credito.tools]

        self.assertEqual(
            [
                "consultar_limite",
                "registrar_solicitacao",
                "processar_solicitacao",
                "encerrar_atendimento",
            ],
            nomes,
        )

    def test_16_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}

        self._processar()

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_17_cliente_duplicado_e_bloqueado(self):
        cliente = self._read_csv(self.clientes_path)[0]
        self._write_clientes([cliente, cliente])
        self._assert_bloqueado_sem_escrita()

    def test_18_cliente_inexistente_e_bloqueado(self):
        cliente_b = self._read_csv(self.clientes_path)[1]
        self._write_clientes([cliente_b])
        self._assert_bloqueado_sem_escrita()

    def test_19_sessao_nao_autenticada_e_bloqueada_antes_de_io(self):
        context = FakeToolContext(autenticado=False)
        with patch("tools.credito_tools.pd.read_csv") as read_csv:
            resultado = self._processar(context)

        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        read_csv.assert_not_called()

    def test_20_sessao_encerrada_e_bloqueada_antes_de_io(self):
        context = FakeToolContext(encerrado=True)
        with patch("tools.credito_tools.pd.read_csv") as read_csv:
            resultado = self._processar(context)

        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        read_csv.assert_not_called()

    def test_21_csv_de_solicitacoes_ausente_e_bloqueado(self):
        self.solicitacoes_path.unlink()
        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = self._processar()

        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        to_csv.assert_not_called()

    def test_22_csv_de_clientes_ausente_e_bloqueado(self):
        self.clientes_path.unlink()
        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = self._processar()

        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        to_csv.assert_not_called()

    def test_23_csv_de_score_ausente_e_bloqueado(self):
        self.score_path.unlink()
        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = self._processar()

        self.assertFalse(resultado["processado"])
        self.assertIsNotNone(resultado["erro"])
        to_csv.assert_not_called()

    def test_24_csv_de_solicitacoes_malformado_e_bloqueado(self):
        self._write_csv(
            self.solicitacoes_path,
            ["coluna_invalida"],
            [{"coluna_invalida": "1"}],
        )
        self._assert_bloqueado_sem_escrita()

    def test_25_csv_de_clientes_malformado_e_bloqueado(self):
        self._write_csv(
            self.clientes_path,
            ["cpf", "limite_credito"],
            [{"cpf": CPF_A, "limite_credito": "1000.00"}],
        )
        self._assert_bloqueado_sem_escrita()

    def test_26_csv_de_score_malformado_e_bloqueado(self):
        self._write_csv(
            self.score_path,
            ["coluna_invalida"],
            [{"coluna_invalida": "1"}],
        )
        self._assert_bloqueado_sem_escrita()

    def test_27_timestamp_invalido_e_bloqueado_antes_de_io(self):
        for timestamp in (None, "", "   ", 123):
            with self.subTest(timestamp=timestamp):
                with patch("tools.credito_tools.pd.read_csv") as read_csv:
                    resultado = processar_solicitacao(
                        timestamp,
                        tool_context=self.context,
                    )

                self.assertFalse(resultado["processado"])
                self.assertIsNotNone(resultado["erro"])
                read_csv.assert_not_called()

    def test_28_erros_nao_contem_cpf(self):
        solicitacao = self._solicitacao()
        self._write_solicitacoes([solicitacao, solicitacao])

        resultado = self._processar()

        self.assertIsNotNone(resultado["erro"])
        self.assertNotIn(CPF_A, resultado["erro"])


if __name__ == "__main__":
    unittest.main()
