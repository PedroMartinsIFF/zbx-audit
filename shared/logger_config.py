import logging
import logging.handlers
from pathlib import Path
from typing import Optional
import sys

class LoggerSetup:
    """Configuração centralizada de logging para a aplicação"""
    
    _logger_instance: Optional[logging.Logger] = None
    
    @staticmethod
    def get_logger(name: str = __name__, log_level: str = "INFO") -> logging.Logger:
        """
        Obtém ou cria um logger configurado
        
        Args:
            name: Nome do logger (geralmente __name__)
            log_level: Nível de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        
        Returns:
            Logger configurado
        """
        logger = logging.getLogger(name)
        
        # Evitar duplicação de handlers
        if logger.handlers:
            return logger
        
        # Definir nível
        logger.setLevel(getattr(logging, log_level))
        
        # Criar diretório de logs se não existir
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # Formatter comum
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Handler para console
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level))
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # Handler para arquivo (rotating)
        log_file = logs_dir / "zbx-audit.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5  # Manter 5 backups
        )
        file_handler.setLevel(logging.DEBUG)  # Arquivo sempre em DEBUG
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Handler para erros (arquivo separado)
        error_log_file = logs_dir / "zbx-audit-errors.log"
        error_handler = logging.handlers.RotatingFileHandler(
            error_log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
        
        return logger

# Logger global padrão
logger = LoggerSetup.get_logger(__name__)
