# Spec 05 — Agente de Entrevista de Crédito

## Status: Implementado

---

## 1. Visão Geral

O Agente de Entrevista de Crédito coleta cinco respostas financeiras e as
entrega a uma única operação determinística. A mesma operação valida os dados,
calcula o score pela política oficial e publica a atualização do cliente
autenticado em `clientes.csv`.

O LLM não calcula, fornece, recebe nem persiste diretamente o score. O valor
numérico do score também não é informado ao cliente.

**Arquivo do agente:** `agents/entrevista_credito.py`
**Ferramentas:** `tools/score_tools.py`

---

## 2. Responsabilidades

1. Fazer cinco perguntas, uma de cada vez e na ordem definida.
2. Enviar somente as cinco respostas para `processar_entrevista_credito`.
3. Pedir a correção do campo indicado quando a tool rejeitar uma resposta.
4. Em sucesso, informar apenas que o perfil foi atualizado.
5. Sinalizar o retorno ao fluxo de crédito sem afirmar aprovação.
6. Permitir o encerramento do atendimento a qualquer momento.

O estado conversacional das cinco perguntas e a comprovação automatizada do
handoff real permanecem fora do escopo desta especificação.

---

## 3. Perguntas da Entrevista

| # | Campo | Pergunta sugerida |
|---|---|---|
| 1 | `renda_mensal` | "Qual é a sua renda mensal aproximada em reais?" |
| 2 | `tipo_emprego` | "Você trabalha formalmente, é autônomo ou está desempregado?" |
| 3 | `despesas_fixas` | "Quais são suas despesas fixas mensais aproximadas?" |
| 4 | `num_dependentes` | "Quantas pessoas dependem financeiramente de você?" |
| 5 | `tem_dividas` | "Você possui dívidas ativas no momento?" |

Após coletar as cinco respostas, o agente chama uma única vez:

```text
processar_entrevista_credito(
    renda_mensal,
    tipo_emprego,
    despesas_fixas,
    num_dependentes,
    tem_dividas,
)
```

O CPF não é perguntado nem enviado: ele é derivado da sessão autenticada.

---

## 4. Tool Pública Consolidada

```python
def processar_entrevista_credito(
    renda_mensal: float,
    tipo_emprego: str,
    despesas_fixas: float,
    num_dependentes: int,
    tem_dividas: str,
    tool_context: ToolContext,
) -> dict:
    ...
```

A tool executa, nesta ordem:

1. autoriza a sessão e deriva o CPF autenticado;
2. valida e normaliza as cinco respostas;
3. calcula o score pela fórmula oficial;
4. limita o resultado ao intervalo de 0 a 1000;
5. exige exatamente um cliente correspondente ao CPF da sessão;
6. valida o score anteriormente persistido;
7. altera somente `score_credito` desse cliente;
8. publica `clientes.csv` atomicamente;
9. retorna sucesso somente após a publicação.

Estrutura pública do retorno:

```python
{
    "processado": bool,
    "perfil_atualizado": bool,
    "retornar_credito": bool,
    "campo_invalido": str | None,
    "erro": str | None,
}
```

O retorno não contém score calculado ou anterior, CPF, limite, status, fórmula,
pesos, componentes ou as respostas completas.

As funções `calcular_score` e `atualizar_score_cliente` permanecem no módulo
somente para compatibilidade interna. Elas não são tools do agente.

---

## 5. Validação e Normalização

### 5.1 Renda e despesas

Aceitam números finitos maiores ou iguais a zero, inclusive representação
numérica em texto. Rejeitam `None`, booleanos, texto não numérico, `NaN`,
infinito e números negativos. Uma falha não inicia leitura ou escrita do CSV.

### 5.2 Dependentes

Aceita somente número inteiro não negativo, inclusive representação inteira em
texto. Rejeita `None`, booleanos, texto não numérico, números negativos, `NaN`,
infinito e valores fracionários. Três ou mais dependentes usam a chave de peso
`3`.

