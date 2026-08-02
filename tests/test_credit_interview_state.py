import unittest

from google.adk.sessions.state import State

from session_state import (
    CONVERSATION_ENDED,
    CREDIT_INTERVIEW_ATTEMPTS,
    CREDIT_INTERVIEW_COLLECTING,
    CREDIT_INTERVIEW_COMPLETED,
    CREDIT_INTERVIEW_CURRENT_FIELD,
    CREDIT_INTERVIEW_DECLINED,
    CREDIT_INTERVIEW_FIELDS,
    CREDIT_INTERVIEW_INTERRUPTED,
    CREDIT_INTERVIEW_NOT_OFFERED,
    CREDIT_INTERVIEW_OFFERED,
    CREDIT_INTERVIEW_READY,
    CREDIT_INTERVIEW_RESPONSES,
    CREDIT_INTERVIEW_RETURN_PENDING,
    CREDIT_INTERVIEW_STATUS,
    _copiar_estado,
    aceitar_entrevista_credito,
    concluir_processamento_entrevista,
    criar_estado_inicial,
    oferecer_entrevista_credito,
    recusar_entrevista_credito,
    registrar_resposta_entrevista,
    registrar_resposta_invalida_entrevista,
)


class CreditInterviewStateTests(unittest.TestCase):
    @staticmethod
    def _estado_em_coleta():
        return aceitar_entrevista_credito(
            oferecer_entrevista_credito(criar_estado_inicial())
        )

    @staticmethod
    def _respostas_validas():
        return {
            "renda_mensal": 5000.0,
            "tipo_emprego": "formal",
            "despesas_fixas": 2000.0,
            "num_dependentes": 1,
            "tem_dividas": "nao",
        }

    def _estado_pronto(self):
        state = self._estado_em_coleta()
        for campo in CREDIT_INTERVIEW_FIELDS:
            self.assertEqual(campo, state[CREDIT_INTERVIEW_CURRENT_FIELD])
            state = registrar_resposta_entrevista(
                state,
                self._respostas_validas()[campo],
            )
        return state

    def test_estado_inicial(self):
        state = criar_estado_inicial()

        self.assertEqual(
            CREDIT_INTERVIEW_NOT_OFFERED,
            state[CREDIT_INTERVIEW_STATUS],
        )
        self.assertIsNone(state[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual({}, state[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(
            {campo: 0 for campo in CREDIT_INTERVIEW_FIELDS},
            state[CREDIT_INTERVIEW_ATTEMPTS],
        )
        self.assertFalse(state[CREDIT_INTERVIEW_RETURN_PENDING])

    def test_copia_estado_dict_preserva_conteudo_e_isola_colecoes(self):
        original = criar_estado_inicial()
        original[CREDIT_INTERVIEW_RESPONSES]["renda_mensal"] = 5000.0

        copia = _copiar_estado(original)

        self.assertEqual(original, copia)
        copia[CREDIT_INTERVIEW_RESPONSES]["renda_mensal"] = 7000.0
        copia[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"] = 1
        self.assertEqual(5000.0, original[CREDIT_INTERVIEW_RESPONSES]["renda_mensal"])
        self.assertEqual(0, original[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])

    def test_copia_estado_adk_real_preserva_conteudo_e_isola_colecoes(self):
        original = State(value=criar_estado_inicial(), delta={})

        copia = _copiar_estado(original)

        self.assertEqual(original.to_dict(), copia)
        copia[CREDIT_INTERVIEW_RESPONSES]["renda_mensal"] = 5000.0
        copia[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"] = 1
        self.assertEqual({}, original.get(CREDIT_INTERVIEW_RESPONSES))
        self.assertEqual(0, original.get(CREDIT_INTERVIEW_ATTEMPTS)["renda_mensal"])

    def test_copia_rejeita_objeto_incompativel(self):
        with self.assertRaisesRegex(TypeError, "não pode ser copiado"):
            _copiar_estado(object())

    def test_oferta(self):
        inicial = criar_estado_inicial()

        oferecida = oferecer_entrevista_credito(inicial)

        self.assertEqual(CREDIT_INTERVIEW_OFFERED, oferecida[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(CREDIT_INTERVIEW_NOT_OFFERED, inicial[CREDIT_INTERVIEW_STATUS])

    def test_aceite_aponta_para_primeira_pergunta(self):
        oferecida = oferecer_entrevista_credito(criar_estado_inicial())

        aceita = aceitar_entrevista_credito(oferecida)

        self.assertEqual(CREDIT_INTERVIEW_COLLECTING, aceita[CREDIT_INTERVIEW_STATUS])
        self.assertEqual(
            CREDIT_INTERVIEW_FIELDS[0],
            aceita[CREDIT_INTERVIEW_CURRENT_FIELD],
        )

    def test_recusa_nao_inicia_coleta(self):
        oferecida = oferecer_entrevista_credito(criar_estado_inicial())

        recusada = recusar_entrevista_credito(oferecida)

        self.assertEqual(CREDIT_INTERVIEW_DECLINED, recusada[CREDIT_INTERVIEW_STATUS])
        self.assertIsNone(recusada[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual({}, recusada[CREDIT_INTERVIEW_RESPONSES])

    def test_ordem_canonica_das_cinco_perguntas(self):
        self.assertEqual(
            (
                "renda_mensal",
                "tipo_emprego",
                "despesas_fixas",
                "num_dependentes",
                "tem_dividas",
            ),
            CREDIT_INTERVIEW_FIELDS,
        )

    def test_resposta_valida_avanca_para_proxima_pergunta(self):
        state = self._estado_em_coleta()

        atualizado = registrar_resposta_entrevista(state, 5000.0)

        self.assertEqual("tipo_emprego", atualizado[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual(
            {"renda_mensal": 5000.0},
            atualizado[CREDIT_INTERVIEW_RESPONSES],
        )

    def test_respostas_validas_sao_preservadas_apos_invalidade_posterior(self):
        state = self._estado_em_coleta()
        state = registrar_resposta_entrevista(state, 5000.0)
        state = registrar_resposta_entrevista(state, "formal")

        atualizado = registrar_resposta_invalida_entrevista(state)

        self.assertEqual(
            {"renda_mensal": 5000.0, "tipo_emprego": "formal"},
            atualizado[CREDIT_INTERVIEW_RESPONSES],
        )
        self.assertEqual("despesas_fixas", atualizado[CREDIT_INTERVIEW_CURRENT_FIELD])

    def test_primeira_invalidade_permite_nova_tentativa(self):
        state = self._estado_em_coleta()

        atualizado = registrar_resposta_invalida_entrevista(state)

        self.assertEqual(CREDIT_INTERVIEW_COLLECTING, atualizado[CREDIT_INTERVIEW_STATUS])
        self.assertEqual("renda_mensal", atualizado[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual(1, atualizado[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])

    def test_tentativas_invalidas_sao_isoladas_por_pergunta(self):
        state = registrar_resposta_invalida_entrevista(self._estado_em_coleta())
        state = registrar_resposta_entrevista(state, 5000.0)

        atualizado = registrar_resposta_invalida_entrevista(state)

        self.assertEqual(1, atualizado[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])
        self.assertEqual(1, atualizado[CREDIT_INTERVIEW_ATTEMPTS]["tipo_emprego"])

    def test_segunda_invalidade_interrompe_por_fallback(self):
        state = registrar_resposta_invalida_entrevista(self._estado_em_coleta())

        interrompida = registrar_resposta_invalida_entrevista(state)
        repetida = registrar_resposta_invalida_entrevista(interrompida)

        self.assertEqual(
            CREDIT_INTERVIEW_INTERRUPTED,
            interrompida[CREDIT_INTERVIEW_STATUS],
        )
        self.assertEqual(2, interrompida[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])
        self.assertEqual({}, interrompida[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(interrompida, repetida)

    def test_fallback_nao_conclui_nem_autoriza_retorno(self):
        state = registrar_resposta_invalida_entrevista(self._estado_em_coleta())
        interrompida = registrar_resposta_invalida_entrevista(state)

        resultado = concluir_processamento_entrevista(interrompida)

        self.assertEqual(CREDIT_INTERVIEW_INTERRUPTED, resultado[CREDIT_INTERVIEW_STATUS])
        self.assertFalse(resultado[CREDIT_INTERVIEW_RETURN_PENDING])

    def test_cinco_respostas_validas_deixam_estado_pronto(self):
        state = self._estado_pronto()

        self.assertEqual(CREDIT_INTERVIEW_READY, state[CREDIT_INTERVIEW_STATUS])
        self.assertIsNone(state[CREDIT_INTERVIEW_CURRENT_FIELD])
        self.assertEqual(self._respostas_validas(), state[CREDIT_INTERVIEW_RESPONSES])
        self.assertFalse(state[CREDIT_INTERVIEW_RETURN_PENDING])

    def test_processamento_bem_sucedido_conclui_entrevista(self):
        concluida = concluir_processamento_entrevista(self._estado_pronto())

        self.assertEqual(CREDIT_INTERVIEW_COMPLETED, concluida[CREDIT_INTERVIEW_STATUS])

    def test_segunda_conclusao_nao_produz_nova_transicao(self):
        concluida = concluir_processamento_entrevista(self._estado_pronto())

        repetida = concluir_processamento_entrevista(concluida)

        self.assertEqual(concluida, repetida)

    def test_retorno_ao_credito_fica_pendente_somente_apos_sucesso(self):
        pronta = self._estado_pronto()
        concluida = concluir_processamento_entrevista(pronta)

        self.assertFalse(pronta[CREDIT_INTERVIEW_RETURN_PENDING])
        self.assertTrue(concluida[CREDIT_INTERVIEW_RETURN_PENDING])

    def test_encerramento_global_bloqueia_todas_as_transicoes(self):
        cenarios = [
            (criar_estado_inicial(), oferecer_entrevista_credito),
            (
                oferecer_entrevista_credito(criar_estado_inicial()),
                aceitar_entrevista_credito,
            ),
            (
                oferecer_entrevista_credito(criar_estado_inicial()),
                recusar_entrevista_credito,
            ),
            (self._estado_em_coleta(), registrar_resposta_invalida_entrevista),
            (self._estado_pronto(), concluir_processamento_entrevista),
        ]

        for state, transicao in cenarios:
            with self.subTest(transicao=transicao.__name__):
                state[CONVERSATION_ENDED] = True
                self.assertEqual(state, transicao(state))

        state = self._estado_em_coleta()
        state[CONVERSATION_ENDED] = True
        self.assertEqual(state, registrar_resposta_entrevista(state, 5000.0))

    def test_transicoes_nao_mutam_estado_de_entrada(self):
        state = self._estado_em_coleta()
        antes = dict(state)
        antes[CREDIT_INTERVIEW_RESPONSES] = dict(state[CREDIT_INTERVIEW_RESPONSES])
        antes[CREDIT_INTERVIEW_ATTEMPTS] = dict(state[CREDIT_INTERVIEW_ATTEMPTS])

        registrar_resposta_invalida_entrevista(state)
        registrar_resposta_entrevista(state, 5000.0)

        self.assertEqual(antes, state)

    def test_transicoes_fora_do_estado_de_origem_nao_avancam(self):
        inicial = criar_estado_inicial()
        oferecida = oferecer_entrevista_credito(inicial)

        self.assertEqual(inicial, aceitar_entrevista_credito(inicial))
        self.assertEqual(inicial, recusar_entrevista_credito(inicial))
        self.assertEqual(
            oferecida,
            registrar_resposta_entrevista(oferecida, 5000.0),
        )
        self.assertEqual(
            oferecida,
            registrar_resposta_invalida_entrevista(oferecida),
        )
        self.assertEqual(
            oferecida,
            concluir_processamento_entrevista(oferecida),
        )

    def test_duas_sessoes_possuem_estado_independente(self):
        sessao_a = criar_estado_inicial()
        sessao_b = criar_estado_inicial()

        sessao_a = registrar_resposta_invalida_entrevista(
            aceitar_entrevista_credito(oferecer_entrevista_credito(sessao_a))
        )
        sessao_a[CREDIT_INTERVIEW_RESPONSES]["isolamento"] = True

        self.assertEqual(CREDIT_INTERVIEW_NOT_OFFERED, sessao_b[CREDIT_INTERVIEW_STATUS])
        self.assertEqual({}, sessao_b[CREDIT_INTERVIEW_RESPONSES])
        self.assertEqual(0, sessao_b[CREDIT_INTERVIEW_ATTEMPTS]["renda_mensal"])


if __name__ == "__main__":
    unittest.main()
