import sys
from pathlib import Path
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
from typing import Generator

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_POOL_MIN, DB_POOL_MAX
from shared.logger_config import LoggerSetup

logger = LoggerSetup.get_logger(__name__)

# Connection pool global
_connection_pool = None

def init_connection_pool():
    """Inicializa connection pool para melhor performance"""
    global _connection_pool
    
    if _connection_pool is not None:
        logger.debug("Connection pool já inicializado")
        return
    
    try:
        _connection_pool = psycopg2.pool.ThreadedConnectionPool(
            DB_POOL_MIN,
            DB_POOL_MAX,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            connect_timeout=5
        )
        logger.info(f"✅ Connection pool inicializado: {DB_POOL_MIN}-{DB_POOL_MAX} conexões")
    except psycopg2.OperationalError as e:
        logger.critical(f"❌ Erro ao criar connection pool: {e}")
        raise
    except Exception as e:
        logger.critical(f"❌ Erro inesperado ao inicializar pool: {e}")
        raise

@contextmanager
def get_db_connection() -> Generator:
    """
    Context manager para obter conexão do pool
    
    Uso:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(...)
    
    Yields:
        Conexão PostgreSQL
    """
    if _connection_pool is None:
        logger.debug("Pool não inicializado, inicializando agora")
        init_connection_pool()
    
    conn = None
    try:
        conn = _connection_pool.getconn()
        logger.debug("Conexão obtida do pool")
        yield conn
        conn.commit()
        logger.debug("Transação commitada com sucesso")
    except psycopg2.DatabaseError as e:
        if conn:
            conn.rollback()
            logger.error(f"Erro de banco de dados, transação revertida: {e}")
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Erro inesperado na transação, revertida: {e}")
        raise
    finally:
        if conn:
            _connection_pool.putconn(conn)
            logger.debug("Conexão retornada ao pool")

def close_connection_pool():
    """Fecha connection pool gracefully"""
    global _connection_pool
    try:
        if _connection_pool:
            _connection_pool.closeall()
            _connection_pool = None
            logger.info("✅ Connection pool fechado com sucesso")
    except Exception as e:
        logger.error(f"Erro ao fechar connection pool: {e}")
        raise

