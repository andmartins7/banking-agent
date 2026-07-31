# Script de execução do Banco Ágil
# Uso: .\run.ps1

Write-Host "🏦 Iniciando Banco Ágil..." -ForegroundColor Cyan

# Verificar .env
if (-not (Test-Path ".env")) {
    Write-Host "❌ Arquivo .env não encontrado!" -ForegroundColor Red
    Write-Host "   Execute: copy .env.example .env" -ForegroundColor Yellow
    Write-Host "   Depois preencha GOOGLE_API_KEY no arquivo .env" -ForegroundColor Yellow
    exit 1
}

# Verificar CSVs
if (-not (Test-Path "data\clientes.csv")) {
    Write-Host "📦 Inicializando dados..." -ForegroundColor Yellow
    python data/seed.py
}

# Iniciar app
Write-Host "🚀 Abrindo em http://localhost:8501" -ForegroundColor Green
python -m streamlit run app.py
