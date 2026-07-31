"""
Script de inicialização dos dados do Banco Ágil.

Uso:
    python data/seed.py           # Cria apenas se não existir
    python data/seed.py --force   # Recria mesmo se já existir

Idempotente sem --force: não sobrescreve arquivos existentes.
"""

import sys
from pathlib import Path

# Permite importar config mesmo ao rodar como script
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd  # noqa: E402
from config import CSV_CLIENTES, CSV_SCORE_LIMITE, CSV_SOLICITACOES, DATA_DIR  # noqa: E402


def criar_clientes(force: bool = False) -> None:
    path = CSV_CLIENTES
    if path.exists() and not force:
        print(f"[seed] {path.name} já existe. Use --force para recriar.")
        return

    dados = {
        "cpf": [
            "12345678901",
            "98765432100",
            "11122233344",
            "55566677788",
            "99988877766",
        ],
        "nome": [
            "João Silva",
            "Maria Oliveira",
            "Carlos Mendes",
            "Ana Souza",
            "Pedro Costa",
        ],
        "data_nascimento": [
            "15/03/1985",
            "22/07/1990",
            "08/11/1978",
            "30/01/1995",
            "14/06/1982",
        ],
        "score_credito": [750, 420, 600, 850, 300],
        "limite_credito": [5000.00, 1500.00, 3000.00, 10000.00, 500.00],
    }
    pd.DataFrame(dados).to_csv(path, index=False)
    print(f"[seed] {path.name} criado com {len(dados['cpf'])} clientes.")


def criar_score_limite(force: bool = False) -> None:
    path = CSV_SCORE_LIMITE
    if path.exists() and not force:
        print(f"[seed] {path.name} já existe. Use --force para recriar.")
        return

    dados = {
        "limite_maximo": [
            1000.00,
            2000.00,
            5000.00,
            10000.00,
            20000.00,
            50000.00,
            999999.99,
        ],
        "score_minimo": [200, 300, 450, 600, 750, 850, 950],
    }
    pd.DataFrame(dados).to_csv(path, index=False)
    print(f"[seed] {path.name} criado com {len(dados['limite_maximo'])} faixas.")


def criar_solicitacoes(force: bool = False) -> None:
    path = CSV_SOLICITACOES
    if path.exists() and not force:
        print(f"[seed] {path.name} já existe. Use --force para recriar.")
        return

    colunas = [
        "cpf_cliente",
        "data_hora_solicitacao",
        "limite_atual",
        "novo_limite_solicitado",
        "status_pedido",
    ]
    pd.DataFrame(columns=colunas).to_csv(path, index=False)
    print(f"[seed] {path.name} criado (vazio, apenas cabeçalho).")


if __name__ == "__main__":
    force = "--force" in sys.argv

    # Garantir que o diretório data/ existe
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    criar_clientes(force)
    criar_score_limite(force)
    criar_solicitacoes(force)

    print("[seed] Concluído.")