def setup_database():
    """Cria schema do banco de dados"""
    try:
        logger.info("Inicializando banco de dados...")
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                logger.debug("Criando tabela 'events'...")
                has_vector_support = False
                try:
                    cursor.execute("SAVEPOINT vector_extension_check")
                    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cursor.execute("RELEASE SAVEPOINT vector_extension_check")
                    has_vector_support = True
                    logger.debug("Extensão pgvector garantida")
                except psycopg2.Error as e:
                    cursor.execute("ROLLBACK TO SAVEPOINT vector_extension_check")
                    logger.warning(f"Não foi possível garantir extensão 'vector': {e}")

                cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    timestamp BIGINT NOT NULL,
                    host_name TEXT NOT NULL,
                    hostgroups JSONB NOT NULL,
                    proxy_name TEXT,
                    severity INTEGER NOT NULL,
                    problem_name TEXT NOT NULL,
                    is_control_group BOOLEAN DEFAULT FALSE,
                    correlation_window TEXT,
                    event_status TEXT CHECK (event_status IN ('Problem', 'Recovery')),
                    event_duration INTEGER CHECK (event_duration >= 0),
                    r_eventid TEXT,
                    runbook_context TEXT,
                    created_at BIGINT DEFAULT extract(epoch from now()),
                    updated_at BIGINT DEFAULT extract(epoch from now())
                )
                """)
                logger.debug("Tabela 'events' criada/verificada")

                if has_vector_support:
                    logger.debug("Criando tabela 'runbooks'...")
                    cursor.execute("""
                    CREATE TABLE IF NOT EXISTS runbooks (
                        id SERIAL PRIMARY KEY,
                        group_name TEXT,
                        source_path TEXT NOT NULL,
                        title TEXT,
                        chunk_index INTEGER NOT NULL DEFAULT 0,
                        content TEXT NOT NULL,
                        embedding vector(768) NOT NULL,
                        content_hash TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE (source_path, chunk_index)
                    )
                    """)
                    logger.debug("Tabela 'runbooks' criada/verificada")

                    cursor.execute("""
                    ALTER TABLE runbooks
                        ADD COLUMN IF NOT EXISTS group_name TEXT
                    """)
                    logger.debug("Coluna 'group_name' garantida na tabela 'runbooks'")
                else:
                    logger.warning("Tabela 'runbooks' não criada: extensão pgvector indisponível")

                logger.debug("Garantindo colunas novas na tabela 'events'...")
                cursor.execute("""
                ALTER TABLE events
                    ADD COLUMN IF NOT EXISTS event_duration INTEGER,
                    ADD COLUMN IF NOT EXISTS r_eventid TEXT,
                    ADD COLUMN IF NOT EXISTS runbook_context TEXT
                """)
                logger.debug("Colunas 'event_duration' e 'r_eventid' garantidas")

                logger.debug("Criando tabela 'ollama_response'...")
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS ollama_response (
                    id SERIAL PRIMARY KEY,
                    timestamp BIGINT DEFAULT extract(epoch from now()),
                    groupname TEXT NOT NULL,
                    response TEXT NOT NULL,
                    ai_prompt TEXT,
                    model TEXT,
                    classification TEXT,
                    risk_level TEXT,
                    main_problem TEXT,
                    summary TEXT,
                    recommended_actions JSONB,
                    metrics_snapshot JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """)
                logger.debug("Tabela 'ollama_response' criada/verificada")

                logger.debug("Garantindo colunas estruturadas na tabela 'ollama_response'...")
                cursor.execute("""
                ALTER TABLE ollama_response
                    ADD COLUMN IF NOT EXISTS model TEXT
                """)
                cursor.execute("""
                ALTER TABLE ollama_response
                    ADD COLUMN IF NOT EXISTS ai_prompt TEXT,
                    ADD COLUMN IF NOT EXISTS classification TEXT,
                    ADD COLUMN IF NOT EXISTS risk_level TEXT,
                    ADD COLUMN IF NOT EXISTS main_problem TEXT,
                    ADD COLUMN IF NOT EXISTS summary TEXT,
                    ADD COLUMN IF NOT EXISTS recommended_actions JSONB,
                    ADD COLUMN IF NOT EXISTS metrics_snapshot JSONB
                """)
                logger.debug("Colunas estruturadas garantidas")

                # Criar índices
                logger.debug("Criando índices otimizados...")
                indexes = [
                    ("idx_events_timestamp", "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC)"),
                    ("idx_events_hostgroups_gin", "CREATE INDEX IF NOT EXISTS idx_events_hostgroups_gin ON events USING gin(hostgroups)"),
                    ("idx_events_proxy", "CREATE INDEX IF NOT EXISTS idx_events_proxy ON events(proxy_name) WHERE proxy_name IS NOT NULL"),
                    ("idx_events_severity", "CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity)"),
                    ("idx_events_status", "CREATE INDEX IF NOT EXISTS idx_events_status ON events(event_status)"),
                    ("idx_events_duration", "CREATE INDEX IF NOT EXISTS idx_events_duration ON events(event_duration) WHERE event_duration IS NOT NULL"),
                    ("idx_events_composite", "CREATE INDEX IF NOT EXISTS idx_events_composite ON events(timestamp, event_status, is_control_group)"),
                    ("idx_ollama_groupname", "CREATE INDEX IF NOT EXISTS idx_ollama_groupname ON ollama_response(groupname, timestamp DESC)"),
                ]

                if has_vector_support:
                    indexes.append(("idx_runbooks_source", "CREATE INDEX IF NOT EXISTS idx_runbooks_source ON runbooks(source_path, chunk_index)"))
                    indexes.append(("idx_runbooks_group", "CREATE INDEX IF NOT EXISTS idx_runbooks_group ON runbooks(group_name)"))
                
                for idx_name, idx_sql in indexes:
                    try:
                        cursor.execute("SAVEPOINT idx_creation")
                        cursor.execute(idx_sql)
                        cursor.execute("RELEASE SAVEPOINT idx_creation")
                        logger.debug(f"Índice '{idx_name}' criado/verificado")
                    except psycopg2.Error as e:
                        cursor.execute("ROLLBACK TO SAVEPOINT idx_creation")
                        logger.warning(f"Erro ao criar índice '{idx_name}': {e}")
                
        logger.info("✅ Banco de dados inicializado com sucesso")
        
    except psycopg2.DatabaseError as e:
        logger.critical(f"❌ Erro de banco de dados ao inicializar: {e}")
        raise
    except Exception as e:
        logger.critical(f"❌ Erro inesperado ao inicializar banco: {e}")
        raise
