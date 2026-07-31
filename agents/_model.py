"""
Modelo LLM ativo para os agentes.

Lê GEMINI_MODEL de config.py — sem chamadas de rede no import.
Para trocar de modelo, altere GEMINI_MODEL em config.py.
"""

from config import GEMINI_MODEL

MODELO_ATIVO = GEMINI_MODEL
