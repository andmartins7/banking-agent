import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import pandas as pd
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CREDIT_INTERVIEW_REQUEST_TIMESTAMP,
    CREDIT_INTERVIEW_RETURN_PENDING,
    criar_estado_inicial,
)
import tools.credito_tools as credito_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
TIMESTAMP_A = "2026-07-01T10:20:30.123456+00:00"
TIMESTAMP_B = "2026-07-01T10:21:30.123456+00:00"


class FakeToolContext:
    def __init__(self, cpf=CPF_A):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = True
        self.state[AUTHENTICATED_CPF] = cpf


class CreditRequestReanalysisTests(unittest.TestCase):
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
            patch(
                "tools.credito_tools.CSV_SOLICITACOES",
                self.solicitacoes_path,
            ),
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

    def _write_clientes(self, score_a="500", limite_a="1000.00"):
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
                    "score_credito": str(score_a),
                    "limite_credito": str(limite_a),
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

    def _atualizar_score(self, cpf, score):
        clientes = self._read_csv(self.clientes_path)
        for cliente in clientes:
            if cliente["cpf"] == cpf:
                cliente["score_credito"] = str(score)
        self._write_csv(
            self.clientes_path,
            [
                "cpf",
                "nome",
                "data_nascimento",
                "score_credito",
                "limite_credito",
            ],
            clientes,
        )

    def _write_score(self):
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [
                {"limite_maximo": "5000.00", "score_minimo": "700"},
                {"limite_maximo": "10000.00", "score_minimo": "850"},
            ],
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

    def _associar(self, context=None, timestamp=TIMESTAMP_A):
        contexto = context or self.context
        contexto.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = timestamp
        return contexto

    def _reanalisar(self, context=None):
        return credito_tools.reanalisar_solicitacao(
            context or self.context
        )

    def _hashes_temporarios(self):
        return {
            self.clientes_path: self._hash(self.clientes_path),
            self.score_path: self._hash(self.score_path),
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
        }

    def test_01_rejeicao_inicial_associa_timestamp_exato_da_linha(self):
        self._write_solicitacoes([
            self._solicitacao(status="pendente"),
        ])

        resultado = credito_tools.processar_solicitacao(
            TIMESTAMP_A,
            self.context,
        )

        linha = self._read_csv(self.solicitacoes_path)[0]
        self.assertTrue(resultado["processado"])
        self.assertEqual("rejeitado", linha["status_pedido"])
        self.assertEqual(
            linha["data_hora_solicitacao"],
            self.context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP],
        )

    def test_02_aprovacao_inicial_nao_associa_entrevista(self):
        self._write_clientes(score_a="800")
        self._write_solicitacoes([
            self._solicitacao(status="pendente"),
        ])

        resultado = credito_tools.processar_solicitacao(
            TIMESTAMP_A,
            self.context,
        )

        self.assertTrue(resultado["processado"])
        self.assertEqual("aprovado", resultado["status_pedido"])
        self.assertIsNone(
            self.context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP]
        )

    def test_03_falha_antes_da_rejeicao_persistida_nao_associa(self):
        self._write_solicitacoes([
            self._solicitacao(status="pendente"),
        ])
        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=OSError("falha injetada"),
        ):
            resultado = credito_tools.processar_solicitacao(
                TIMESTAMP_A,
                self.context,
            )

        self.assertFalse(resultado["processado"])
        self.assertIsNone(
            self.context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP]
        )
        self.assertEqual(
            "pendente",
            self._read_csv(self.solicitacoes_path)[0]["status_pedido"],
        )

    def test_04_reanalise_aprova_mesma_linha_com_score_atualizado(self):
        self._associar()
        self._atualizar_score(CPF_A, 800)
        linhas_antes = self._read_csv(self.solicitacoes_path)
        self.context.state[CREDIT_INTERVIEW_RETURN_PENDING] = True

        resultado = self._reanalisar()

        linhas_depois = self._read_csv(self.solicitacoes_path)
        cliente = self._read_csv(self.clientes_path)[0]
        self.assertTrue(resultado["processado"])
        self.assertEqual("aprovado", resultado["status_pedido"])
        self.assertEqual(3000.0, resultado["novo_limite"])
        self.assertEqual("aprovado", linhas_depois[0]["status_pedido"])
        self.assertEqual(len(linhas_antes), len(linhas_depois))
        self.assertEqual(
            linhas_antes[0]["data_hora_solicitacao"],
            linhas_depois[0]["data_hora_solicitacao"],
        )
        self.assertEqual(3000.0, float(cliente["limite_credito"]))
        self.assertTrue(
            self.context.state[CREDIT_INTERVIEW_RETURN_PENDING]
        )

    def test_05_score_insuficiente_mantem_pedido_limite_e_quantidade(self):
        self._associar()
        antes = self._hashes_temporarios()
        linhas_antes = self._read_csv(self.solicitacoes_path)

        resultado = self._reanalisar()

        linhas_depois = self._read_csv(self.solicitacoes_path)
        self.assertTrue(resultado["processado"])
        self.assertEqual("rejeitado", resultado["status_pedido"])
        self.assertFalse(resultado["limite_atualizado"])
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertEqual(len(linhas_antes), len(linhas_depois))

    def test_06_referencia_ausente_e_bloqueada_sem_escrita(self):
        antes = self._hashes_temporarios()

        resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self._hashes_temporarios())

    def test_07_pedido_ausente_e_bloqueado_sem_escrita(self):
        self._associar(timestamp="timestamp-inexistente")
        antes = self._hashes_temporarios()

        resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self._hashes_temporarios())

    def test_08_duplicata_cpf_timestamp_e_erro_sem_escrita(self):
        linha = self._solicitacao()
        self._write_solicitacoes([linha, linha])
        self._associar()
        antes = self._hashes_temporarios()

        resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertIn("integridade", resultado["erro"].lower())
        self.assertEqual(antes, self._hashes_temporarios())

    def test_09_outro_cpf_nao_pode_reanalisar_pedido(self):
        contexto_b = self._associar(FakeToolContext(CPF_B))
        antes = self._hashes_temporarios()

        resultado = self._reanalisar(contexto_b)

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self._hashes_temporarios())

    def test_10_status_aprovado_nao_pode_ser_reanalisado(self):
        self._write_solicitacoes([
            self._solicitacao(status="aprovado"),
        ])
        self._associar()
        antes = self._hashes_temporarios()

        resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self._hashes_temporarios())

    def test_11_status_pendente_nao_pode_ser_reanalisado(self):
        self._write_solicitacoes([
            self._solicitacao(status="pendente"),
        ])
        self._associar()
        antes = self._hashes_temporarios()

        resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self._hashes_temporarios())

    def test_12_snapshot_divergente_bloqueia_sem_escrita(self):
        self._write_clientes(limite_a="1500.00")
        self._associar()
        antes = self._hashes_temporarios()

        resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertIn("diverge", resultado["erro"].lower())
        self.assertEqual(antes, self._hashes_temporarios())

    def test_13_falha_de_publicacao_restaura_pedido_e_cliente(self):
        self._associar()
        self._atualizar_score(CPF_A, 800)
        antes = self._hashes_temporarios()
        escrever_original = credito_tools._escrever_csv_atomico

        def falhar_no_cliente(dataframe, destino):
            if Path(destino) == self.clientes_path:
                raise OSError("falha injetada no cliente")
            return escrever_original(dataframe, destino)

        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=falhar_no_cliente,
        ):
            resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self._hashes_temporarios())
        self.assertEqual(
            "rejeitado",
            self._read_csv(self.solicitacoes_path)[0]["status_pedido"],
        )

    def test_14_processamento_e_reanalise_usam_politica_compartilhada(self):
        self._write_solicitacoes([
            self._solicitacao(status="pendente"),
        ])
        politica_original = credito_tools._avaliar_politica_credito
        with patch(
            "tools.credito_tools._avaliar_politica_credito",
            wraps=politica_original,
        ) as politica:
            primeira = credito_tools.processar_solicitacao(
                TIMESTAMP_A,
                self.context,
            )
            self._atualizar_score(CPF_A, 800)
            segunda = self._reanalisar()

        self.assertEqual("rejeitado", primeira["status_pedido"])
        self.assertEqual("aprovado", segunda["status_pedido"])
        self.assertEqual(2, politica.call_count)
        self.assertEqual((500, 3000.0), politica.call_args_list[0].args[:2])
        self.assertEqual((800, 3000.0), politica.call_args_list[1].args[:2])

    def test_15_nucleo_de_politica_e_puro(self):
        faixas = pd.DataFrame([
            {"limite_maximo": 5000.0, "score_minimo": 700},
        ])
        with patch("tools.credito_tools.pd.read_csv") as ler, patch(
            "tools.credito_tools._escrever_csv_atomico"
        ) as escrever:
            aprovado = credito_tools._avaliar_politica_credito(
                800,
                3000.0,
                faixas,
            )

        self.assertTrue(aprovado)
        ler.assert_not_called()
        escrever.assert_not_called()

    def test_16_sessoes_e_cpfs_permanecem_isolados(self):
        self._write_solicitacoes([
            self._solicitacao(),
            self._solicitacao(
                cpf=CPF_B,
                timestamp=TIMESTAMP_B,
                limite_atual="2000.00",
                novo_limite="4000.00",
            ),
        ])
        contexto_a = self._associar(FakeToolContext(CPF_A), TIMESTAMP_A)
        contexto_b = self._associar(FakeToolContext(CPF_B), TIMESTAMP_B)
        self._atualizar_score(CPF_A, 800)

        resultado = self._reanalisar(contexto_a)

        linhas = self._read_csv(self.solicitacoes_path)
        clientes = self._read_csv(self.clientes_path)
        self.assertEqual("aprovado", resultado["status_pedido"])
        self.assertEqual("aprovado", linhas[0]["status_pedido"])
        self.assertEqual("rejeitado", linhas[1]["status_pedido"])
        self.assertEqual("2000.00", clientes[1]["limite_credito"])
        self.assertEqual(
            TIMESTAMP_B,
            contexto_b.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP],
        )

    def test_17_estado_inicial_tem_somente_referencia_minima_do_pedido(self):
        estado = criar_estado_inicial()

        self.assertIsNone(estado[CREDIT_INTERVIEW_REQUEST_TIMESTAMP])
        for chave_redundante in (
            "credit_interview_request_cpf",
            "credit_interview_request_new_limit",
            "credit_interview_request_current_limit",
            "credit_interview_request_score",
            "credit_interview_request_status",
        ):
            self.assertNotIn(chave_redundante, estado)

    def test_18_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}
        self._associar()
        self._atualizar_score(CPF_A, 800)

        self._reanalisar()

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_19_falhas_de_localizacao_inicial_nao_associam_entrevista(self):
        cenarios = {
            "ausente": [
                self._solicitacao(timestamp=TIMESTAMP_B, status="pendente"),
            ],
            "duplicado": [
                self._solicitacao(status="pendente"),
                self._solicitacao(status="pendente"),
            ],
            "outro_cpf": [
                self._solicitacao(cpf=CPF_B, status="pendente"),
            ],
        }

        for nome, linhas in cenarios.items():
            with self.subTest(nome=nome):
                self._write_solicitacoes(linhas)
                self.context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP] = None

                resultado = credito_tools.processar_solicitacao(
                    TIMESTAMP_A,
                    self.context,
                )

                self.assertFalse(resultado["processado"])
                self.assertIsNone(
                    self.context.state[CREDIT_INTERVIEW_REQUEST_TIMESTAMP]
                )

    def test_20_reanalise_exige_sessao_autenticada_antes_de_ler_csv(self):
        self._associar()
        self.context.state[AUTHENTICATED] = False
        antes = self._hashes_temporarios()

        with patch("tools.credito_tools.pd.read_csv") as ler:
            resultado = self._reanalisar()

        self.assertFalse(resultado["processado"])
        self.assertIn("autenticação", resultado["erro"].lower())
        ler.assert_not_called()
        self.assertEqual(antes, self._hashes_temporarios())


if __name__ == "__main__":
    unittest.main()
