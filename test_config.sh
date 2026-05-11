#!/bin/bash

# Script para verificar configuração do zbx-audit
# Uso: bash test_config.sh

set -e

echo "🔍 Verificando Configuração do zbx-audit"
echo "=========================================="
echo ""

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Verificar se .env existe
echo "📋 1. Verificando arquivo .env..."
if [ -f .env ]; then
    echo -e "${GREEN}✅ Arquivo .env encontrado${NC}"
else
    echo -e "${RED}❌ Arquivo .env NÃO encontrado${NC}"
    echo "   Solução: cp .env.example .env"
    exit 1
fi
echo ""

# 2. Verificar variáveis obrigatórias
echo "🔐 2. Verificando variáveis obrigatórias..."
REQUIRED_VARS=("ZABBIX_URL" "ZABBIX_USER" "ZABBIX_PASSWORD" "DB_NAME" "DB_USER" "DB_PASSWORD")

source .env

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}❌ Variável '$var' não configurada${NC}"
    else
        echo -e "${GREEN}✅ $var configurado${NC}"
    fi
done
echo ""

# 3. Verificar Python
echo "🐍 3. Verificando Python..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo -e "${GREEN}✅ $PYTHON_VERSION${NC}"
else
    echo -e "${RED}❌ Python3 não encontrado${NC}"
    exit 1
fi
echo ""

# 4. Verificar dependências Python
echo "📦 4. Verificando dependências Python..."
REQUIRED_PACKAGES=("psycopg2" "requests" "pyzabbix" "dotenv")

for package in "${REQUIRED_PACKAGES[@]}"; do
    if python3 -c "import ${package//-/_}" 2>/dev/null; then
        echo -e "${GREEN}✅ $package${NC}"
    else
        echo -e "${YELLOW}⚠️  $package não instalado${NC}"
    fi
done
echo ""

# 5. Testar conexão com Zabbix
echo "🔗 5. Testando conexão com Zabbix..."
if python3 -c "
from config import ZABBIX_URL, ZABBIX_USER
import requests
try:
    response = requests.get(ZABBIX_URL, timeout=5, verify=False)
    print(f'✅ Zabbix acessível: {ZABBIX_URL}')
except Exception as e:
    print(f'❌ Erro: {str(e)}')
    exit(1)
" 2>/dev/null; then
    echo -e "${GREEN}✅ Conexão com Zabbix OK${NC}"
else
    echo -e "${RED}❌ Erro ao conectar com Zabbix${NC}"
fi
echo ""

# 6. Testar conexão com PostgreSQL
echo "🗄️  6. Testando conexão com PostgreSQL..."
if python3 -c "
import psycopg2
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

try:
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5
    )
    conn.close()
    print(f'✅ Banco acessível: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}')
except Exception as e:
    print(f'❌ Erro: {str(e)}')
    exit(1)
" 2>/dev/null; then
    echo -e "${GREEN}✅ Conexão com PostgreSQL OK${NC}"
else
    echo -e "${RED}❌ Erro ao conectar com PostgreSQL${NC}"
fi
echo ""

# 7. Testar Ollama (se configurado)
echo "🤖 7. Testando Ollama (opcional)..."
if python3 -c "
from config import OLLAMA_API_URL
import requests
try:
    response = requests.get(OLLAMA_API_URL.replace('/api/generate', '/api/tags'), timeout=5)
    if response.status_code == 200:
        print(f'✅ Ollama acessível: {OLLAMA_API_URL}')
    else:
        print(f'⚠️  Ollama respondeu com erro: {response.status_code}')
except Exception as e:
    print(f'⚠️  Ollama não acessível (pode estar desligado): {str(e)}')
" 2>/dev/null; then
    :
else
    :
fi
echo ""

echo "=========================================="
echo -e "${GREEN}✅ CONFIGURAÇÃO VALIDADA COM SUCESSO${NC}"
echo ""
echo "Próximos passos:"
echo "  1. python3 cli.py --init-db         (criar tabelas)"
echo "  2. python3 cli.py --show-stats      (ver status)"
echo "  3. python3 cli.py --group X --only-collect --hours 1  (coletar eventos)"
echo ""
