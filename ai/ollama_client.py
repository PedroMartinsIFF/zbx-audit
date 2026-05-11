import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger_config import LoggerSetup

try:
    from shared.config import OLLAMA_TIMEOUT
except Exception:
    OLLAMA_TIMEOUT = 600


logger = LoggerSetup.get_logger(__name__)


class OllamaClient:
    def __init__(self, model: str, api_url: str):
        self.model = model
        self.api_url = api_url

    @staticmethod
    def needs_output_rewrite(text: str) -> bool:
        if not text:
            return False
        suspicious_patterns = [
            r"```",
            r"\bimport\s+\w+",
            r"\bdef\s+\w+\(",
            r"\bclass\s+\w+",
            r"startActivity:",
            r"&quot;|&#39;|&gt;|&lt;",
            r"se houver\s+\*\*procedimento",
            r"inclua obrigatoriamente",
        ]
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in suspicious_patterns)

    @staticmethod
    def looks_truncated(text: str) -> bool:
        if not text:
            return False

        stripped = text.strip()
        if len(stripped) < 40:
            return False

        truncated_markers = [
            "**Ações Rec",
            "**Ação Imed",
            "Resumo Exec",
            "## 4. AÇÕES",
            "Match RAG:",
        ]
        if any(stripped.endswith(marker) for marker in truncated_markers):
            return True

        if stripped[-1] not in ".!?)]}\"":
            return True

        return False

    def generate(self, prompt: str, group_name: str, label: str, num_predict: int = 1200) -> str:
        logger.info(
            "🤖 Iniciando chamada Ollama | etapa=%s | grupo=%s | modelo=%s | timeout=%ss | prompt_chars=%s",
            label,
            group_name,
            self.model,
            OLLAMA_TIMEOUT,
            len(prompt or ""),
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
                "top_k": 30,
                "top_p": 0.8,
                "num_predict": num_predict,
            }
        }
        start_time = time.time()
        response = requests.post(self.api_url, json=payload, timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        elapsed = time.time() - start_time
        text = data.get("response", "Nenhuma resposta da IA.")
        logger.info(
            "✅ Ollama respondeu | etapa=%s | grupo=%s | duracao=%.1fs | response_chars=%s",
            label,
            group_name,
            elapsed,
            len(text or ""),
        )
        return text

    def generate_with_default_logging(
        self,
        prompt: str,
        group_name: str,
        use_toon: bool = False,
        num_predict: int = 1200,
    ) -> str:
        logger.info(
            "🤖 Iniciando chamada Ollama | grupo=%s | modelo=%s | timeout=%ss | prompt_chars=%s | toon=%s",
            group_name,
            self.model,
            OLLAMA_TIMEOUT,
            len(prompt or ""),
            use_toon,
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
                "top_k": 30,
                "top_p": 0.8,
                "num_predict": num_predict,
            }
        }

        start_time = time.time()
        response = requests.post(self.api_url, json=payload, timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        elapsed = time.time() - start_time
        text = data.get("response", "Nenhuma resposta da IA.")
        logger.info(
            "✅ Ollama respondeu | grupo=%s | duracao=%.1fs | response_chars=%s",
            group_name,
            elapsed,
            len(text or ""),
        )
        return text

    def rewrite_to_report_format(self, raw_response: str, group_name: str) -> str:
        rewrite_prompt = f"""
Reescreva a resposta abaixo para um relatório SRE em português, sem código e sem entidades HTML.

Regras obrigatórias:
- Não incluir blocos de código nem comandos.
- Não incluir pseudo-código.
- Não incluir HTML/entities.
- Manter texto objetivo e técnico.
- Tamanho máximo: 35 linhas.
- NÃO truncar a resposta; conclua todas as seções até o resumo executivo.

Grupo: {group_name}

Resposta original:
{raw_response}
"""
        logger.info(
            "🧹 Iniciando rewrite da resposta IA | grupo=%s | tamanho_resposta=%s chars",
            group_name,
            len(raw_response or ""),
        )

        payload = {
            "model": self.model,
            "prompt": rewrite_prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": 4096,
                "top_k": 20,
                "top_p": 0.7,
                "num_predict": 900,
            },
        }
        rewrite_start = time.time()
        response = requests.post(self.api_url, json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        rewritten = data.get("response", raw_response)
        logger.info(
            "✅ Rewrite concluído | grupo=%s | duracao=%.1fs | tamanho_rewrite=%s chars",
            group_name,
            time.time() - rewrite_start,
            len(rewritten or ""),
        )

        if self.looks_truncated(rewritten):
            logger.warning(
                "⚠️ Rewrite truncado detectado | grupo=%s | tamanho_atual=%s chars",
                group_name,
                len(rewritten or ""),
            )
            continuation_prompt = f"""
Continue e finalize o relatório abaixo exatamente do ponto onde ele parou.

Regras:
- Não recomeçar do início.
- Não adicionar código.
- Finalizar com resumo executivo completo.

Trecho atual:
{rewritten}
"""
            continuation_start = time.time()
            continuation_payload = {
                "model": self.model,
                "prompt": continuation_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": 4096,
                    "top_k": 20,
                    "top_p": 0.7,
                    "num_predict": 600,
                },
            }
            continuation_response = requests.post(self.api_url, json=continuation_payload, timeout=180)
            continuation_response.raise_for_status()
            continuation_data = continuation_response.json()
            continuation_text = continuation_data.get("response", "").strip()
            if continuation_text:
                rewritten = f"{rewritten.rstrip()}\n{continuation_text}"
            logger.info(
                "✅ Continuação do rewrite concluída | grupo=%s | duracao=%.1fs | tamanho_final=%s chars",
                group_name,
                time.time() - continuation_start,
                len(rewritten or ""),
            )

        return rewritten
