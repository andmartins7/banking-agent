# Spec 00 — Memória do Projeto

## Status: Fase 3 funcional concluída — modo entrega/MVP

---

## 1. Finalidade

Este documento é o ponto de retomada do projeto Banco Ágil. Ele registra o
último marco integrado na branch principal, as entregas concluídas, as
limitações conhecidas e o próximo trabalho autorizado.

As specs funcionais continuam sendo a fonte de verdade para comportamento e
arquitetura. Esta memória não substitui requisitos, não antecipa fases e deve
ser atualizada somente com evidências verificadas no Git e nos testes.

---

## 2. Estado Verificado no Fechamento da Fase 2

| Item | Estado |
|---|---|
| Branch principal | `main` |
| HEAD funcional pós-F2 | `746fa5ba59043b041a19a3c6bef450975c9c5f11` |
| `origin/main` no fechamento | `746fa5ba59043b041a19a3c6bef450975c9c5f11` |
| Fase 1 | concluída e integrada |
| Fase 2 | concluída e integrada |
| Pull Request da Fase 2 | [#9](https://github.com/andmartins7/banking-agent/pull/9), squash-mergeado |
| Suíte completa | 365/365 aprovados |
| Falhos | 0 |
| Erros | 0 |
| Ignorados | 0 |
| Próximo passo | investigar P0 restante e o estado atual da UI/fluxo completo |

O HEAD acima foi criado pelo squash merge do PR #9, com a mensagem:

```text
feat: make fx flow safe and deterministic
```

O squash SHA da Fase 2 é
`746fa5ba59043b041a19a3c6bef450975c9c5f11`. As Fases 1 e 2 estão
concluídas e integradas em `main`.

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

A Fase 1 foi integrada pelo squash merge do PR #7 no commit
`98ddb6a165ff30a1b503a77317db76061b20da75`, com a mensagem:

```text
feat: make credit interview flow deterministic
```

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

## 5. Fase 2 — Câmbio Seguro e Determinístico

Estado: **concluída, validada e integrada**.

A Fase 2 isolou a integração externa, tornou a autorização e a apresentação
da cotação determinísticas e impediu que o LLM produzisse ou alterasse dados
financeiros.

Entregas comprovadas:

- integração com a AwesomeAPI isolada atrás de provider testável;
- URL controlada e allowlist de moedas suportadas;
- timeout HTTP explícito;
- parsing e schema externos estritos;
- números financeiros validados sem aceitar valores inválidos ou artificiais;
- temporalidade fornecida pela fonte preservada, sem timezone inventada;
- tool disponível somente para sessão autenticada e ativa;
- CPF obtido internamente da sessão e ausente do schema exposto ao LLM;
- falhas controladas sem cotações ou valores financeiros fictícios;
- renderer determinístico com precedência sobre o texto produzido pelo LLM;
- agente proibido de inventar, estimar, arredondar ou alterar a cotação;
- handoff interno mantido invisível para o cliente;
- cenários E2E de sucesso e indisponibilidade comprovados;
- testes executados sem rede externa ou LLM real;
- integridade dos três CSVs reais preservada por hashes SHA-256.

### 5.1 Evidências da Fase 2

| Gate | Resultado |
|---|---|
| Pull Request | [#9](https://github.com/andmartins7/banking-agent/pull/9), squash-mergeado |
| Squash SHA | `746fa5ba59043b041a19a3c6bef450975c9c5f11` |
| Arquivos integrados | 9 |
| Testes focados de Câmbio | 115/115 aprovados |
| Suíte completa | 365/365 aprovados |
| Falhos | 0 |
| Erros | 0 |
| Ignorados | 0 |
| `compileall` | exit code 0 |
| `git diff --check` | exit code 0 |
| Rede externa ou LLM real nos testes | não utilizados |
| CSVs reais | hashes SHA-256 preservados |

---

## 6. Fase 3 — Checkpoint Funcional do MVP

Estado funcional: **concluído, validado e integrado**.

A Fase 3 fechou o fluxo vertical da UI, consolidou o hardening P0 e validou o
MVP completo. A auditoria final F3-019 registrou decisão **GO**, sem bloqueio P0
funcional.

| Item | Estado |
|---|---|
| Branch principal | `main` |
| HEAD funcional | `ba362963dd1152e248f5fe4abb8106b0c4dd1680` |
| Auditoria F3-019 | GO |
| Suíte completa | 417/417 aprovados |
| Falhos | 0 |
| Erros | 0 |
| Ignorados | 0 |
| P0 funcionais | concluídos |
| Modo do projeto | entrega/MVP |

Evidências funcionais consolidadas:

- autenticação vertical e encerramento determinístico após três falhas;
- consulta e aumento de limite, nos caminhos aprovado e rejeitado;
- entrevista financeira, recálculo de score e reanálise do mesmo pedido;
- Câmbio com sucesso e indisponibilidade controlada;
- encerramento global e isolamento entre sessões;
- testes verticais de Streamlit sem LLM ou rede real;
- integridade dos CSVs reais preservada.

No fechamento da F3-019, a atualização do README era a última pendência
documental P0. A F3-020 finaliza essa documentação sem alterar o checkpoint
funcional acima.

---

## 7. Próximo Passo

Próximo: concluir a submissão do MVP usando a documentação final e o checkpoint
funcional aprovado. Itens P1 e P2 permanecem congelados até a submissão; não há
nova fase funcional autorizada neste marco.

---

## 8. Débitos Preservados

Permanecem como limitações conhecidas do MVP:

- CI remota;
- concorrência e locks para operações persistentes;
- persistência de sessão apenas em memória;
- warning transitivo sobre `google-cloud-storage < 3.0.0`;
- quality gates adicionais ainda não configurados;
- Docker, deploy, métricas e observabilidade como itens P2 congelados.

Handoffs invisíveis e encerramento global não aparecem mais como débitos porque
foram concluídos e validados na Fase 1.

---

## 9. Regras de Atualização desta Memória

Ao concluir uma fase:

1. registrar somente o estado já mergeado em `main`;
2. informar SHA, PR e arquivos efetivamente publicados;
3. registrar comandos e resultados de validação;
4. preservar débitos e limitações sem convertê-los em sucesso;
5. apontar uma única próxima fase;
6. não incluir segredos, dados reais ou resultados sem evidência.
