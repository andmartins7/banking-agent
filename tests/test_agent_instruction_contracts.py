import unittest

from agents._model import MODELO_ATIVO
from agents.cambio import SYSTEM_PROMPT_CAMBIO, agente_cambio
from agents.credito import SYSTEM_PROMPT_CREDITO, agente_credito
from agents.entrevista_credito import (
    SYSTEM_PROMPT_ENTREVISTA,
    agente_entrevista_credito,
)
from agents.triagem import SYSTEM_PROMPT_TRIAGEM, agente_triagem


class AgentInstructionContractTests(unittest.TestCase):
    @staticmethod
    def _prompt_normalizado(prompt):
        return " ".join(prompt.casefold().split())

    def test_01_credito_nao_decide_aprovacao_ou_rejeicao(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CREDITO)

        self.assertIn("nunca aprove ou rejeite manualmente", prompt)
        self.assertIn(
            "status final e o valor aplicado pertencem exclusivamente",
            prompt,
        )
        self.assertIn("decisão de aprovação ou rejeição", prompt)

    def test_02_credito_nao_interpreta_aceite_ou_recusa(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CREDITO)

        self.assertIn(
            "não ofereça, interprete resposta ou inicie entrevista",
            prompt,
        )
        self.assertIn("oferta da entrevista, aceite, recusa", prompt)

    def test_03_credito_nao_controla_reanalise(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CREDITO)

        self.assertIn("sistema reanalisa automaticamente", prompt)
        self.assertIn("não tente selecionar solicitações para reanálise", prompt)
        self.assertIn("não crie outra solicitação após a entrevista", prompt)

    def test_04_entrevista_nao_escolhe_sequencia_ou_pergunta(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA)

        self.assertIn("pergunta atual, sequência dos cinco campos", prompt)
        self.assertIn("não escolha perguntas", prompt)
        self.assertIn("não avance campos", prompt)

    def test_05_entrevista_nao_reconstroi_respostas_do_historico(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA)

        self.assertIn(
            "não reconstrua respostas ou argumentos financeiros pelo histórico textual",
            prompt,
        )

    def test_06_entrevista_nao_controla_tentativas_ou_fallback(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA)

        self.assertIn("contagem de tentativas, fallback", prompt)
        self.assertIn("controlados deterministicamente pelo sistema", prompt)

    def test_07_entrevista_nao_decide_quando_processar(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA)

        self.assertIn("não decida quando processar a entrevista", prompt)
        self.assertIn("não invoque processamento por iniciativa própria", prompt)

    def test_08_entrevista_nao_calcula_score(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA)

        self.assertIn("nunca calcule, estime, revele ou invente score", prompt)
        self.assertIn("nunca decida aprovação, rejeição ou valor de limite", prompt)

    def test_09_prompts_proibem_inventar_dados_criticos(self):
        prompts = {
            "triagem": self._prompt_normalizado(SYSTEM_PROMPT_TRIAGEM),
            "credito": self._prompt_normalizado(SYSTEM_PROMPT_CREDITO),
            "entrevista": self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA),
            "cambio": self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO),
        }

        for nome, prompt in prompts.items():
            with self.subTest(nome=nome):
                self.assertIn("nunca invente", prompt)
        self.assertIn("cpf, score, limite, status, timestamp", prompts["credito"])
        self.assertIn("cpf, renda, emprego, despesas", prompts["entrevista"])

    def test_10_prompts_nao_anunciam_handoffs(self):
        proibidos = {
            "vou transferir",
            "agente de crédito",
            "agente de entrevista",
            "agente de triagem",
            "retornando ao agente",
            "retornar ao agente",
        }
        for nome, prompt in {
            "triagem": SYSTEM_PROMPT_TRIAGEM,
            "credito": SYSTEM_PROMPT_CREDITO,
            "entrevista": SYSTEM_PROMPT_ENTREVISTA,
            "cambio": SYSTEM_PROMPT_CAMBIO,
        }.items():
            normalizado = self._prompt_normalizado(prompt)
            with self.subTest(nome=nome):
                self.assertTrue(proibidos.isdisjoint({
                    frase for frase in proibidos if frase in normalizado
                }))

    def test_11_credito_nao_anuncia_mudanca_para_entrevista(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CREDITO)

        self.assertNotIn("transfira", prompt)
        self.assertNotIn("especialista de análise financeira", prompt)
        self.assertIn("nunca anuncie redirecionamentos", prompt)

    def test_12_entrevista_nao_anuncia_retorno_ao_credito(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA)

        self.assertNotIn("vou retomar", prompt)
        self.assertNotIn("retorno ao agente", prompt)
        self.assertIn("não anuncie redirecionamento", prompt)

    def test_13_triagem_nao_revela_redirecionamento_interno(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_TRIAGEM)

        self.assertNotIn("transfira", prompt)
        self.assertNotIn("transferência", prompt)
        self.assertIn("mudança de capacidade deve ser natural e invisível", prompt)

    def test_14_tom_respeitoso_e_objetivo_permanece(self):
        for nome, prompt in {
            "triagem": SYSTEM_PROMPT_TRIAGEM,
            "credito": SYSTEM_PROMPT_CREDITO,
            "entrevista": SYSTEM_PROMPT_ENTREVISTA,
            "cambio": SYSTEM_PROMPT_CAMBIO,
        }.items():
            normalizado = self._prompt_normalizado(prompt)
            with self.subTest(nome=nome):
                self.assertIn("respeitoso", normalizado)
                self.assertIn("objetivo", normalizado)

    def test_15_prompts_declaram_autoridade_do_sistema_e_estado(self):
        prompts = [
            self._prompt_normalizado(SYSTEM_PROMPT_TRIAGEM),
            self._prompt_normalizado(SYSTEM_PROMPT_CREDITO),
            self._prompt_normalizado(SYSTEM_PROMPT_ENTREVISTA),
            self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO),
        ]

        for prompt in prompts:
            with self.subTest(prompt=prompt[:40]):
                self.assertIn("autoridade do sistema", prompt)
                self.assertIn("estado", prompt)
                self.assertIn("sistema", prompt)

    def test_16_tools_expostas_permanecem_iguais_ao_baseline(self):
        tools_por_agente = {
            "triagem": [tool.__name__ for tool in agente_triagem.tools],
            "credito": [tool.__name__ for tool in agente_credito.tools],
            "entrevista": [
                tool.__name__ for tool in agente_entrevista_credito.tools
            ],
            "cambio": [tool.__name__ for tool in agente_cambio.tools],
        }

        self.assertEqual(
            ["autenticar_cliente", "encerrar_atendimento"],
            tools_por_agente["triagem"],
        )
        self.assertEqual(
            [
                "consultar_limite",
                "registrar_solicitacao",
                "processar_solicitacao",
                "encerrar_atendimento",
            ],
            tools_por_agente["credito"],
        )
        self.assertEqual(
            ["processar_entrevista_credito", "encerrar_atendimento"],
            tools_por_agente["entrevista"],
        )
        self.assertEqual(
            ["buscar_cotacao", "encerrar_atendimento"],
            tools_por_agente["cambio"],
        )

    def test_17_agente_de_entrevista_continua_existindo(self):
        self.assertEqual(
            "agente_entrevista_credito",
            agente_entrevista_credito.name,
        )
        self.assertEqual([], agente_entrevista_credito.sub_agents)

    def test_18_quatro_agentes_e_topologia_permanecem_definidos(self):
        agentes = {
            agente_triagem.name,
            agente_credito.name,
            agente_entrevista_credito.name,
            agente_cambio.name,
        }

        self.assertEqual({
            "agente_triagem",
            "agente_credito",
            "agente_entrevista_credito",
            "agente_cambio",
        }, agentes)
        self.assertEqual(
            ["agente_credito", "agente_cambio"],
            [agente.name for agente in agente_triagem.sub_agents],
        )
        self.assertEqual(
            ["agente_entrevista_credito"],
            [agente.name for agente in agente_credito.sub_agents],
        )

    def test_19_encerramento_explicito_nao_e_delegado_ao_llm(self):
        for nome, prompt in {
            "triagem": SYSTEM_PROMPT_TRIAGEM,
            "credito": SYSTEM_PROMPT_CREDITO,
            "entrevista": SYSTEM_PROMPT_ENTREVISTA,
            "cambio": SYSTEM_PROMPT_CAMBIO,
        }.items():
            normalizado = self._prompt_normalizado(prompt)
            with self.subTest(nome=nome):
                self.assertNotIn("use `encerrar_atendimento`", normalizado)
                self.assertIn("encerramento explícito", normalizado)
                self.assertIn("sistema", normalizado)

    def test_20_cambio_preserva_identidade_modelo_tools_e_topologia(self):
        self.assertEqual("agente_cambio", agente_cambio.name)
        self.assertEqual(MODELO_ATIVO, agente_cambio.model)
        self.assertEqual([], agente_cambio.sub_agents)
        self.assertEqual(
            ["buscar_cotacao", "encerrar_atendimento"],
            [tool.__name__ for tool in agente_cambio.tools],
        )

    def test_21_cambio_exige_tool_e_proibe_cotacao_propria(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        self.assertIn("para toda solicitação de cotação", prompt)
        self.assertIn("usar `buscar_cotacao`", prompt)
        self.assertIn("nunca invente ou estime cotação", prompt)
        self.assertIn("conhecimento próprio", prompt)

    def test_22_cambio_nao_altera_nem_formata_dados_finais(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        self.assertIn("não altere, arredonde, reformate, omita ou complemente", prompt)
        self.assertIn("qualquer valor retornado", prompt)
        self.assertIn("não formate nem reescreva", prompt)
        self.assertIn("apresentação financeira final", prompt)
        self.assertIn("pertence ao sistema", prompt)

    def test_23_cambio_nao_cria_fallback_numerico_ou_esconde_falha(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        self.assertIn("não esconda nem substitua o erro", prompt)
        self.assertIn("nunca produza estimativa ou fallback numérico", prompt)
        self.assertIn("não repita automaticamente", prompt)

    def test_24_cambio_nao_infere_ou_converte_temporalidade(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        self.assertIn("não converta datas, horários ou timestamps", prompt)
        self.assertIn("nunca infira timezone, fuso ou offset", prompt)

    def test_25_cambio_desambigua_sem_escolher_moeda_arbitrariamente(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        self.assertIn("correspondência inequívoca", prompt)
        self.assertIn("moeda estiver ausente ou ambígua", prompt)
        self.assertIn("pedir esclarecimento", prompt)
        self.assertIn("nunca escolher uma moeda arbitrariamente", prompt)
        self.assertIn("não invente código nem prometa suporte", prompt)

    def test_26_cambio_nao_converte_ou_interpreta_financeiramente(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        self.assertIn("não converta valores para outra moeda", prompt)
        self.assertIn("destino da consulta atual é brl", prompt)
        self.assertIn("não calcule spread, média, percentual adicional", prompt)
        self.assertIn("não interprete compra ou venda", prompt)
        self.assertIn("aconselhar o cliente", prompt)

    def test_27_cambio_nao_vaza_arquitetura_nem_acessa_outros_dominios(self):
        prompt = self._prompt_normalizado(SYSTEM_PROMPT_CAMBIO)
        proibidos = {
            "functionresponse",
            "renderer",
            "orquestrador",
            "provider",
            "schema",
            "json",
            "http",
            "url",
            "api",
            "cpf do cliente",
            "calcule score",
            "aumente limite",
        }
        self.assertTrue(proibidos.isdisjoint({
            termo for termo in proibidos if termo in prompt
        }))
        self.assertIn("não solicite nem tente obter cpf", prompt)
        self.assertIn("nunca anuncie transferência", prompt)
        self.assertIn("handoff", prompt)


if __name__ == "__main__":
    unittest.main()
