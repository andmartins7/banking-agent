import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
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
        self._write_clientes([self._cliente()])

        self.csv_patch = patch("tools.auth_tools.CSV_CLIENTES", self.csv_path)
        self.csv_patch.start()
        self.addCleanup(self.csv_patch.stop)

    @staticmethod
    def _cliente(
        *,
        cpf=CPF_VALIDO,
        nascimento=DATA_VALIDA,
        nome="Cliente Fictício",
        score="700",
        limite="2500.00",
    ):
        return {
            "cpf": cpf,
            "nome": nome,
            "data_nascimento": nascimento,
            "score_credito": score,
            "limite_credito": limite,
        }

    def _write_clientes(self, rows, fieldnames=None):
        if fieldnames is None:
            fieldnames = [
                "cpf",
                "nome",
                "data_nascimento",
                "score_credito",
                "limite_credito",
            ]
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

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

    def test_csv_com_header_sem_cpf_e_erro_tecnico_controlado(self):
        context = FakeToolContext()
        self._write_clientes(
            [{
                "nome": "Cliente Fictício",
                "data_nascimento": DATA_VALIDA,
                "score_credito": "700",
                "limite_credito": "2500.00",
            }],
            fieldnames=[
                "nome",
                "data_nascimento",
                "score_credito",
                "limite_credito",
            ],
        )

        resultado = self.autenticar_valido(context)

        self.assertFalse(resultado["autenticado"])
        self.assertIsNotNone(resultado["erro"])
        self.assertEqual(0, context.state[AUTH_ATTEMPTS])
        self.assertFalse(context.state[CONVERSATION_ENDED])

    def test_cpf_duplicado_com_linhas_identicas_nao_autentica(self):
        cliente = self._cliente()
        self._write_clientes([cliente, dict(cliente)])
        context = FakeToolContext()

        resultado = self.autenticar_valido(context)

        self.assertFalse(resultado["autenticado"])
        self.assertIsNone(resultado["cliente"])
        self.assertIsNone(resultado["erro"])
        self.assertFalse(context.state[AUTHENTICATED])
        self.assertIsNone(context.state[AUTHENTICATED_CPF])
        self.assertEqual(1, context.state[AUTH_ATTEMPTS])

    def test_cpf_duplicado_com_nascimentos_diferentes_nao_escolhe_linha(self):
        duplicatas = [
            self._cliente(nascimento="31/12/1989", nome="Primeiro"),
            self._cliente(nascimento=DATA_VALIDA, nome="Segundo"),
        ]

        for rows in [duplicatas, list(reversed(duplicatas))]:
            with self.subTest(ordem=[row["nome"] for row in rows]):
                self._write_clientes(rows)
                context = FakeToolContext()

                resultado = self.autenticar_valido(context)

                self.assertFalse(resultado["autenticado"])
                self.assertIsNone(resultado["cliente"])
                self.assertFalse(context.state[AUTHENTICATED])
                self.assertIsNone(context.state[AUTHENTICATED_CPF])
                self.assertEqual(1, context.state[AUTH_ATTEMPTS])

    def test_tres_registros_do_mesmo_cpf_nao_autenticam(self):
        self._write_clientes([
            self._cliente(nascimento=DATA_VALIDA),
            self._cliente(nascimento="02/02/1991"),
            self._cliente(nascimento="03/03/1992"),
        ])

        resultado = self.autenticar_valido(FakeToolContext())

        self.assertFalse(resultado["autenticado"])
        self.assertIsNone(resultado["cliente"])

    def test_duplicidade_nao_expoe_dados_cadastrais_ou_financeiros(self):
        dados_sensiveis = {
            "nome": "Nome Não Exposto",
            "nascimento": DATA_VALIDA,
            "score": "987",
            "limite": "999999.99",
        }
        self._write_clientes([
            self._cliente(**dados_sensiveis),
            self._cliente(
                nascimento="31/12/1989",
                nome="Outro Nome Não Exposto",
                score="123",
                limite="888888.88",
            ),
        ])

        resultado = self.autenticar_valido(FakeToolContext())
        serializado = repr(resultado)

        self.assertIsNone(resultado["cliente"])
        for valor in [
            CPF_VALIDO,
            "Nome Não Exposto",
            "Outro Nome Não Exposto",
            DATA_VALIDA,
            "31/12/1989",
            "987",
            "123",
            "999999.99",
            "888888.88",
        ]:
            self.assertNotIn(valor, serializado)

    def test_duplicidade_participa_do_limite_de_tres_falhas(self):
        context = FakeToolContext()
        self.falhar(context)
        autenticar_cliente(
            CPF_VALIDO,
            "31/12/1999",
            tool_context=context,
        )
        cliente = self._cliente()
        self._write_clientes([cliente, dict(cliente)])

        terceira = self.autenticar_valido(context)

        self.assertEqual(3, context.state[AUTH_ATTEMPTS])
        self.assertTrue(context.state[CONVERSATION_ENDED])
        self.assertTrue(terceira["tentativas_esgotadas"])
        self.assertTrue(terceira["encerrado"])
        estado_encerrado = context.state.copy()

        with patch("tools.auth_tools.pd.read_csv") as read_csv:
            quarta = self.autenticar_valido(context)

        read_csv.assert_not_called()
        self.assertFalse(quarta["autenticado"])
        self.assertEqual(estado_encerrado, context.state)

    def test_autenticacao_valida_apos_duas_falhas_continua_funcionando(self):
        context = FakeToolContext()
        self.falhar(context)
        autenticar_cliente(
            CPF_VALIDO,
            "31/12/1999",
            tool_context=context,
        )

        resultado = self.autenticar_valido(context)

        self.assertTrue(resultado["autenticado"])
        self.assertEqual(CPF_VALIDO, context.state[AUTHENTICATED_CPF])
        self.assertEqual(0, context.state[AUTH_ATTEMPTS])
        self.assertFalse(context.state[CONVERSATION_ENDED])

    def test_duplicidade_mantem_contadores_de_sessoes_independentes(self):
        cliente = self._cliente()
        self._write_clientes([cliente, dict(cliente)])
        primeira = FakeToolContext()
        segunda = FakeToolContext()

        self.autenticar_valido(primeira)
        self.autenticar_valido(segunda)
        self.autenticar_valido(segunda)

        self.assertEqual(1, primeira.state[AUTH_ATTEMPTS])
        self.assertEqual(2, segunda.state[AUTH_ATTEMPTS])
        self.assertFalse(primeira.state[AUTHENTICATED])
        self.assertFalse(segunda.state[AUTHENTICATED])
        self.assertIsNone(primeira.state[AUTHENTICATED_CPF])
        self.assertIsNone(segunda.state[AUTHENTICATED_CPF])

    def test_csv_real_permanece_inalterado_ao_testar_duplicidade(self):
        hash_antes = hashlib.sha256(config.CSV_CLIENTES.read_bytes()).hexdigest()
        cliente = self._cliente()
        self._write_clientes([cliente, dict(cliente)])

        self.autenticar_valido(FakeToolContext())

        hash_depois = hashlib.sha256(config.CSV_CLIENTES.read_bytes()).hexdigest()
        self.assertEqual(hash_antes, hash_depois)

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
