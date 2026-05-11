import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.db import get_db_connection
from shared.logger_config import LoggerSetup

logger = LoggerSetup.get_logger(__name__)


class RunbookIndexer:
    def __init__(self, ollama_api_url: str, embedding_model: str = "nomic-embed-text"):
        self.ollama_api_url = ollama_api_url
        self.embedding_model = embedding_model
        self.embedding_url = ollama_api_url.replace("generate", "embeddings")

    @staticmethod
    def _split_markdown_into_chunks(content: str, max_chars: int = 1800) -> List[str]:
        lines = content.splitlines()
        chunks: List[str] = []
        current: List[str] = []
        current_size = 0

        for line in lines:
            is_heading = line.strip().startswith("#")
            line_size = len(line) + 1

            if is_heading and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                current_size = line_size
                continue

            if current_size + line_size > max_chars and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                current_size = line_size
            else:
                current.append(line)
                current_size += line_size

        if current:
            chunks.append("\n".join(current).strip())

        return [chunk for chunk in chunks if chunk]

    def _embed_text(self, text: str) -> List[float]:
        response = requests.post(
            self.embedding_url,
            json={"model": self.embedding_model, "prompt": text},
            timeout=60,
        )
        response.raise_for_status()
        embedding = response.json().get("embedding")
        if not embedding:
            raise ValueError("Embedding não retornado pelo Ollama")
        return embedding

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_runbooks_table(cursor) -> None:
        cursor.execute("SELECT to_regclass('public.runbooks')")
        runbooks_table = cursor.fetchone()[0]
        if not runbooks_table:
            raise RuntimeError(
                "Tabela 'runbooks' não existe. Instale a extensão pgvector no PostgreSQL e execute a aplicação novamente para criar o schema de RAG."
            )

    def index_markdown_file(self, markdown_path: str, group_name: str = None) -> Tuple[int, int]:
        path = Path(markdown_path)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {markdown_path}")

        content = path.read_text(encoding="utf-8")
        chunks = self._split_markdown_into_chunks(content)
        if not chunks:
            return 0, 0

        source_path = str(path.resolve())
        inserted = 0
        updated = 0

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                self._ensure_runbooks_table(cursor)
                for idx, chunk in enumerate(chunks):
                    chunk_hash = self._hash_text(chunk)

                    cursor.execute(
                        """
                        SELECT content_hash, group_name
                        FROM runbooks
                        WHERE source_path = %s AND chunk_index = %s
                        """,
                        (source_path, idx),
                    )
                    existing = cursor.fetchone()
                    if existing and existing[0] == chunk_hash:
                        existing_group_name = existing[1]
                        if existing_group_name != group_name:
                            cursor.execute(
                                """
                                UPDATE runbooks
                                SET group_name = %s, updated_at = NOW()
                                WHERE source_path = %s AND chunk_index = %s
                                """,
                                (group_name, source_path, idx),
                            )
                            updated += 1
                        continue

                    embedding = self._embed_text(chunk)
                    title = path.stem

                    cursor.execute(
                        """
                        INSERT INTO runbooks (source_path, title, chunk_index, content, embedding, content_hash, updated_at)
                        VALUES (%s, %s, %s, %s, %s::vector, %s, NOW())
                        ON CONFLICT (source_path, chunk_index)
                        DO UPDATE SET
                            title = EXCLUDED.title,
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            content_hash = EXCLUDED.content_hash,
                            updated_at = NOW()
                        """,
                        (source_path, title, idx, chunk, str(embedding), chunk_hash),
                    )

                    cursor.execute(
                        """
                        UPDATE runbooks
                        SET group_name = %s
                        WHERE source_path = %s AND chunk_index = %s
                        """,
                        (group_name, source_path, idx),
                    )

                    if existing:
                        updated += 1
                    else:
                        inserted += 1

                cursor.execute(
                    "DELETE FROM runbooks WHERE source_path = %s AND chunk_index >= %s",
                    (source_path, len(chunks)),
                )

        logger.info("✅ Runbook indexado: %s (chunks: %s, inseridos: %s, atualizados: %s)", source_path, len(chunks), inserted, updated)
        return inserted, updated

    def index_markdown_directory(self, docs_dir: str, group_name: str = None) -> Tuple[int, int, int]:
        directory = Path(docs_dir)
        if not directory.exists() or not directory.is_dir():
            raise NotADirectoryError(f"Diretório inválido: {docs_dir}")

        md_files = sorted(directory.rglob("*.md"))
        if not md_files:
            logger.warning("Nenhum arquivo .md encontrado em %s", docs_dir)
            return 0, 0, 0

        total_inserted = 0
        total_updated = 0
        processed = 0

        for file_path in md_files:
            inserted, updated = self.index_markdown_file(str(file_path), group_name=group_name)
            processed += 1
            total_inserted += inserted
            total_updated += updated

        logger.info(
            "📚 Indexação concluída: %s arquivos, %s chunks inseridos, %s chunks atualizados",
            processed,
            total_inserted,
            total_updated,
        )
        return processed, total_inserted, total_updated
