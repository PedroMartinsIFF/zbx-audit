import csv
from langdetect import detect, LangDetectException
from langdetect import DetectorFactory

import subprocess

import requests
import json


DetectorFactory.seed = 0

TARGET_COLUMNS = {"response", "ai_prompt"}
ALLOWED_LANGS = {"pt", "en"}
ID_COLUMN = "id"

MODEL = "llama3.2:3b"  # ajuste para o modelo que você usa


JUDGE_PROMPT_TEMPLATE = """
Você é um auditor.

Voc6e receberá:
1) Um PROMPT contendo os dados de entrada.
2) Uma RESPONSE gerada por um assistente.

Tarefa:

Analise a RESPONSE com base no PROMPT e classifique-a em uma das seguintes categorias:
- SUPPORTED: A resposta é adequada, relevante e atende ao prompt.
- UNSUPPORTED: A resposta é inadequada, irrelevante ou não atende ao prompt.
- INSUFFICIENT_CONTEXT: A resposta é muito vaga ou genérica, sem informações suficientes

PROMPT:
{prompt}

RESPONSE:
{response}
"""




# -------------------------
# Detector CJK (ultra sensível)
# -------------------------
def is_cjk(char):
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF or   # CJK Unified Ideographs
        0x3400 <= code <= 0x4DBF or   # CJK Extension A
        0x3040 <= code <= 0x309F or   # Hiragana
        0x30A0 <= code <= 0x30FF or   # Katakana
        0xAC00 <= code <= 0xD7AF     # Hangul
    )

def contains_cjk(text):
    for idx, char in enumerate(text):
        if is_cjk(char):
            return True, char, idx
    return False, None, None

# -------------------------
# Detector de idioma
# -------------------------
def detect_language(text):
    try:
        return detect(text)
    except LangDetectException:
        return None
# -------------------------
# Extrai o techo do documento com o caractere problemático
# -------------------------
def extract_context(text, index, window=120):
    start = max(0, index - window)
    end = min(len(text), index + window + 1)

    snippet = text[start:end]

    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""

    return f"{prefix}{snippet}{suffix}"


def judge_response(prompt, response):
    judge_prompt = JUDGE_PROMPT_TEMPLATE.format(
        prompt=prompt.strip(),
        response=response.strip()
    )

    result = subprocess.run(
        ["ollama", "run", MODEL],
        input=judge_prompt,      # ✅ STRING, não dict
        capture_output=True,
        text=True
    )

    output = result.stdout.strip()
    return output
'''
    # limpeza defensiva
    for line in output.splitlines():
        line = line.strip()
        if line in {"SUPPORTED", "UNSUPPORTED", "INSUFFICIENT_CONTEXT"}:
            return line

    return "JUDGE_ERROR"
'''

# -------------------------
# Scan do CSV
# -------------------------
def scan_csv(file_path):
    with open(file_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        available_columns = set(reader.fieldnames or [])
        columns_to_scan = TARGET_COLUMNS & available_columns

        for row_idx, row in enumerate(reader, start=1):
            row_id = row.get("id", "N/A")

            for col in columns_to_scan:
                text = (row.get(col) or "").strip()
                if not text:
                    continue

                # 🔥 Detector CJK
                has_cjk, char, char_idx = contains_cjk(text)
                if has_cjk:
                    context = extract_context(text, char_idx, window=120)

                    print("[ALERTA CJK]")
                    print(f"ID: {row_id}")
                    print(f"Linha CSV: {row_idx}")
                    print(f"Coluna: {col}")
                    print(f"Caractere detectado: '{char}' (posição {char_idx})")
                    print("Contexto do texto:")
                    print(context)
                    print("-" * 60)
                    continue
                
                if col == "response":
                    prompt = row.get("ai_prompt", "").strip()
                    response = text

                    if prompt and response:
                        veredic = judge_response(prompt, response)
                        print(
                            f"[JUDGE] ID: {row_id} | "
                            f"Linha: {row_idx} | "
                            f"Veredicto: {veredic}"
                        )


# uso
scan_csv("ollama_qwen_not_servico_not_certifyssl.csv")
