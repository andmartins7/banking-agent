# Spec 00 — Memória do Projeto

## Status: Atualizado após a Fase 1

---

## 1. Finalidade

Este documento é o ponto de retomada do projeto Banco Ágil. Ele registra o
último marco integrado na branch principal, as entregas concluídas, as
limitações conhecidas e o próximo trabalho autorizado.

As specs funcionais continuam sendo a fonte de verdade para comportamento e
arquitetura. Esta memória não substitui requisitos, não antecipa fases e deve
ser atualizada somente com evidências verificadas no Git e nos testes.

---

## 2. Estado Verificado no Fechamento da Fase 1

| Item | Estado |
|---|---|
| Branch principal | `main` |
| HEAD funcional pós-F1 | `98ddb6a165ff30a1b503a77317db76061b20da75` |
| `origin/main` no fechamento | `98ddb6a165ff30a1b503a77317db76061b20da75` |
| Fase 1 | concluída e integrada |
| Pull Request | [#7](https://github.com/andmartins7/banking-agent/pull/7), mergeado |
| Suíte completa | 284/284 aprovados |
| Falhos | 0 |
| Ignorados | 0 |
| Próxima fase | Fase 2 — Câmbio seguro e testável |

O commit acima foi criado pelo squash merge do PR #7, com a mensagem:

```text
feat: make credit interview flow deterministic
```

A F2-001 foi concluída somente como investigação. Nenhuma implementação da
Fase 2 foi realizada até este marco.

---

## 3. Fase 0 — Registro Histórico

Esta seção preserva o estado comprovado no fechamento da Fase 0. Os SHAs,
contagens e resultados abaixo são históricos e não representam o HEAD atual.

### 3.1 Estado Verificado no Fechamento da Fase 0

| Item | Estado |
|---|---|
| Branch local | `main` |
| HEAD local | `60716ccc6b949490f545eb8efea10cf46f6f9c85` |
| `origin/main` | `60716ccc6b949490f545eb8efea10cf46f6f9c85` |
| Working tree | limpa |
| Branches locais | somente `main` |
| Branches remotas | somente `origin/main` |

O commit foi criado pelo squash merge do PR
[#6](https://github.com/andmartins7/banking-agent/pull/6), com a mensagem:

```text
fix: consolidate deterministic credit interview
```

### 3.2 Marco Concluído

Estado da Fase 0: **concluída e mergeada**.

Arquivos publicados:

- `tools/score_tools.py`;
- `agents/entrevista_credito.py`;
- `specs/05-agente-entrevista-credito.md`;
- `tests/test_credit_interview_processing.py`.

Garantias comprovadas:

- `processar_entrevista_credito` recebe somente as cinco respostas financeiras
  e o `tool_context` injetado pelo ADK;
- CPF é derivado exclusivamente da sessão autenticada;
- CPF, score, limite, status e score anterior não são argumentos da tool;
- o retorno público contém somente `processado`, `perfil_atualizado`,
  `retornar_credito`, `campo_invalido` e `erro`;
- score, fórmula, pesos, componentes e CPF não são expostos no retorno;
- o schema ADK expõe somente as cinco respostas e oculta `tool_context`;
- o agente de entrevista expõe somente `processar_entrevista_credito` e
  `encerrar_atendimento`;
- validação, cálculo e persistência acontecem em uma operação determinística;
- a escrita de `clientes.csv` usa substituição atômica individual e remove o
  temporário em sucesso ou falha;
- nenhuma dependência foi adicionada.

### 3.3 Evidências Históricas

Validação executada antes e depois do merge da Fase 0:

| Gate | Resultado |
|---|---|
| Testes focados da entrevista | 32/32 aprovados |
| Suíte completa | 135/135 aprovados |
| Falhos | 0 |
| Ignorados | 0 |
| `compileall` | exit code 0 |
| `git diff --check` | exit code 0 |
| Rede ou LLM durante os testes | não utilizados |
| Temporários residuais | nenhum |

Hashes SHA-256 preservados durante as validações:

| CSV | SHA-256 |
|---|---|
| `data/clientes.csv` | `341F5C284764AB8379461F9CA3F3C39CDAF4E83FE79CC8226D33A5A44F3637AC` |
| `data/score_limite.csv` | `0547EB599F85CD59C85433CEF2479FE696E1CF4976F23C8319C8615218AD1A51` |
| `data/solicitacoes_aumento_limite.csv` | `59BE2A8032EC7C1A3D4A56F4D126917ED6F8CD89FFE72514D3BC394E251E1956` |

O aviso conhecido sobre suporte futuro a `google-cloud-storage < 3.0.0` já
existia nesse marco. Nenhuma dependência foi atualizada na Fase 0.

---

## 4. Fase 1 — Estado e Fluxo da Entrevista

Estado: **concluída, validada e integrada**.

A Fase 1 transferiu para código determinístico as decisões críticas do fluxo de
crédito e da entrevista, mantendo o LLM restrito à comunicação e aos usos
explicitamente autorizados das ferramentas.

Entregas comprovadas:

- autenticação, autorização e limite de três tentativas controlados por código;
- encerramento global determinístico e idempotente;
- registro e processamento determinísticos da solicitação de crédito;
- política de aprovação ou rejeição executada em código;
- estado explícito da entrevista na sessão;
- oferta, aceite e recusa classificados deterministicamente;
- coleta ordenada das cinco respostas financeiras, uma por turno;
- máximo de duas tentativas inválidas por pergunta;
- fallback conservador sem processar perfil incompleto;
- cálculo e persistência determinísticos do score;
- associação da entrevista ao mesmo pedido rejeitado;
- reanálise automática do pedido associado, sem criar nova solicitação;
- handoffs entre capacidades mantidos invisíveis para o cliente;
- cenário E2E Crédito → Entrevista → Crédito validado sem rede ou LLM real.

### 4.1 Evidências da Fase 1

| Gate | Resultado |
|---|---|
| Pull Request | [#7](https://github.com/andmartins7/banking-agent/pull/7), mergeado |
| Arquivos integrados | 17 |
| Suíte completa | 284/284 aprovados |
| Falhos | 0 |
| Ignorados | 0 |
| `compileall` | exit code 0 |
| `git diff --check` | exit code 0 |
| Rede ou LLM real nos testes E2E | não utilizados |

---

## 5. Próxima Fase

### Fase 2 — Câmbio seguro e testável

A F2-001 foi concluída como investigação. O hardening de Câmbio ainda não foi
implementado.

Achados comprovados:

- o provider atual é a AwesomeAPI;
- a URL base é controlada por configuração;
- existe allowlist de moedas suportadas;
- existe timeout explícito na chamada HTTP;
- a própria tool de Câmbio não exige autorização da sessão;
- o parsing da resposta externa ainda é permissivo;
- a apresentação final ainda depende do LLM;
- ainda não existem testes funcionais específicos de Câmbio;
- o desenho recomendado é um provider/adapter mínimo e injetável.

O próximo trabalho autorizado é implementar e testar esse hardening sem
expandir o escopo para outras áreas.

---

## 6. Débitos Preservados

Permanecem pendentes após a Fase 1:

- hardening e testes funcionais de Câmbio;
- CI remota;
- concorrência e locks para operações persistentes;
- persistência de sessão apenas em memória;
- warning transitivo sobre `google-cloud-storage < 3.0.0`;
- validação integral do fluxo pela interface Streamlit;
- segurança e observabilidade não cobertas pelas fases concluídas;
- matriz completa de quality gates;
- Docker, deploy e métricas opcionais.

Handoffs invisíveis e encerramento global não aparecem mais como débitos porque
foram concluídos e validados na Fase 1.

---

## 7. Regras de Atualização desta Memória

Ao concluir uma fase:

1. registrar somente o estado já mergeado em `main`;
2. informar SHA, PR e arquivos efetivamente publicados;
3. registrar comandos e resultados de validação;
4. preservar débitos e limitações sem convertê-los em sucesso;
5. apontar uma única próxima fase;
6. não incluir segredos, dados reais ou resultados sem evidência.
