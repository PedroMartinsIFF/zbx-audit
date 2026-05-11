import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from shared.logger_config import LoggerSetup

# Carrega variáveis do arquivo .env
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)
else:
    print(f"⚠️  Arquivo .env não encontrado em {env_path}")
    print(f"📋 Copie .env.example para .env e configure as variáveis")

logger = LoggerSetup.get_logger(__name__)

class ConfigError(Exception):
    """Erro de configuração da aplicação"""
    pass

def get_required_env(key: str) -> str:
    """
    Obtém variável de ambiente obrigatória
    
    Args:
        key: Nome da variável
    
    Raises:
        ConfigError: Se variável não estiver definida
    
    Returns:
        Valor da variável
    """
    value = os.getenv(key)
    if not value:
        error_msg = f"Variável de ambiente obrigatória '{key}' não definida. Configure e tente novamente."
        logger.critical(error_msg)
        raise ConfigError(error_msg)
    return value

def get_optional_env(key: str, default: str = "") -> str:
    """
    Obtém variável de ambiente opcional com valor padrão
    
    Args:
        key: Nome da variável
        default: Valor padrão se não definida
    
    Returns:
        Valor da variável ou default
    """
    value = os.getenv(key, default)
    if not value and not default:
        logger.warning(f"Variável opcional '{key}' não definida, usando vazio")
    return value


def reload_env_file() -> None:
    if env_path.exists():
        load_dotenv(env_path, override=True)


def get_runtime_ollama_settings() -> tuple[str, str, int]:
    reload_env_file()
    model = get_optional_env("OLLAMA_MODEL", "phi3:mini")
    api_url = get_optional_env("OLLAMA_API_URL", "http://localhost:11434/api/generate")
    timeout = int(get_optional_env("OLLAMA_TIMEOUT", "600"))
    return model, api_url, timeout

# ============================================
# VARIÁVEIS DE AMBIENTE OBRIGATÓRIAS
# ============================================

try:
    # Zabbix
    ZABBIX_URL = get_required_env("ZABBIX_URL")
    ZABBIX_USER = get_required_env("ZABBIX_USER")
    ZABBIX_PASSWORD = get_required_env("ZABBIX_PASSWORD")
    
    # Banco de Dados
    DB_NAME = get_required_env("DB_NAME")
    DB_USER = get_required_env("DB_USER")
    DB_PASSWORD = get_required_env("DB_PASSWORD")
    
    logger.info("✅ Todas as variáveis obrigatórias foram carregadas com sucesso")
    
except ConfigError as e:
    logger.critical(f"Falha ao carregar configuração: {e}")
    sys.exit(1)

# ============================================
# VARIÁVEIS DE AMBIENTE OPCIONAIS
# ============================================

# Banco de Dados (com defaults seguros)
DB_HOST = get_optional_env("DB_HOST", "localhost")
DB_PORT = get_optional_env("DB_PORT", "5432")

# Batch size para coleta
BATCH_SIZE = int(get_optional_env("BATCH_SIZE", "1000"))

# Ollama/IA
OLLAMA_MODEL = get_optional_env("OLLAMA_MODEL", "phi3:mini")
OLLAMA_API_URL = get_optional_env("OLLAMA_API_URL", "http://localhost:11434/api/generate")
OLLAMA_TIMEOUT = int(get_optional_env("OLLAMA_TIMEOUT", "600"))

# Logging
LOG_LEVEL = get_optional_env("LOG_LEVEL", "INFO")

# Banco de Dados - Pools
DB_POOL_MIN = int(get_optional_env("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(get_optional_env("DB_POOL_MAX", "10"))

logger.debug(f"Configurações carregadas:")
logger.debug(f"  Zabbix URL: {ZABBIX_URL}")
logger.debug(f"  Database: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
logger.debug(f"  Ollama Model: {OLLAMA_MODEL}")
logger.debug(f"  Log Level: {LOG_LEVEL}")
