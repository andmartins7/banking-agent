import csv
import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    ErroAutorizacaoSessao,
    criar_estado_inicial,
    obter_cpf_autorizado,
)
from tools.credito_tools import (
    atualizar_limite_cliente,
    atualizar_status_solicitacao,
    checar_score_para_limite,
    consultar_limite,
    registrar_solicitacao,
)
from tools.score_tools import calcular_score, atualizar_score_cliente


CPF_A = "11111111111"
CPF_B = "22222222222"
CPF_AUSENTE = "33333333333"
TIMESTAMP_A = "2026-01-01T10:00:00"
TIMESTAMP_B = "2026-01-01T11:00:00"


class FakeToolContext:
    def __init__(self, cpf=None, *, autenticado=False, encerrado=False):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = autenticado
        self.state[AUTHENTICATED_CPF] = cpf
        self.state[CONVERSATION_ENDED] = encerrado


class CreditSessionAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.clientes_path = base / "clientes.csv"
        self.score_path = base / "score_limite.csv"
        self.solicitacoes_path = base / "solicitacoes.csv"

        self._write_csv(
            self.clientes_path,
            ["cpf", "nome", "data_nascimento", "score_credito", "limite_credito"],
            [
                {
                    "cpf": CPF_A,
                    "nome": "Cliente A",
                    "data_nascimento": "01/01/1990",
                    "score_credito": "800",
                    "limite_credito": "1000.00",
                },
                {
                    "cpf": CPF_B,
                    "nome": "Cliente B",
                    "data_nascimento": "02/02/1992",
                    "score_credito": "200",
                    "limite_credito": "9000.00",
                },
            ],
        )
        self._write_csv(
            self.score_path,
            ["limite_maximo", "score_minimo"],
            [
                {"limite_maximo": "2000.00", "score_minimo": "500"},
                {"limite_maximo": "10000.00", "score_minimo": "700"},
            ],
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
            [
                {
                    "cpf_cliente": CPF_A,
                    "data_hora_solicitacao": TIMESTAMP_A,
                    "limite_atual": "1000.00",
                    "novo_limite_solicitado": "2000.00",
                    "status_pedido": "pendente",
                },
                {
                    "cpf_cliente": CPF_B,
                    "data_hora_solicitacao": TIMESTAMP_B,
                    "limite_atual": "9000.00",
                    "novo_limite_solicitado": "10000.00",
                    "status_pedido": "pendente",
                },
            ],
        )

        patches = [
            patch("tools.credito_tools.CSV_CLIENTES", self.clientes_path),
            patch("tools.credito_tools.CSV_SCORE_LIMITE", self.score_path),
            patch("tools.credito_tools.CSV_SOLICITACOES", self.solicitacoes_path),
            patch("tools.score_tools.CSV_CLIENTES", self.clientes_path),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def _write_csv(path, fieldnames, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_csv(path):
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _contexto(self, cpf=CPF_A):
        return FakeToolContext(cpf, autenticado=True)

    def _chamadas_tools(self, context):
        return [
            lambda: consultar_limite(tool_context=context),
            lambda: registrar_solicitacao(3000.0, tool_context=context),
            lambda: checar_score_para_limite(3000.0, tool_context=context),
            lambda: atualizar_status_solicitacao(
                TIMESTAMP_A,
                "aprovado",
                tool_context=context,
            ),
            lambda: atualizar_limite_cliente(3000.0, tool_context=context),
            lambda: calcular_score(
                5000.0,
                "formal",
                1000.0,
                0,
                "nao",
                tool_context=context,
            ),
            lambda: atualizar_score_cliente(700, tool_context=context),
        ]

    def test_todas_tools_rejeitam_sessao_nao_autenticada(self):
        context = FakeToolContext(CPF_A, autenticado=False)

        for chamada in self._chamadas_tools(context):
            with self.subTest(tool=chamada):
                resultado = chamada()
                self.assertIsNotNone(resultado["erro"])

    def test_todas_tools_rejeitam_sessao_encerrada(self):
        context = FakeToolContext(CPF_A, autenticado=True, encerrado=True)

        for chamada in self._chamadas_tools(context):
            with self.subTest(tool=chamada):
                resultado = chamada()
                self.assertIsNotNone(resultado["erro"])

    def test_guard_rejeita_contexto_ausente_invalido_e_cpf_invalido(self):
        contextos = [
            None,
            object(),
            FakeToolContext(None, autenticado=True),
            FakeToolContext("123", autenticado=True),
        ]

        for context in contextos:
            with self.subTest(context_type=type(context).__name__):
                with self.assertRaises(ErroAutorizacaoSessao):
                    obter_cpf_autorizado(context)

    def test_autorizacao_falha_antes_de_qualquer_io(self):
        contextos = [
            FakeToolContext(CPF_A, autenticado=False),
            FakeToolContext(CPF_A, autenticado=True, encerrado=True),
            FakeToolContext("123", autenticado=True),
        ]

        with (
            patch("tools.credito_tools.pd.read_csv") as credito_read,
            patch("tools.score_tools.pd.read_csv") as score_read,
            patch("tools.credito_tools._garantir_csv_solicitacoes") as garantir,
        ):
            for context in contextos:
                for chamada in self._chamadas_tools(context):
                    chamada()

        credito_read.assert_not_called()
        score_read.assert_not_called()
        garantir.assert_not_called()

    def test_consultar_limite_retorna_somente_dados_da_sessao(self):
        resultado_a = consultar_limite(tool_context=self._contexto(CPF_A))
        resultado_b = consultar_limite(tool_context=self._contexto(CPF_B))

        self.assertEqual(1000.0, resultado_a["limite_atual"])
        self.assertEqual(800, resultado_a["score_credito"])
        self.assertEqual(9000.0, resultado_b["limite_atual"])
        self.assertEqual(200, resultado_b["score_credito"])
        self.assertNotIn("cpf", resultado_a)
        self.assertNotIn("cpf", resultado_b)

    def test_registro_deriva_cpf_limite_atual_e_status(self):
        resultado = registrar_solicitacao(
            3500.0,
            tool_context=self._contexto(CPF_A),
        )
        registros = self._read_csv(self.solicitacoes_path)
        criado = registros[-1]

        self.assertIsNone(resultado["erro"])
        self.assertEqual(CPF_A, criado["cpf_cliente"])
        self.assertEqual(1000.0, float(criado["limite_atual"]))
        self.assertEqual("pendente", criado["status_pedido"])
        self.assertEqual("pendente", resultado["status_pedido"])

    def test_analise_utiliza_score_persistido(self):
        resultado_a = checar_score_para_limite(
            5000.0,
            tool_context=self._contexto(CPF_A),
        )
        resultado_b = checar_score_para_limite(
            5000.0,
            tool_context=self._contexto(CPF_B),
        )

        self.assertTrue(resultado_a["aprovado"])
        self.assertFalse(resultado_b["aprovado"])

    def test_assinaturas_nao_expoem_argumentos_proibidos(self):
        contratos = {
            consultar_limite: {"tool_context"},
            registrar_solicitacao: {"novo_limite_solicitado", "tool_context"},
            checar_score_para_limite: {"novo_limite", "tool_context"},
            atualizar_status_solicitacao: {
                "data_hora_solicitacao",
                "novo_status",
                "tool_context",
            },
            atualizar_limite_cliente: {"novo_limite", "tool_context"},
            calcular_score: {
                "renda_mensal",
                "tipo_emprego",
                "despesas_fixas",
                "num_dependentes",
                "tem_dividas",
                "tool_context",
            },
            atualizar_score_cliente: {"novo_score", "tool_context"},
        }

        for tool, esperado in contratos.items():
            with self.subTest(tool=tool.__name__):
                parametros = set(inspect.signature(tool).parameters)
                self.assertEqual(esperado, parametros)
                self.assertNotIn("cpf", parametros)
                self.assertNotIn("score_cliente", parametros)
                self.assertNotIn("limite_atual", parametros)
                self.assertNotIn("status_pedido", parametros)

    def test_atualizacao_status_afeta_somente_sessao(self):
        resultado = atualizar_status_solicitacao(
            TIMESTAMP_A,
            "aprovado",
            tool_context=self._contexto(CPF_A),
        )
        registros = self._read_csv(self.solicitacoes_path)
        por_cpf = {row["cpf_cliente"]: row for row in registros}

        self.assertTrue(resultado["atualizado"])
        self.assertEqual("aprovado", por_cpf[CPF_A]["status_pedido"])
        self.assertEqual("pendente", por_cpf[CPF_B]["status_pedido"])

    def test_atualizacao_limite_afeta_somente_sessao(self):
        resultado = atualizar_limite_cliente(
            4500.0,
            tool_context=self._contexto(CPF_A),
        )
        clientes = {row["cpf"]: row for row in self._read_csv(self.clientes_path)}

        self.assertTrue(resultado["atualizado"])
        self.assertEqual(4500.0, float(clientes[CPF_A]["limite_credito"]))
        self.assertEqual(9000.0, float(clientes[CPF_B]["limite_credito"]))

    def test_atualizacao_score_afeta_somente_sessao(self):
        resultado = atualizar_score_cliente(
            650,
            tool_context=self._contexto(CPF_A),
        )
        clientes = {row["cpf"]: row for row in self._read_csv(self.clientes_path)}

        self.assertTrue(resultado["atualizado"])
        self.assertEqual(650, int(clientes[CPF_A]["score_credito"]))
        self.assertEqual(200, int(clientes[CPF_B]["score_credito"]))

    def test_sessao_a_nao_altera_dados_da_sessao_b(self):
        contexto_a = self._contexto(CPF_A)
        cliente_b_antes = {
            row["cpf"]: row for row in self._read_csv(self.clientes_path)
        }[CPF_B]
        solicitacao_b_antes = {
            row["cpf_cliente"]: row for row in self._read_csv(self.solicitacoes_path)
        }[CPF_B]

        consultar_limite(tool_context=contexto_a)
        registrar_solicitacao(3000.0, tool_context=contexto_a)
        checar_score_para_limite(3000.0, tool_context=contexto_a)
        atualizar_status_solicitacao(
            TIMESTAMP_A,
            "rejeitado",
            tool_context=contexto_a,
        )
        atualizar_limite_cliente(3000.0, tool_context=contexto_a)
        atualizar_score_cliente(700, tool_context=contexto_a)

        cliente_b_depois = {
            row["cpf"]: row for row in self._read_csv(self.clientes_path)
        }[CPF_B]
        solicitacao_b_depois = {
            row["cpf_cliente"]: row for row in self._read_csv(self.solicitacoes_path)
        }[CPF_B]
        self.assertEqual(cliente_b_antes, cliente_b_depois)
        self.assertEqual(solicitacao_b_antes, solicitacao_b_depois)

    def test_erros_nao_expoem_cpf_integral(self):
        context = self._contexto(CPF_AUSENTE)
        resultados = [chamada() for chamada in self._chamadas_tools(context)]

        for resultado in resultados:
            erro = resultado.get("erro")
            if erro:
                self.assertNotIn(CPF_AUSENTE, erro)
                self.assertNotIn(CPF_A, erro)
                self.assertNotIn(CPF_B, erro)

    def test_calcular_score_rejeita_sessao_nao_autenticada(self):
        resultado = calcular_score(
            5000.0,
            "formal",
            1000.0,
            0,
            "nao",
            tool_context=FakeToolContext(),
        )

        self.assertIsNotNone(resultado["erro"])
        self.assertEqual(0, resultado["score"])

    def test_csvs_reais_permanecem_inalterados(self):
        caminhos = [
            config.CSV_CLIENTES,
            config.CSV_SCORE_LIMITE,
            config.CSV_SOLICITACOES,
        ]
        antes = {path: self._hash(path) for path in caminhos}

        registrar_solicitacao(3200.0, tool_context=self._contexto(CPF_A))
        atualizar_limite_cliente(3200.0, tool_context=self._contexto(CPF_A))
        atualizar_score_cliente(720, tool_context=self._contexto(CPF_A))

        depois = {path: self._hash(path) for path in caminhos}
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
