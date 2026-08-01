import csv
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import config
from session_state import AUTHENTICATED, AUTHENTICATED_CPF, criar_estado_inicial
from tools.credito_tools import (
    atualizar_status_solicitacao,
    checar_score_para_limite,
    registrar_solicitacao,
)


CPF_CLIENTE = "11111111111"
LIMITE_ATUAL = 1000.0
TIMESTAMP = "2026-01-02T03:04:05.123456+00:00"


class FakeToolContext:
    def __init__(self):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = True
        self.state[AUTHENTICATED_CPF] = CPF_CLIENTE


class CreditRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.clientes_path = base / "clientes.csv"
        self.score_path = base / "score_limite.csv"
        self.solicitacoes_path = base / "solicitacoes.csv"
        self.context = FakeToolContext()
        self.instante = datetime(
            2026,
            1,
            2,
            3,
            4,
            5,
            123456,
            tzinfo=timezone.utc,
        )

        self._write_csv(
            self.clientes_path,
            ["cpf", "nome", "data_nascimento", "score_credito", "limite_credito"],
            [{
                "cpf": CPF_CLIENTE,
                "nome": "Cliente Fictício",
                "data_nascimento": "01/01/1990",
                "score_credito": "700",
                "limite_credito": str(LIMITE_ATUAL),
            }],
        )
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [
                {"limite_maximo": "2000.00", "score_minimo": "500"},
                {"limite_maximo": "5000.00", "score_minimo": "700"},
                {"limite_maximo": "10000.00", "score_minimo": "800"},
            ],
        )

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

    def _row_solicitacao(self, status="pendente", timestamp=TIMESTAMP):
        return {
            "cpf_cliente": CPF_CLIENTE,
            "data_hora_solicitacao": timestamp,
            "limite_atual": str(LIMITE_ATUAL),
            "novo_limite_solicitado": "2500.0",
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

    def _registrar(self, valor):
        with patch("tools.credito_tools._agora_utc", return_value=self.instante):
            return registrar_solicitacao(valor, tool_context=self.context)

    def _assert_registro_invalido(self, valor):
        resultado = registrar_solicitacao(valor, tool_context=self.context)
        self.assertFalse(resultado["registrado"])
        self.assertIsNotNone(resultado["erro"])

    def test_01_limite_valido_maior_que_atual_e_registrado(self):
        resultado = self._registrar(2500.0)
        registros = self._read_csv(self.solicitacoes_path)

        self.assertTrue(resultado["registrado"])
        self.assertEqual(1, len(registros))
        self.assertEqual("2500.0", registros[0]["novo_limite_solicitado"])
        self.assertEqual("pendente", registros[0]["status_pedido"])

    def test_02_limite_igual_ao_atual_e_rejeitado(self):
        self._assert_registro_invalido(LIMITE_ATUAL)

    def test_03_limite_inferior_ao_atual_e_rejeitado(self):
        self._assert_registro_invalido(999.99)

    def test_04_zero_e_rejeitado(self):
        self._assert_registro_invalido(0)

    def test_05_valor_negativo_e_rejeitado(self):
        self._assert_registro_invalido(-1)

    def test_06_booleano_e_rejeitado(self):
        for valor in (True, False):
            with self.subTest(valor=valor):
                self._assert_registro_invalido(valor)

    def test_07_none_e_texto_nao_numerico_sao_rejeitados(self):
        for valor in (None, "não numérico"):
            with self.subTest(valor=valor):
                self._assert_registro_invalido(valor)

    def test_08_nan_e_rejeitado(self):
        self._assert_registro_invalido(float("nan"))

    def test_09_infinitos_sao_rejeitados(self):
        for valor in (float("inf"), float("-inf")):
            with self.subTest(valor=valor):
                self._assert_registro_invalido(valor)

    def test_10_valor_invalido_nao_cria_csv_de_solicitacoes(self):
        with patch("tools.credito_tools._garantir_csv_solicitacoes") as garantir:
            self._assert_registro_invalido(0)

        garantir.assert_not_called()
        self.assertFalse(self.solicitacoes_path.exists())

    def test_11_valor_invalido_nao_modifica_csv_existente(self):
        self._write_solicitacoes([self._row_solicitacao()])
        antes = self._hash(self.solicitacoes_path)

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            self._assert_registro_invalido(LIMITE_ATUAL)

        to_csv.assert_not_called()
        self.assertEqual(antes, self._hash(self.solicitacoes_path))

    def test_12_timestamp_e_iso_8601(self):
        resultado = self._registrar(2500.0)

        self.assertEqual(self.instante, datetime.fromisoformat(resultado["data_hora"]))

    def test_13_timestamp_contem_timezone_utc(self):
        resultado = self._registrar(2500.0)
        instante = datetime.fromisoformat(resultado["data_hora"])

        self.assertEqual(timedelta(0), instante.utcoffset())
        self.assertTrue(resultado["data_hora"].endswith("+00:00"))

    def test_14_timestamp_contem_microssegundos(self):
        resultado = self._registrar(2500.0)

        self.assertEqual(TIMESTAMP, resultado["data_hora"])

    def test_15_valor_dentro_da_faixa_usa_faixa_correta(self):
        resultado = checar_score_para_limite(3500.0, tool_context=self.context)

        self.assertTrue(resultado["limite_coberto"])
        self.assertEqual(5000.0, resultado["limite_maximo_faixa"])
        self.assertEqual(700, resultado["score_minimo_necessario"])

    def test_16_valor_acima_da_maior_faixa_e_rejeitado(self):
        resultado = checar_score_para_limite(12000.0, tool_context=self.context)

        self.assertFalse(resultado["aprovado"])
        self.assertFalse(resultado["limite_coberto"])
        self.assertIsNone(resultado["score_minimo_necessario"])
        self.assertIsNone(resultado["erro"])

    def test_17_score_insuficiente_rejeita(self):
        resultado = checar_score_para_limite(8000.0, tool_context=self.context)

        self.assertFalse(resultado["aprovado"])
        self.assertTrue(resultado["limite_coberto"])
        self.assertEqual(800, resultado["score_minimo_necessario"])
        self.assertIsNone(resultado["erro"])

    def test_18_score_suficiente_aprova(self):
        resultado = checar_score_para_limite(4500.0, tool_context=self.context)

        self.assertTrue(resultado["aprovado"])
        self.assertTrue(resultado["limite_coberto"])
        self.assertNotIn("score_cliente", resultado)

    def test_19_status_pendente_transita_para_aprovado(self):
        self._write_solicitacoes([self._row_solicitacao()])

        resultado = atualizar_status_solicitacao(
            TIMESTAMP, "aprovado", tool_context=self.context
        )

        self.assertTrue(resultado["atualizado"])
        self.assertEqual("pendente", resultado["status_anterior"])
        self.assertEqual("aprovado", self._read_csv(self.solicitacoes_path)[0]["status_pedido"])

    def test_20_status_pendente_transita_para_rejeitado(self):
        self._write_solicitacoes([self._row_solicitacao()])

        resultado = atualizar_status_solicitacao(
            TIMESTAMP, "rejeitado", tool_context=self.context
        )

        self.assertTrue(resultado["atualizado"])
        self.assertEqual("rejeitado", self._read_csv(self.solicitacoes_path)[0]["status_pedido"])

    def test_21_reprovado_persiste_como_rejeitado(self):
        self._write_solicitacoes([self._row_solicitacao()])

        resultado = atualizar_status_solicitacao(
            TIMESTAMP, "reprovado", tool_context=self.context
        )

        self.assertTrue(resultado["atualizado"])
        self.assertEqual("rejeitado", resultado["status_novo"])
        self.assertEqual("rejeitado", self._read_csv(self.solicitacoes_path)[0]["status_pedido"])

    def test_22_status_arbitrario_e_rejeitado_sem_escrita(self):
        self._write_solicitacoes([self._row_solicitacao()])
        antes = self._hash(self.solicitacoes_path)

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = atualizar_status_solicitacao(
                TIMESTAMP, "cancelado", tool_context=self.context
            )

        self.assertFalse(resultado["atualizado"])
        to_csv.assert_not_called()
        self.assertEqual(antes, self._hash(self.solicitacoes_path))

    def test_23_atualizacao_para_pendente_e_rejeitada(self):
        self._write_solicitacoes([self._row_solicitacao()])

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = atualizar_status_solicitacao(
                TIMESTAMP, "pendente", tool_context=self.context
            )

        self.assertFalse(resultado["atualizado"])
        to_csv.assert_not_called()

    def test_24_solicitacao_aprovada_nao_pode_ser_reprocessada(self):
        self._write_solicitacoes([self._row_solicitacao(status="aprovado")])

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = atualizar_status_solicitacao(
                TIMESTAMP, "rejeitado", tool_context=self.context
            )

        self.assertFalse(resultado["atualizado"])
        self.assertEqual("aprovado", resultado["status_anterior"])
        to_csv.assert_not_called()

    def test_25_solicitacao_rejeitada_nao_pode_ser_reprocessada(self):
        self._write_solicitacoes([self._row_solicitacao(status="rejeitado")])

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = atualizar_status_solicitacao(
                TIMESTAMP, "aprovado", tool_context=self.context
            )

        self.assertFalse(resultado["atualizado"])
        self.assertEqual("rejeitado", resultado["status_anterior"])
        to_csv.assert_not_called()

    def test_26_timestamp_inexistente_nao_altera_arquivo(self):
        self._write_solicitacoes([self._row_solicitacao()])
        antes = self._hash(self.solicitacoes_path)

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = atualizar_status_solicitacao(
                "outro-timestamp", "aprovado", tool_context=self.context
            )

        self.assertFalse(resultado["atualizado"])
        to_csv.assert_not_called()
        self.assertEqual(antes, self._hash(self.solicitacoes_path))

    def test_27_duplicidade_de_cpf_e_timestamp_impede_escrita(self):
        self._write_solicitacoes([
            self._row_solicitacao(),
            self._row_solicitacao(),
        ])

        with patch("tools.credito_tools.pd.DataFrame.to_csv") as to_csv:
            resultado = atualizar_status_solicitacao(
                TIMESTAMP, "aprovado", tool_context=self.context
            )

        self.assertFalse(resultado["atualizado"])
        self.assertIn("integridade", resultado["erro"].lower())
        to_csv.assert_not_called()

    def test_28_erros_nao_contem_cpf(self):
        self._write_solicitacoes([self._row_solicitacao()])
        resultados = [
            registrar_solicitacao(0, tool_context=self.context),
            checar_score_para_limite(0, tool_context=self.context),
            atualizar_status_solicitacao(
                "inexistente", "aprovado", tool_context=self.context
            ),
        ]

        for resultado in resultados:
            with self.subTest(resultado=resultado):
                self.assertIsNotNone(resultado["erro"])
                self.assertNotIn(CPF_CLIENTE, resultado["erro"])

    def test_29_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}

        self._registrar(2500.0)
        checar_score_para_limite(2500.0, tool_context=self.context)
        self._write_solicitacoes([self._row_solicitacao()])
        atualizar_status_solicitacao(
            TIMESTAMP, "aprovado", tool_context=self.context
        )

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_30_limite_sem_faixa_finaliza_como_rejeitado(self):
        registro = self._registrar(12000.0)
        antes = self._read_csv(self.solicitacoes_path)

        self.assertTrue(registro["registrado"])
        self.assertEqual("pendente", antes[0]["status_pedido"])

        analise = checar_score_para_limite(
            12000.0,
            tool_context=self.context,
        )

        self.assertFalse(analise["aprovado"])
        self.assertFalse(analise["limite_coberto"])
        self.assertIsNone(analise["erro"])

        atualizacao = atualizar_status_solicitacao(
            registro["data_hora"],
            "rejeitado",
            tool_context=self.context,
        )

        depois = self._read_csv(self.solicitacoes_path)
        self.assertTrue(atualizacao["atualizado"])
        self.assertEqual("rejeitado", depois[0]["status_pedido"])

    def test_31_tabela_de_score_ausente_continua_erro_tecnico(self):
        self.score_path.unlink()

        resultado = checar_score_para_limite(
            2500.0,
            tool_context=self.context,
        )

        self.assertFalse(resultado["aprovado"])
        self.assertIsNotNone(resultado["erro"])

    def test_32_tabela_de_score_malformada_continua_erro_tecnico(self):
        self._write_csv(
            self.score_path,
            ["coluna_invalida"],
            [{"coluna_invalida": "1"}],
        )

        resultado = checar_score_para_limite(
            2500.0,
            tool_context=self.context,
        )

        self.assertFalse(resultado["aprovado"])
        self.assertIsNotNone(resultado["erro"])


if __name__ == "__main__":
    unittest.main()
