"""
Configurações centralizadas do sistema Banco Ágil.
Sem lógica — apenas constantes e valores configuráveis.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

CSV_CLIENTES    = DATA_DIR / "clientes.csv"
CSV_SCORE_LIMITE = DATA_DIR / "score_limite.csv"
CSV_SOLICITACOES = DATA_DIR / "solicitacoes_aumento_limite.csv"

# ── LLM ───────────────────────────────────────────────────────────────────
# gemini-2.0-flash encerrado em 01/06/2026
# gemini-3.5-flash com alta demanda intermitente — usar fallback como primário por ora
GEMINI_MODEL          = "gemini-3.1-flash-lite"
GEMINI_MODEL_FALLBACK = "gemini-3.5-flash"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# ── Autenticação ──────────────────────────────────────────────────────────
MAX_AUTH_ATTEMPTS = 3

# ── Pesos do Score de Crédito ─────────────────────────────────────────────
PESO_RENDA = 30

PESO_EMPREGO: dict[str, int] = {
    "formal":       300,
    "autonomo":     200,
    "desempregado":   0,
}

PESO_DEPENDENTES: dict[int, int] = {
    0: 100,
    1:  80,
    2:  60,
    3:  30,   # 3+ dependentes
}

PESO_DIVIDAS: dict[str, int] = {
    "sim": -100,
    "nao":  100,
}

SCORE_MIN = 0
SCORE_MAX = 1000

# ── Câmbio ─────────────────────────────────────────────────────────────────
# AwesomeAPI: gratuita, sem autenticação
# Endpoint: https://economia.awesomeapi.com.br/last/{moeda}-BRL
CAMBIO_API_BASE_URL = "https://economia.awesomeapi.com.br/last"

# Mapeamento: código ISO → código ISO (normalizado para maiúsculas)
MOEDAS_SUPORTADAS: dict[str, str] = {
    "USD": "USD",
    "EUR": "EUR",
    "GBP": "GBP",
    "JPY": "JPY",
    "BTC": "BTC",
    "CAD": "CAD",
    "AUD": "AUD",
    "CHF": "CHF",
    "ARS": "ARS",
}

# ── App ────────────────────────────────────────────────────────────────────
APP_NAME = "banco_agil"