### 5.3 Emprego

Somente as seguintes respostas são reconhecidas:

| Normalizado | Respostas aceitas |
|---|---|
| `formal` | formal, clt, empregado, registrado, carteira assinada |
| `autonomo` | autonomo, autônomo, mei, freelancer |
| `desempregado` | desempregado, sem emprego |

Uma resposta desconhecida é rejeitada. Não existe fallback implícito para
`desempregado`.

### 5.4 Dívidas

| Normalizado | Respostas aceitas |
|---|---|
| `sim` | sim, s, tenho, possuo, yes |
| `nao` | não, nao, n, não tenho, nao tenho, no |

Uma resposta desconhecida é rejeitada. Ela nunca é convertida implicitamente
em `nao`.

---

## 6. Fórmula Oficial

Os pesos são importados de `config.py` e não podem ser fornecidos ou
substituídos pelo LLM.

```python
score_raw = (
    (renda_mensal / (despesas_fixas + 1)) * PESO_RENDA
    + PESO_EMPREGO[tipo_emprego]
    + PESO_DEPENDENTES[min(num_dependentes, 3)]
    + PESO_DIVIDAS[tem_dividas]
)

score_final = max(0, min(1000, round(score_raw)))
```

Exemplos de referência internos:

- renda 5000, formal, despesas 2000, 1 dependente e sem dívidas: `555`;
- renda 1500, autônomo, despesas 1200, 3 dependentes e com dívidas: `167`;
- renda 0, desempregado, despesas 500, 0 dependentes e sem dívidas: `200`.

Esses valores podem ser verificados nos testes e no CSV temporário, mas não são
devolvidos ao modelo nem apresentados ao cliente.

---

## 7. Persistência

Antes da escrita, `clientes.csv` deve existir, conter os cabeçalhos `cpf` e
`score_credito`, possuir exatamente um cliente para o CPF autenticado e conter
um score anterior inteiro entre 0 e 1000.

A publicação:

- prepara um arquivo temporário no mesmo diretório do destino;
- usa UTF-8 e não grava índice do DataFrame;
- executa `flush` e `os.fsync` antes de fechar o temporário;
- fecha o arquivo antes de `os.replace`;
- remove o temporário após sucesso ou falha.

Falha de preparação ou publicação preserva os bytes anteriores do destino e
retorna erro controlado sem CPF ou stack trace.

---

## 8. Agente ADK

O agente expõe somente:

```text
processar_entrevista_credito
encerrar_atendimento
```

O schema destinado ao modelo contém exclusivamente:

```text
renda_mensal
tipo_emprego
despesas_fixas
num_dependentes
tem_dividas
```

`tool_context` é injetado pelo ADK e permanece oculto.

O prompt proíbe o agente de calcular, pedir, revelar ou encaminhar score. Quando
`campo_invalido` estiver preenchido, ele pede a correção desse campo. Quando a
operação for concluída, informa que o perfil foi atualizado e sinaliza o
retorno ao fluxo de crédito, sem declarar aprovação da solicitação.

---

## 9. Critérios de Aceitação

- [ ] Uma única tool pública consolida validação, cálculo e persistência.
- [ ] CPF e score não são argumentos controlados pelo LLM.
- [ ] O score não aparece no retorno público nem no schema do modelo.
- [ ] Normalizações desconhecidas são rejeitadas sem escrita.
- [ ] A fórmula e os pesos oficiais são usados pelo código.
- [ ] Somente o cliente autenticado tem `score_credito` atualizado.
- [ ] A publicação de `clientes.csv` é atômica por arquivo.
- [ ] Falhas preservam o arquivo anterior e não deixam temporários.
- [ ] O agente expõe somente a tool consolidada e o encerramento.
- [ ] O cliente é informado de que o perfil foi atualizado, sem receber o score.
- [ ] Não se afirma que o handoff real já foi testado.
