import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger_config import LoggerSetup

try:
    from db.db import get_db_connection
except Exception:
    get_db_connection = None


logger = LoggerSetup.get_logger(__name__)


class RagSupport:
    def __init__(self, api_url: str):
        self.api_url = api_url

    def embedding_endpoints(self):
        if "/api/" in self.api_url:
            base = self.api_url.split("/api/")[0]
            return [f"{base}/api/embeddings", f"{base}/api/embed"]
        return [
            self.api_url.replace("generate", "embeddings"),
            self.api_url.replace("generate", "embed"),
        ]

    def fetch_embedding(self, text: str):
        for endpoint in self.embedding_endpoints():
            try:
                payload = {"model": "nomic-embed-text", "prompt": text}
                if endpoint.endswith("/embed"):
                    payload = {"model": "nomic-embed-text", "input": text}

                response = requests.post(endpoint, json=payload, timeout=30)
                if response.status_code == 404:
                    continue

                response.raise_for_status()
                data = response.json()
                embedding = data.get("embedding")
                if not embedding:
                    embeddings = data.get("embeddings")
                    if isinstance(embeddings, list) and embeddings:
                        embedding = embeddings[0]

                if embedding:
                    return embedding
            except Exception:
                continue
        return None

    def get_relevant_runbook(self, problem_description: str, group_name: str = None) -> str:
        if not problem_description or get_db_connection is None:
            return ""

        try:
            embed = self.fetch_embedding(problem_description)
            if not embed:
                return ""

            # Normaliza group_name para evitar misses por espaços extras.
            normalized_group_name = (group_name or "").strip() or None

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    if normalized_group_name:
                        cur.execute(
                            """
                            SELECT content
                            FROM runbooks
                            WHERE group_name = %s
                              AND (
                                    content ILIKE '%%Formato do Problema%%'
                                 OR content ILIKE '%%Procedimento de resolução%%'
                              )
                            ORDER BY embedding <=> %s::vector
                            LIMIT 1
                            """,
                            (normalized_group_name, json.dumps(embed)),
                        )
                        result = cur.fetchone()
                        if result:
                            return result[0]

                        cur.execute(
                            """
                            SELECT content
                            FROM runbooks
                            WHERE group_name = %s
                            ORDER BY embedding <=> %s::vector
                            LIMIT 1
                            """,
                            (normalized_group_name, json.dumps(embed)),
                        )
                        result = cur.fetchone()
                        if result:
                            return result[0]
                        # Fallback para runbook global quando não houver match no grupo.

                    cur.execute(
                        """
                        SELECT content
                        FROM runbooks
                        WHERE group_name IS NULL
                          AND (
                                content ILIKE '%%Formato do Problema%%'
                             OR content ILIKE '%%Procedimento de resolução%%'
                          )
                        ORDER BY embedding <=> %s::vector
                        LIMIT 1
                        """,
                        (json.dumps(embed),),
                    )
                    result = cur.fetchone()
                    if result:
                        return result[0]

                    cur.execute(
                        "SELECT content FROM runbooks WHERE group_name IS NULL ORDER BY embedding <=> %s::vector LIMIT 1",
                        (json.dumps(embed),),
                    )
                    result = cur.fetchone()
                    return result[0] if result else ""
        except Exception:
            return ""

    def get_relevant_runbook_candidates(self, problem_description: str, group_name: str = None, limit: int = 5) -> list[str]:
        if not problem_description or get_db_connection is None:
            return []

        try:
            embed = self.fetch_embedding(problem_description)
            if not embed:
                return []

            normalized_group_name = (group_name or "").strip() or None
            candidates: list[str] = []
            seen = set()

            def _append_rows(rows):
                for row in rows or []:
                    if not row:
                        continue
                    content = row[0]
                    if not content:
                        continue
                    key = content[:500]
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(content)

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    if normalized_group_name:
                        cur.execute(
                            """
                            SELECT content
                            FROM runbooks
                            WHERE group_name = %s
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                            """,
                            (normalized_group_name, json.dumps(embed), limit),
                        )
                        _append_rows(cur.fetchall())

                    cur.execute(
                        """
                        SELECT content
                        FROM runbooks
                        WHERE group_name IS NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (json.dumps(embed), limit),
                    )
                    _append_rows(cur.fetchall())

            return candidates
        except Exception:
            return []

    @staticmethod
    def normalize_text(text: str) -> str:
        text = (text or "").lower()
        text = re.sub(r"[\W_]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def extract_runbook_rules(self, context_runbook: str):
        if not context_runbook:
            return []
        rules = []

        problem_header_count = len(
            re.findall(r"\*\*Formato do Problema\*\*:", context_runbook, flags=re.IGNORECASE)
        )
        procedure_header_count = len(
            re.findall(r"\*\*Procedimento de resolu[cç][aã]o:\*\*", context_runbook, flags=re.IGNORECASE)
        )

        # Caso 1: formato por família, com múltiplos padrões compartilhando um único procedimento.
        # Esse caso precisa vir primeiro para não ser capturado incorretamente pelo parser legado.
        if problem_header_count > 1 and procedure_header_count == 1:
            family_problem_pattern = re.compile(
                r"-\s*\*\*Formato do Problema\*\*:\s*(.+?)(?=\n|$)",
                flags=re.IGNORECASE,
            )
            procedure_pattern = re.compile(
                r"\*\*Procedimento de resolu[cç][aã]o:\*\*\s*(.*?)(?=\n##\s|\Z)",
                flags=re.IGNORECASE | re.DOTALL,
            )

            family_problems = [
                match.replace("XXXX", " ").replace("XXXXX", " ").strip()
                for match in family_problem_pattern.findall(context_runbook)
                if match and match.strip()
            ]
            procedure_match = procedure_pattern.search(context_runbook)
            clean_procedure = re.sub(r"\s+", " ", procedure_match.group(1)).strip() if procedure_match else ""

            if family_problems and clean_procedure:
                for problem_pattern in family_problems:
                    rules.append((problem_pattern, clean_procedure))
                return rules

        # Caso 1: formato legado, com blocos repetidos de problema + procedimento.
        legacy_pattern = re.compile(
            r"-\s*\*\*Formato do Problema\*\*:\s*(.*?)\n\*\*Procedimento de resolu[cç][aã]o:\*\*\s*(.*?)(?=\n-\s*\*\*Formato do Problema\*\*:|\n##\s|\Z)",
            flags=re.IGNORECASE | re.DOTALL,
        )

        for problem_pattern, procedure in legacy_pattern.findall(context_runbook):
            clean_problem_pattern = problem_pattern.replace("XXXX", " ").replace("XXXXX", " ").strip()
            clean_procedure = re.sub(r"\s+", " ", procedure).strip()
            if clean_problem_pattern and clean_procedure:
                rules.append((clean_problem_pattern, clean_procedure))

        if rules:
            return rules

        return rules

    @staticmethod
    def collect_problem_candidates(metrics_group: dict):
        candidates = []
        seen = set()

        def _append(problem_name):
            if not problem_name:
                return
            normalized = str(problem_name).strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        # Prioridade 1: problemas associados aos hosts mais relevantes do grupo.
        top_problem_details = metrics_group.get("top_problem_details") or []
        for item in top_problem_details:
            if not isinstance(item, dict):
                continue
            affected_entities = item.get("affected_entities") or []
            if not affected_entities:
                continue
            _append(item.get("problem_name"))

        # Prioridade 2: problemas já associados aos candidatos host-centric, quando presentes.
        for candidate in metrics_group.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            for problem in candidate.get("top_host_problems") or []:
                if isinstance(problem, dict):
                    _append(problem.get("problem_name"))

        top_problems = metrics_group.get("top_problems") or []
        for item in top_problems:
            if isinstance(item, dict):
                problem_name = item.get("name") or item.get("problem") or item.get("problem_name")
                _append(problem_name)
            elif isinstance(item, (list, tuple)) and item:
                _append(item[0])
            elif isinstance(item, str):
                _append(item)

        active_events = metrics_group.get("active_events") or []
        for item in active_events:
            if isinstance(item, dict):
                problem_name = item.get("problem") or item.get("problem_name")
                _append(problem_name)

        return candidates

    def select_rag_match(self, metrics_group: dict, context_runbook: str):
        rules = self.extract_runbook_rules(context_runbook)
        if not rules:
            return None

        candidates = self.collect_problem_candidates(metrics_group)
        if not candidates:
            return None

        best_match = None
        best_score = 0

        for candidate in candidates:
            candidate_norm = self.normalize_text(candidate)
            candidate_tokens = set(candidate_norm.split())
            if not candidate_tokens:
                continue

            for problem_pattern, procedure in rules:
                pattern_norm = self.normalize_text(problem_pattern)
                pattern_tokens = set(pattern_norm.split())
                if not pattern_tokens:
                    continue

                score = len(candidate_tokens.intersection(pattern_tokens))
                if score > best_score:
                    best_score = score
                    best_match = {
                        "problem": candidate,
                        "pattern": problem_pattern,
                        "procedure": procedure,
                        "score": score,
                    }

        if best_match and best_match["score"] >= 2:
            return best_match
        return None
