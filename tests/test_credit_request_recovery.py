import csv
import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from google.adk.tools import FunctionTool
from session_state import AUTHENTICATED, AUTHENTICATED_CPF, criar_estado_inicial
import tools.credito_tools as credito_tools


CPF_CLIENTE = "11111111111"
TIMESTAMP = "2026-01-02T03:04:05.123456+00:00"


class FakeToolContext:
    def __init__(self):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = True
        self.state[AUTHENTICATED_CPF] = CPF_CLIENTE


class CreditRequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.clientes_path = self.base / "clientes.csv"
        self.score_path = self.base / "score_limite.csv"
        self.solicitacoes_path = self.base / "solicitacoes.csv"
        self.context = FakeToolContext()

        self._write_clientes("1000.00")
        self._write_score()
        self._write_solicitacao()

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

    def _write_clientes(self, limite):
        self._write_csv(
            self.clientes_path,
            ["cpf", "nome", "data_nascimento", "score_credito", "limite_credito"],
            [{
                "cpf": CPF_CLIENTE,
                "nome": "Cliente Fictício",
                "data_nascimento": "01/01/1990",
                "score_credito": "750",
                "limite_credito": str(limite),
            }],
        )

    def _write_score(self):
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [
                {"limite_maximo": "5000.00", "score_minimo": "700"},
                {"limite_maximo": "10000.00", "score_minimo": "800"},
            ],
        )

    def _write_solicitacao(
        self,
        *,
        status="pendente",
        limite_atual="1000.00",
        novo_limite="3000.00",
    ):
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
                "cpf_cliente": CPF_CLIENTE,
                "data_hora_solicitacao": TIMESTAMP,
                "limite_atual": str(limite_atual),
                "novo_limite_solicitado": str(novo_limite),
                "status_pedido": status,
            }],
        )

    def _processar(self):
        return credito_tools.processar_solicitacao(
            TIMESTAMP,
            tool_context=self.context,
        )

    def _temporarios(self):
        return list(self.base.rglob("*.tmp"))

    def _falhar_publicacao_cliente(self):
        original = credito_tools._escrever_csv_atomico

        def publicar(dataframe, destino):
            if Path(destino) == self.clientes_path:
                raise OSError("falha injetada na publicação do cliente")
            return original(dataframe, destino)

        return publicar

    def test_01_aprovacao_normal_publica_os_dois_arquivos(self):
        resultado = self._processar()

        solicitacao = self._read_csv(self.solicitacoes_path)[0]
        cliente = self._read_csv(self.clientes_path)[0]
        self.assertTrue(resultado["processado"])
        self.assertEqual("aprovado", solicitacao["status_pedido"])
        self.assertEqual(3000.0, float(cliente["limite_credito"]))
        self.assertEqual([], self._temporarios())

    def test_02_rejeicao_publica_somente_solicitacoes(self):
        self._write_solicitacao(novo_limite="8000.00")
        clientes_antes = self._hash(self.clientes_path)
        original = credito_tools._escrever_csv_atomico
        destinos = []

        def registrar_destino(dataframe, destino):
            destinos.append(Path(destino))
            return original(dataframe, destino)

        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=registrar_destino,
        ):
            resultado = self._processar()

        self.assertTrue(resultado["processado"])
        self.assertEqual("rejeitado", resultado["status_pedido"])
        self.assertEqual([self.solicitacoes_path], destinos)
        self.assertEqual(clientes_antes, self._hash(self.clientes_path))

    def test_03_falha_na_publicacao_da_solicitacao_nao_altera_cliente(self):
        antes = {
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
            self.clientes_path: self._hash(self.clientes_path),
        }

        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=OSError("falha injetada na solicitação"),
        ):
            resultado = self._processar()

        depois = {path: self._hash(path) for path in antes}
        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, depois)

    def test_04_falha_na_publicacao_do_cliente_aciona_rollback(self):
        restaurar_original = credito_tools._restaurar_bytes_atomico
        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=self._falhar_publicacao_cliente(),
        ), patch(
            "tools.credito_tools._restaurar_bytes_atomico",
            wraps=restaurar_original,
        ) as restaurar:
            resultado = self._processar()

        self.assertFalse(resultado["processado"])
        restaurar.assert_called_once()
        self.assertEqual(self.solicitacoes_path, Path(restaurar.call_args.args[1]))

    def test_05_rollback_bem_sucedido_restaura_os_dois_estados(self):
        antes = {
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
            self.clientes_path: self._hash(self.clientes_path),
        }

        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=self._falhar_publicacao_cliente(),
        ):
            resultado = self._processar()

        depois = {path: self._hash(path) for path in antes}
        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, depois)
        self.assertEqual([], self._temporarios())

    def test_06_falha_do_rollback_deixa_estado_recuperavel(self):
        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=self._falhar_publicacao_cliente(),
        ), patch(
            "tools.credito_tools._restaurar_bytes_atomico",
            side_effect=OSError("falha injetada no rollback"),
        ):
            resultado = self._processar()

        solicitacao = self._read_csv(self.solicitacoes_path)[0]
        cliente = self._read_csv(self.clientes_path)[0]
        self.assertFalse(resultado["processado"])
        self.assertEqual("aprovado", solicitacao["status_pedido"])
        self.assertEqual(1000.0, float(cliente["limite_credito"]))
        self.assertEqual([], self._temporarios())

    def test_07_nova_chamada_recupera_aprovado_com_limite_snapshot(self):
        with patch(
            "tools.credito_tools._escrever_csv_atomico",
            side_effect=self._falhar_publicacao_cliente(),
        ), patch(
            "tools.credito_tools._restaurar_bytes_atomico",
            side_effect=OSError("falha injetada no rollback"),
        ):
            primeira = self._processar()

        segunda = self._processar()

        cliente = self._read_csv(self.clientes_path)[0]
        self.assertFalse(primeira["processado"])
        self.assertTrue(segunda["processado"])
        self.assertEqual("aprovado", segunda["status_pedido"])
        self.assertEqual(3000.0, float(cliente["limite_credito"]))

    def test_08_recuperacao_aplica_exatamente_o_valor_registrado(self):
        self._write_solicitacao(status="aprovado", novo_limite="4321.75")

        resultado = self._processar()

        cliente = self._read_csv(self.clientes_path)[0]
        self.assertTrue(resultado["processado"])
        self.assertEqual(4321.75, resultado["novo_limite"])
        self.assertEqual(4321.75, float(cliente["limite_credito"]))

    def test_09_limite_divergente_bloqueia_recuperacao_sem_escrita(self):
        self._write_solicitacao(status="aprovado")
        self._write_clientes("1500.00")
        antes = {
            self.solicitacoes_path: self._hash(self.solicitacoes_path),
            self.clientes_path: self._hash(self.clientes_path),
        }

        with patch("tools.credito_tools._escrever_csv_atomico") as escrever:
            resultado = self._processar()

        depois = {path: self._hash(path) for path in antes}
        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, depois)
        escrever.assert_not_called()

    def test_10_temporarios_sao_removidos_apos_sucesso_e_falha(self):
        destino = self.base / "publicacao.csv"
        destino.write_text("original\n", encoding="utf-8")
        dataframe = credito_tools.pd.DataFrame([{"valor": "novo"}])

        credito_tools._escrever_csv_atomico(dataframe, destino)
        self.assertEqual([], self._temporarios())

        original = destino.read_bytes()
        with patch("tools.credito_tools.os.replace", side_effect=OSError):
            with self.assertRaises(OSError):
                credito_tools._escrever_csv_atomico(dataframe, destino)

        self.assertEqual(original, destino.read_bytes())
        self.assertEqual([], self._temporarios())

    def test_11_falha_na_preparacao_nao_altera_destino_nem_deixa_temporario(self):
        destino = self.base / "preparacao.csv"
        destino.write_text("original\n", encoding="utf-8")
        original = destino.read_bytes()
        dataframe = credito_tools.pd.DataFrame([{"valor": "novo"}])

        with patch.object(dataframe, "to_csv", side_effect=ValueError):
            with self.assertRaises(ValueError):
                credito_tools._escrever_csv_atomico(dataframe, destino)

        self.assertEqual(original, destino.read_bytes())
        self.assertEqual([], self._temporarios())

    def test_12_erros_nao_contem_cpf(self):
        self._write_solicitacao(status="aprovado")
        self._write_clientes("1500.00")

        resultado = self._processar()

        self.assertIsNotNone(resultado["erro"])
        self.assertNotIn(CPF_CLIENTE, resultado["erro"])

    def test_13_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}

        self._processar()

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)

    def test_14_contrato_publico_e_schema_adk_permanecem_inalterados(self):
        assinatura = inspect.signature(credito_tools.processar_solicitacao)
        declaracao = FunctionTool(
            credito_tools.processar_solicitacao
        )._get_declaration().model_dump(exclude_none=True)
        propriedades = declaracao["parameters"]["properties"]

        self.assertEqual(
            {"data_hora_solicitacao", "tool_context"},
            set(assinatura.parameters),
        )
        self.assertEqual({"data_hora_solicitacao"}, set(propriedades))
        self.assertNotIn("tool_context", propriedades)


if __name__ == "__main__":
    unittest.main()
