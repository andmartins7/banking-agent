import inspect
import math
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import pandas as pd

import tools.score_tools as score_tools


class CreditInterviewValidationTests(unittest.TestCase):
    def assertValid(self, campo, valor_bruto, esperado):
        resultado = score_tools.validar_resposta_entrevista(campo, valor_bruto)

        self.assertEqual(
            {
                "valida": True,
                "valor_normalizado": esperado,
                "erro": None,
            },
            resultado,
        )

    def assertInvalid(self, campo, valor_bruto):
        resultado = score_tools.validar_resposta_entrevista(campo, valor_bruto)

        self.assertEqual(False, resultado["valida"])
        self.assertIsNone(resultado["valor_normalizado"])
        self.assertIsInstance(resultado["erro"], str)
        self.assertTrue(resultado["erro"])
        return resultado

    def test_um_valor_valido_para_cada_campo(self):
        casos = {
            "renda_mensal": (5000, 5000.0),
            "tipo_emprego": ("formal", "formal"),
            "despesas_fixas": (2000, 2000.0),
            "num_dependentes": (1, 1),
            "tem_dividas": ("nao", "nao"),
        }

        for campo, (valor_bruto, esperado) in casos.items():
            with self.subTest(campo=campo):
                self.assertValid(campo, valor_bruto, esperado)

    def test_aliases_de_emprego_sao_normalizados(self):
        casos = {
            "formal": [
                "formal",
                "CLT",
                "empregado",
                "registrado",
                "carteira assinada",
            ],
            "autonomo": ["autonomo", "autônomo", "MEI", "freelancer"],
            "desempregado": ["desempregado", "sem emprego"],
        }

        for esperado, aliases in casos.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertValid("tipo_emprego", alias, esperado)

    def test_aliases_de_dividas_sao_normalizados(self):
        casos = {
            "sim": ["sim", "s", "tenho", "possuo", "yes"],
            "nao": ["não", "nao", "n", "não tenho", "nao tenho", "no"],
        }

        for esperado, aliases in casos.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertValid("tem_dividas", alias, esperado)

    def test_espacos_e_caixa_dos_aliases_sao_normalizados(self):
        self.assertValid("tipo_emprego", "  CARTEIRA   ASSINADA ", "formal")
        self.assertValid("tem_dividas", "  NÃO   TENHO ", "nao")

    def test_textos_numericos_aceitos_sao_normalizados(self):
        self.assertValid("renda_mensal", "5000", 5000.0)
        self.assertValid("despesas_fixas", "2000", 2000.0)
        self.assertValid("num_dependentes", "1", 1)

    def test_negativos_sao_rejeitados(self):
        for campo in ["renda_mensal", "despesas_fixas", "num_dependentes"]:
            with self.subTest(campo=campo):
                self.assertInvalid(campo, -1)

    def test_booleanos_numericos_sao_rejeitados(self):
        for campo in ["renda_mensal", "despesas_fixas", "num_dependentes"]:
            with self.subTest(campo=campo):
                self.assertInvalid(campo, True)

    def test_nan_e_rejeitado(self):
        for campo in ["renda_mensal", "despesas_fixas", "num_dependentes"]:
            with self.subTest(campo=campo):
                self.assertInvalid(campo, math.nan)

    def test_infinitos_sao_rejeitados(self):
        casos = [
            ("renda_mensal", math.inf),
            ("despesas_fixas", -math.inf),
            ("num_dependentes", math.inf),
        ]
        for campo, valor in casos:
            with self.subTest(campo=campo, valor=valor):
                self.assertInvalid(campo, valor)

    def test_dependentes_fracionarios_sao_rejeitados(self):
        self.assertInvalid("num_dependentes", 1.5)

    def test_emprego_desconhecido_e_rejeitado(self):
        resultado = self.assertInvalid("tipo_emprego", "efetivado")

        self.assertEqual("Situação de emprego não reconhecida.", resultado["erro"])

    def test_divida_desconhecida_e_rejeitada(self):
        resultado = self.assertInvalid("tem_dividas", "talvez")

        self.assertEqual("Resposta sobre dívidas não reconhecida.", resultado["erro"])

    def test_campo_desconhecido_e_rejeitado(self):
        for campo in ["score", "", None, ["renda_mensal"]]:
            with self.subTest(campo=campo):
                resultado = self.assertInvalid(campo, 5000)
                self.assertEqual(
                    "Campo da entrevista desconhecido.",
                    resultado["erro"],
                )

    def test_interface_nao_recebe_sessao(self):
        parametros = set(
            inspect.signature(score_tools.validar_resposta_entrevista).parameters
        )

        self.assertEqual({"campo", "valor_bruto"}, parametros)

    def test_interface_nao_acessa_sessao_csv_score_ou_persistencia(self):
        with (
            patch("tools.score_tools.obter_cpf_autorizado") as obter_cpf,
            patch("tools.score_tools.pd.read_csv") as read_csv,
            patch("tools.score_tools._calcular_score_oficial") as calcular,
            patch("tools.score_tools._escrever_clientes_atomico") as escrever,
        ):
            resultado = score_tools.validar_resposta_entrevista(
                "tipo_emprego",
                "CLT",
            )

        self.assertTrue(resultado["valida"])
        obter_cpf.assert_not_called()
        read_csv.assert_not_called()
        calcular.assert_not_called()
        escrever.assert_not_called()

    def test_tool_consolidada_usa_somente_respostas_autorizadas_do_estado(self):
        clientes = pd.DataFrame([
            {
                "cpf": "11111111111",
                "score_credito": "500",
            }
        ])
        original = score_tools.validar_resposta_entrevista
        contexto = SimpleNamespace(state={
            "credit_interview_status": "ready_for_processing",
            "credit_interview_return_pending": False,
        })

        with (
            patch(
                "tools.score_tools.validar_resposta_entrevista",
                wraps=original,
            ) as validar,
            patch(
                "tools.score_tools._autorizar_processamento_entrevista",
                return_value={
                    "autorizado": True,
                    "cpf": "11111111111",
                    "respostas": {
                        "renda_mensal": 5000.0,
                        "tipo_emprego": "formal",
                        "despesas_fixas": 2000.0,
                        "num_dependentes": 1,
                        "tem_dividas": "nao",
                    },
                    "erro": None,
                    "campo_invalido": None,
                },
            ) as autorizar,
            patch("tools.score_tools.pd.read_csv", return_value=clientes),
            patch(
                "tools.score_tools._calcular_score_oficial",
                return_value=555,
            ) as calcular,
            patch("tools.score_tools._escrever_clientes_atomico"),
        ):
            resultado = score_tools.processar_entrevista_credito(
                renda_mensal="5000",
                tipo_emprego="CLT",
                despesas_fixas="2000",
                num_dependentes="1",
                tem_dividas="não tenho",
                tool_context=contexto,
            )

        self.assertTrue(resultado["processado"])
        autorizar.assert_called_once()
        self.assertEqual(
            [
                call("renda_mensal", 5000.0),
                call("tipo_emprego", "formal"),
                call("despesas_fixas", 2000.0),
                call("num_dependentes", 1),
                call("tem_dividas", "nao"),
            ],
            validar.call_args_list,
        )
        calcular.assert_called_once_with(5000.0, "formal", 2000.0, 1, "nao")


if __name__ == "__main__":
    unittest.main()
