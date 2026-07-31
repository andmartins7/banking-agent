"""
Ferramentas de câmbio do Banco Ágil.

Funções expostas como tools do Google ADK:
    - buscar_cotacao
"""

import httpx
from datetime import datetime
from config import CAMBIO_API_BASE_URL, MOEDAS_SUPORTADAS


def buscar_cotacao(codigo_moeda: str) -> dict:
    """
    Busca a cotação atual de uma moeda estrangeira em relação ao Real (BRL).

    Utiliza a AwesomeAPI (https://economia.awesomeapi.com.br) — gratuita, sem autenticação.

    Args:
        codigo_moeda: Código ISO da moeda (ex: 'USD', 'EUR', 'GBP').
                      Case-insensitive; normalizado internamente para maiúsculas.

    Returns:
        dict com:
            sucesso (bool): True se cotação obtida com sucesso.
            moeda_codigo (str): código normalizado (ex: 'USD').
            moeda_nome (str): nome completo do par (ex: 'Dólar Americano/Real Brasileiro').
            cotacao_compra (float): valor bid (banco compra).
            cotacao_venda (float): valor ask (banco vende).
            variacao_pct (float): variação percentual no dia.
            data_atualizacao (str): data/hora formatada 'DD/MM/AAAA às HH:MM'.
            erro (str | None): mensagem de erro se sucesso=False.
    """
    _vazio = {
        "sucesso": False,
        "moeda_codigo": "",
        "moeda_nome": "",
        "cotacao_compra": 0.0,
        "cotacao_venda": 0.0,
        "variacao_pct": 0.0,
        "data_atualizacao": "",
    }

    try:
        # 1. Normalizar código
        codigo = codigo_moeda.strip().upper()

        # 2. Verificar suporte
        if codigo not in MOEDAS_SUPORTADAS:
            opcoes = ", ".join(sorted(MOEDAS_SUPORTADAS.keys()))
            return {**_vazio, "erro": f"Moeda '{codigo}' não suportada. Disponíveis: {opcoes}"}

        # 3. Montar URL e fazer requisição
        url = f"{CAMBIO_API_BASE_URL}/{codigo}-BRL"
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()

        # 4. Parsear JSON
        dados = response.json()
        chave = f"{codigo}BRL"
        if chave not in dados:
            return {**_vazio, "erro": "Resposta inesperada da API de câmbio."}

        info = dados[chave]

        # 5. Formatar data
        data_atualizacao = ""
        try:
            dt = datetime.strptime(info["create_date"], "%Y-%m-%d %H:%M:%S")
            data_atualizacao = dt.strftime("%d/%m/%Y às %H:%M")
        except (KeyError, ValueError):
            data_atualizacao = "indisponível"

        # 6. Extrair e converter campos
        cotacao_compra = float(info.get("bid", 0))
        cotacao_venda  = float(info.get("ask", 0))
        variacao_pct   = float(info.get("pctChange", 0))
        moeda_nome     = info.get("name", codigo)

        return {
            "sucesso": True,
            "moeda_codigo": codigo,
            "moeda_nome": moeda_nome,
            "cotacao_compra": cotacao_compra,
            "cotacao_venda": cotacao_venda,
            "variacao_pct": variacao_pct,
            "data_atualizacao": data_atualizacao,
            "erro": None,
        }

    except httpx.TimeoutException:
        return {**_vazio, "erro": "API de câmbio indisponível (timeout). Tente novamente em instantes."}
    except httpx.HTTPStatusError as e:
        return {**_vazio, "erro": f"Erro ao consultar API de câmbio (HTTP {e.response.status_code})."}
    except (KeyError, ValueError) as e:
        print(f"[TOOL ERROR] buscar_cotacao parse: {type(e).__name__}: {e}")
        return {**_vazio, "erro": "Erro ao processar resposta da API de câmbio."}
    except Exception as e:
        print(f"[TOOL ERROR] buscar_cotacao: {type(e).__name__}: {e}")
        return {**_vazio, "erro": "Erro inesperado ao consultar câmbio. Tente novamente."}
