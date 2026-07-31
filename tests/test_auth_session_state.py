import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_state import (
    AUTH_ATTEMPTS,
    AUTHENTICATED,
    AUTHENTICATED_CPF,
    CONVERSATION_ENDED,
    criar_estado_inicial,
)
from tools.auth_tools import autenticar_cliente, encerrar_atendimento


CPF_VALIDO = "12345678901"
CPF_ALTERNATIVO = "98765432100"
DATA_VALIDA = "01/02/1990"


class FakeToolContext:
    def __init__(self, state=None):
        self.state = criar_estado_inicial() if state is None else state


class AuthSessionStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "clientes.csv"
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "cpf",
                    "nome",
                    "data_nascimento",
                    "score_credito",
                    "limite_credito",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "cpf": CPF_VALIDO,
                    "nome": "Cliente Fictício",
                    "data_nascimento": DATA_VALIDA,
                    "score_credito": "700",
                    "limite_credito": "2500.00",
                }
            )

        self.csv_patch = patch("tools.auth_tools.CSV_CLIENTES", self.csv_path)
        self.csv_patch.start()
        self.addCleanup(self.csv_patch.stop)

    def autenticar_valido(self, context):
        return autenticar_cliente(
            "123.456.789-01",
            DATA_VALIDA,
            tool_context=context,
        )

    def falhar(self, context):
        return autenticar_cliente("invalido", DATA_VALIDA, tool_context=context)

    def test_fabrica_retorna_estados_independentes(self):
        primeiro = criar_estado_inicial()
        segundo = criar_estado_inicial()

        primeiro[AUTH_ATTEMPTS] = 2

        self.assertIsNot(primeiro, segundo)
        self.assertEqual(0, segundo[AUTH_ATTEMPTS])

    def test_autenticacao_valida_grava_cpf_normalizado(self):
        context = FakeToolContext()

        resultado = self.autenticar_valido(context)

        self.assertTrue(resultado["autenticado"])
        self.assertTrue(context.state[AUTHENTICATED])
        self.assertEqual(CPF_VALIDO, context.state[AUTHENTICATED_CPF])

    def test_sucesso_zera_falhas_anteriores(self):
        context = FakeToolContext()
        self.falhar(context)

        self.autenticar_valido(context)

        self.assertEqual(0, context.state[AUTH_ATTEMPTS])
        self.assertFalse(context.state[CONVERSATION_ENDED])

    def test_primeira_falha_registra_uma_tentativa(self):
        context = FakeToolContext()

        resultado = self.falhar(context)

        self.assertEqual(1, context.state[AUTH_ATTEMPTS])
        self.assertEqual(2, resultado["tentativas_restantes"])
        self.assertFalse(resultado["encerrado"])

    def test_segunda_falha_registra_duas_tentativas(self):
        context = FakeToolContext()
        self.falhar(context)

        resultado = self.falhar(context)

        self.assertEqual(2, context.state[AUTH_ATTEMPTS])
        self.assertEqual(1, resultado["tentativas_restantes"])
        self.assertFalse(resultado["encerrado"])

    def test_cpf_inexistente_e_nascimento_divergente_consumem_tentativa(self):
        casos = [
            (CPF_ALTERNATIVO, DATA_VALIDA),
            (CPF_VALIDO, "31/12/1999"),
        ]

        for cpf, nascimento in casos:
            with self.subTest(cpf_existe=cpf == CPF_VALIDO):
                context = FakeToolContext()
                resultado = autenticar_cliente(
                    cpf,
                    nascimento,
                    tool_context=context,
                )

                self.assertFalse(resultado["autenticado"])
                self.assertEqual(1, context.state[AUTH_ATTEMPTS])
                self.assertFalse(context.state[CONVERSATION_ENDED])

    def test_terceira_falha_encerra_sessao(self):
        context = FakeToolContext()
        self.falhar(context)
        self.falhar(context)

        resultado = self.falhar(context)

        self.assertEqual(3, context.state[AUTH_ATTEMPTS])
        self.assertTrue(context.state[CONVERSATION_ENDED])
        self.assertTrue(resultado["tentativas_esgotadas"])
        self.assertEqual(0, resultado["tentativas_restantes"])
        self.assertTrue(resultado["encerrado"])

    def test_quarta_chamada_nao_le_csv_nem_altera_estado(self):
        context = FakeToolContext()
        for _ in range(3):
            self.falhar(context)
        estado_encerrado = context.state.copy()

        with patch("tools.auth_tools.pd.read_csv") as read_csv:
            resultado = self.autenticar_valido(context)

        read_csv.assert_not_called()
        self.assertEqual(estado_encerrado, context.state)
        self.assertFalse(resultado["autenticado"])
        self.assertTrue(resultado["encerrado"])

    def test_falha_tecnica_nao_consome_tentativa(self):
        context = FakeToolContext()
        context.state[AUTH_ATTEMPTS] = 2
        arquivo_ausente = Path(self.temp_dir.name) / "ausente.csv"

        with patch("tools.auth_tools.CSV_CLIENTES", arquivo_ausente):
            resultado = self.autenticar_valido(context)

        self.assertIsNotNone(resultado["erro"])
        self.assertEqual(2, context.state[AUTH_ATTEMPTS])
        self.assertFalse(context.state[CONVERSATION_ENDED])

    def test_csv_malformado_nao_autentica_nem_consome_tentativa(self):
        context = FakeToolContext()
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["cpf", "nome", "data_nascimento"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "cpf": CPF_VALIDO,
                    "nome": "Cliente Fictício",
                    "data_nascimento": DATA_VALIDA,
                }
            )

        resultado = self.autenticar_valido(context)

        self.assertIsNotNone(resultado["erro"])
        self.assertFalse(context.state[AUTHENTICATED])
        self.assertIsNone(context.state[AUTHENTICATED_CPF])
        self.assertEqual(0, context.state[AUTH_ATTEMPTS])
        self.assertFalse(context.state[CONVERSATION_ENDED])

    def test_sessao_autenticada_nao_troca_cpf(self):
        context = FakeToolContext()
        self.autenticar_valido(context)

        with patch("tools.auth_tools.pd.read_csv") as read_csv:
            resultado = autenticar_cliente(
                CPF_ALTERNATIVO,
                "03/04/1985",
                tool_context=context,
            )

        read_csv.assert_not_called()
        self.assertTrue(resultado["ja_autenticado"])
        self.assertEqual(CPF_VALIDO, context.state[AUTHENTICATED_CPF])

    def test_encerramento_explicito_grava_estado(self):
        context = FakeToolContext()

        resultado = encerrar_atendimento(tool_context=context)

        self.assertTrue(resultado["encerrado"])
        self.assertTrue(context.state[CONVERSATION_ENDED])

    def test_encerramento_repetido_e_idempotente(self):
        context = FakeToolContext()
        context.state[AUTHENTICATED] = True
        context.state[AUTHENTICATED_CPF] = CPF_VALIDO

        encerrar_atendimento(tool_context=context)
        encerrar_atendimento(tool_context=context)

        self.assertTrue(context.state[CONVERSATION_ENDED])
        self.assertTrue(context.state[AUTHENTICATED])
        self.assertEqual(CPF_VALIDO, context.state[AUTHENTICATED_CPF])

    def test_duas_sessoes_nao_compartilham_estado(self):
        primeira = FakeToolContext()
        segunda = FakeToolContext()

        self.autenticar_valido(primeira)
        self.falhar(segunda)

        self.assertTrue(primeira.state[AUTHENTICATED])
        self.assertEqual(0, primeira.state[AUTH_ATTEMPTS])
        self.assertFalse(segunda.state[AUTHENTICATED])
        self.assertEqual(1, segunda.state[AUTH_ATTEMPTS])
        self.assertIsNone(segunda.state[AUTHENTICATED_CPF])


if __name__ == "__main__":
    unittest.main()
