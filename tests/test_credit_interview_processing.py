import csv
import hashlib
import inspect
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents.entrevista_credito import agente_entrevista_credito
from google.adk.tools import FunctionTool
from session_state import (
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    criar_estado_inicial,
)
import tools.score_tools as score_tools


CPF_A = "11111111111"
CPF_B = "22222222222"
CPF_AUSENTE = "33333333333"


class FakeToolContext:
    def __init__(self, cpf=CPF_A, *, autenticado=True, encerrado=False):
        self.state = criar_estado_inicial()
        self.state[AUTHENTICATED] = autenticado
        self.state[AUTHENTICATED_CPF] = cpf
        self.state[CONVERSATION_ENDED] = encerrado


class CreditInterviewProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.clientes_path = self.base / "clientes.csv"
        self.context = FakeToolContext()
        self._write_clientes()

        csv_patch = patch("tools.score_tools.CSV_CLIENTES", self.clientes_path)
        csv_patch.start()
        self.addCleanup(csv_patch.stop)

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

    def _write_clientes(self, rows=None, fieldnames=None):
        if fieldnames is None:
            fieldnames = [
                "cpf",
                "nome",
                "data_nascimento",
                "score_credito",
                "limite_credito",
            ]
        if rows is None:
            rows = [
                {
                    "cpf": CPF_A,
                    "nome": "Cliente A",
                    "data_nascimento": "01/01/1990",
                    "score_credito": "600",
                    "limite_credito": "1000.00",
                },
                {
                    "cpf": CPF_B,
                    "nome": "Cliente B",
                    "data_nascimento": "02/02/1992",
                    "score_credito": "700",
                    "limite_credito": "2000.00",
                },
            ]
        self._write_csv(self.clientes_path, fieldnames, rows)

    def _processar(self, **overrides):
        argumentos = {
            "renda_mensal": 5000.0,
            "tipo_emprego": "formal",
            "despesas_fixas": 2000.0,
            "num_dependentes": 1,
            "tem_dividas": "nao",
            "tool_context": self.context,
        }
        argumentos.update(overrides)
        return score_tools.processar_entrevista_credito(**argumentos)

    def _score_cliente(self, cpf=CPF_A):
        clientes = {row["cpf"]: row for row in self._read_csv(self.clientes_path)}
        return int(clientes[cpf]["score_credito"])

    def _temporarios(self):
        return list(self.base.rglob("*.tmp"))

    def _assert_bloqueado_sem_escrita(self, **overrides):
        antes = self._hash(self.clientes_path)
        with patch("tools.score_tools._escrever_clientes_atomico") as escrever:
            resultado = self._processar(**overrides)
        self.assertFalse(resultado["processado"])
        self.assertFalse(resultado["perfil_atualizado"])
        self.assertFalse(resultado["retornar_credito"])
        self.assertIsNotNone(resultado["erro"])
        escrever.assert_not_called()
        self.assertEqual(antes, self._hash(self.clientes_path))
        return resultado

    def test_01_exemplo_favoravel_persiste_555(self):
        resultado = self._processar()

        self.assertTrue(resultado["processado"])
        self.assertEqual(555, self._score_cliente())

    def test_02_exemplo_desfavoravel_persiste_167(self):
        resultado = self._processar(
            renda_mensal=1500.0,
            tipo_emprego="autônomo",
            despesas_fixas=1200.0,
            num_dependentes=3,
            tem_dividas="sim",
        )

        self.assertTrue(resultado["processado"])
        self.assertEqual(167, self._score_cliente())

    def test_03_desempregado_sem_dividas_persiste_200(self):
        resultado = self._processar(
            renda_mensal=0.0,
            tipo_emprego="desempregado",
            despesas_fixas=500.0,
            num_dependentes=0,
            tem_dividas="não",
        )

        self.assertTrue(resultado["processado"])
        self.assertEqual(200, self._score_cliente())

    def test_04_score_e_limitado_a_1000(self):
        resultado = self._processar(
            renda_mensal=1_000_000.0,
            despesas_fixas=0.0,
            num_dependentes=0,
        )

        self.assertTrue(resultado["processado"])
        self.assertEqual(1000, self._score_cliente())

    def test_05_score_e_limitado_a_0(self):
        resultado = self._processar(
            renda_mensal=0.0,
            tipo_emprego="sem emprego",
            despesas_fixas=0.0,
            num_dependentes=3,
            tem_dividas="sim",
        )

        self.assertTrue(resultado["processado"])
        self.assertEqual(0, self._score_cliente())

    def test_06_emprego_formal_e_aliases(self):
        for alias in [
            "formal",
            "CLT",
            "empregado",
            "registrado",
            "carteira assinada",
        ]:
            with self.subTest(alias=alias):
                resultado = self._processar(tipo_emprego=alias)
                self.assertTrue(resultado["processado"])
                self.assertEqual(555, self._score_cliente())

    def test_07_emprego_autonomo_e_aliases(self):
        for alias in ["autonomo", "autônomo", "MEI", "freelancer"]:
            with self.subTest(alias=alias):
                resultado = self._processar(tipo_emprego=alias)
                self.assertTrue(resultado["processado"])
                self.assertEqual(455, self._score_cliente())

    def test_08_desempregado_e_aliases(self):
        for alias in ["desempregado", "sem emprego"]:
            with self.subTest(alias=alias):
                resultado = self._processar(tipo_emprego=alias)
                self.assertTrue(resultado["processado"])
                self.assertEqual(255, self._score_cliente())

    def test_09_emprego_desconhecido_e_rejeitado_sem_escrita(self):
        resultado = self._assert_bloqueado_sem_escrita(
            tipo_emprego="efetivado",
        )
        self.assertEqual("tipo_emprego", resultado["campo_invalido"])

    def test_10_dividas_positivas_sao_normalizadas(self):
        for alias in ["sim", "s", "tenho", "possuo", "yes"]:
            with self.subTest(alias=alias):
                resultado = self._processar(tem_dividas=alias)
                self.assertTrue(resultado["processado"])
                self.assertEqual(355, self._score_cliente())

    def test_11_dividas_negativas_sao_normalizadas(self):
        for alias in ["não", "nao", "n", "não tenho", "nao tenho", "no"]:
            with self.subTest(alias=alias):
                resultado = self._processar(tem_dividas=alias)
                self.assertTrue(resultado["processado"])
                self.assertEqual(555, self._score_cliente())

    def test_12_divida_desconhecida_e_rejeitada_sem_escrita(self):
        resultado = self._assert_bloqueado_sem_escrita(tem_dividas="talvez")
        self.assertEqual("tem_dividas", resultado["campo_invalido"])

    def test_13_renda_invalida_e_bloqueada(self):
        for valor in [None, "mil reais", -1.0]:
            with self.subTest(valor=valor):
                resultado = self._assert_bloqueado_sem_escrita(
                    renda_mensal=valor,
                )
                self.assertEqual("renda_mensal", resultado["campo_invalido"])

    def test_14_despesas_invalidas_sao_bloqueadas(self):
        for valor in [None, "quinhentos", -0.01]:
            with self.subTest(valor=valor):
                resultado = self._assert_bloqueado_sem_escrita(
                    despesas_fixas=valor,
                )
                self.assertEqual("despesas_fixas", resultado["campo_invalido"])

    def test_15_dependentes_invalidos_sao_bloqueados(self):
        for valor in [None, "dois", -1, 1.5]:
            with self.subTest(valor=valor):
                resultado = self._assert_bloqueado_sem_escrita(
                    num_dependentes=valor,
                )
                self.assertEqual("num_dependentes", resultado["campo_invalido"])

    def test_16_booleanos_nao_sao_aceitos_como_numeros(self):
        casos = [
            {"renda_mensal": True},
            {"despesas_fixas": False},
            {"num_dependentes": True},
        ]
        for caso in casos:
            with self.subTest(caso=caso):
                self._assert_bloqueado_sem_escrita(**caso)

    def test_17_nan_e_infinitos_sao_bloqueados(self):
        casos = [
            {"renda_mensal": math.nan},
            {"renda_mensal": math.inf},
            {"despesas_fixas": -math.inf},
            {"num_dependentes": math.nan},
        ]
        for caso in casos:
            with self.subTest(caso=caso):
                self._assert_bloqueado_sem_escrita(**caso)

    def test_17_textos_numericos_validos_sao_aceitos(self):
        resultado = self._processar(
            renda_mensal="5000",
            despesas_fixas="2000",
            num_dependentes="1",
        )

        self.assertTrue(resultado["processado"])
        self.assertEqual(555, self._score_cliente())

    def test_18_somente_cliente_autenticado_e_atualizado(self):
        cliente_b_antes = self._score_cliente(CPF_B)

        resultado = self._processar()

        self.assertTrue(resultado["processado"])
        self.assertEqual(555, self._score_cliente(CPF_A))
        self.assertEqual(cliente_b_antes, self._score_cliente(CPF_B))

    def test_19_cliente_ausente_e_bloqueado(self):
        resultado = self._processar(
            tool_context=FakeToolContext(CPF_AUSENTE),
        )

        self.assertFalse(resultado["processado"])
        self.assertIn("não encontrado", resultado["erro"])

    def test_20_cliente_duplicado_e_bloqueado(self):
        rows = self._read_csv(self.clientes_path)
        rows.append(dict(rows[0]))
        self._write_clientes(rows=rows)
        antes = self._hash(self.clientes_path)

        resultado = self._processar()

        self.assertFalse(resultado["processado"])
        self.assertIn("duplicado", resultado["erro"])
        self.assertEqual(antes, self._hash(self.clientes_path))

    def test_21_csv_malformado_e_score_anterior_invalido_sao_bloqueados(self):
        casos = [
            (
                ["cpf", "nome"],
                [{"cpf": CPF_A, "nome": "Cliente A"}],
            ),
            (
                ["cpf", "score_credito"],
                [{"cpf": CPF_A, "score_credito": "inválido"}],
            ),
        ]
        for fieldnames, rows in casos:
            with self.subTest(fieldnames=fieldnames, rows=rows):
                self._write_clientes(rows=rows, fieldnames=fieldnames)
                antes = self._hash(self.clientes_path)
                resultado = self._processar()
                self.assertFalse(resultado["processado"])
                self.assertEqual(antes, self._hash(self.clientes_path))

    def test_22_falha_na_publicacao_preserva_bytes_originais(self):
        antes = self.clientes_path.read_bytes()

        with patch("tools.score_tools.os.replace", side_effect=OSError):
            resultado = self._processar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self.clientes_path.read_bytes())
        self.assertEqual([], self._temporarios())

    def test_23_nenhum_temporario_residual_em_sucesso_ou_falha(self):
        sucesso = self._processar()
        self.assertTrue(sucesso["processado"])
        self.assertEqual([], self._temporarios())

        with patch("tools.score_tools.os.replace", side_effect=OSError):
            falha = self._processar(renda_mensal=4000.0)
        self.assertFalse(falha["processado"])
        self.assertEqual([], self._temporarios())

    def test_24_retorno_nao_expoe_score_formula_pesos_ou_cpf(self):
        resultado = self._processar()

        self.assertEqual(
            {
                "processado",
                "perfil_atualizado",
                "retornar_credito",
                "campo_invalido",
                "erro",
            },
            set(resultado),
        )
        serializado = repr(resultado).lower()
        for proibido in ["score", "formula", "fórmula", "peso", CPF_A]:
            self.assertNotIn(proibido, serializado)

    def test_25_assinatura_nao_aceita_cpf_nem_score(self):
        parametros = set(
            inspect.signature(score_tools.processar_entrevista_credito).parameters
        )

        self.assertEqual(
            {
                "renda_mensal",
                "tipo_emprego",
                "despesas_fixas",
                "num_dependentes",
                "tem_dividas",
                "tool_context",
            },
            parametros,
        )
        for proibido in ["cpf", "score", "novo_score", "limite", "status"]:
            self.assertNotIn(proibido, parametros)

    def test_26_agente_expoe_somente_duas_tools_permitidas(self):
        nomes = [tool.__name__ for tool in agente_entrevista_credito.tools]

        self.assertEqual(
            ["processar_entrevista_credito", "encerrar_atendimento"],
            nomes,
        )

    def test_27_schema_adk_expoe_somente_cinco_respostas(self):
        declaracao = FunctionTool(
            score_tools.processar_entrevista_credito
        )._get_declaration().model_dump(exclude_none=True)
        propriedades = set(declaracao["parameters"]["properties"])

        self.assertEqual(
            {
                "renda_mensal",
                "tipo_emprego",
                "despesas_fixas",
                "num_dependentes",
                "tem_dividas",
            },
            propriedades,
        )

    def test_28_tool_context_fica_oculto_do_modelo(self):
        declaracao = FunctionTool(
            score_tools.processar_entrevista_credito
        )._get_declaration().model_dump(exclude_none=True)
        propriedades = declaracao["parameters"]["properties"]

        self.assertNotIn("tool_context", propriedades)

    def test_29_sessao_nao_autenticada_ou_encerrada_bloqueia_antes_de_io(self):
        contextos = [
            FakeToolContext(autenticado=False),
            FakeToolContext(encerrado=True),
        ]
        with patch("tools.score_tools.pd.read_csv") as read_csv:
            for contexto in contextos:
                with self.subTest(contexto=contexto.state):
                    resultado = self._processar(tool_context=contexto)
                    self.assertFalse(resultado["processado"])
                    self.assertIsNotNone(resultado["erro"])
        read_csv.assert_not_called()

    def test_30_csv_real_permanece_inalterado(self):
        antes = self._hash(config.CSV_CLIENTES)

        resultado = self._processar()

        self.assertTrue(resultado["processado"])
        self.assertEqual(antes, self._hash(config.CSV_CLIENTES))

    def test_31_falha_na_preparacao_preserva_destino_e_remove_temporario(self):
        antes = self.clientes_path.read_bytes()
        original = score_tools.pd.DataFrame.to_csv

        def falhar_preparacao(dataframe, *args, **kwargs):
            if "score_credito" in dataframe.columns:
                raise ValueError("falha injetada")
            return original(dataframe, *args, **kwargs)

        with patch.object(
            score_tools.pd.DataFrame,
            "to_csv",
            side_effect=falhar_preparacao,
            autospec=True,
        ):
            resultado = self._processar()

        self.assertFalse(resultado["processado"])
        self.assertEqual(antes, self.clientes_path.read_bytes())
        self.assertEqual([], self._temporarios())


if __name__ == "__main__":
    unittest.main()
