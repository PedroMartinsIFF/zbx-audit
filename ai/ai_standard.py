import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.ai import OllamaAnalyzer
from analyzer.group_metrics_report import GroupMetricsReportBuilder
from shared.logger_config import LoggerSetup
from shared.utils import convert_json_to_toon

logger = LoggerSetup.get_logger(__name__)


class StandardOllamaAnalyzer(OllamaAnalyzer):
    def __init__(self, model: str, api_url: str):
        super().__init__(model, api_url)
        self.group_metrics_report = GroupMetricsReportBuilder(self._format_number, self._execute_prompt)

    def _create_prompt(
        self,
        metrics_group,
        group_name,
        metrics_env=None,
        use_toon=False,
        toon_depth=3,
        context_runbook: str = "",
        rag_match_info: str = "",
    ) -> str:
        if use_toon:
            group_data = "\n".join(convert_json_to_toon(metrics_group, max_depth=toon_depth))
            data_format = "TOON"
        else:
            group_data = json.dumps(metrics_group, indent=2, default=str, ensure_ascii=False)
            data_format = "JSON"

        runbook_block = ""
        if context_runbook:
            runbook_block = f"""
**Procedimento sugerido (RAG):**
{context_runbook}
"""

        rag_match_block = ""
        if rag_match_info:
            rag_match_block = f"""
**Match RAG determinístico (pré-processado):**
{rag_match_info}
"""

        return f"""
Você é um SRE Zabbix. Análise DIRETA do grupo '{group_name}'. Use dados EXATOS.

    **REGRAS DE SAÍDA (OBRIGATÓRIAS):**
    - Responda APENAS em português.
    - Responda APENAS em texto técnico; NÃO gere código, comandos, JSON, XML, HTML, pseudo-código, ou trechos com `import`, `def`, `class`.
    - NÃO use entidades HTML (`&quot;`, `&#39;`, `&gt;`, etc).
    - Se faltar dado para alguma seção, escreva "N/A".
    - Se existir **Procedimento sugerido (RAG)**, identifique explicitamente o problema que faz match e cite o procedimento de resolução.
    - NÃO reproduza regras/instruções do prompt no resultado final.
    - Se existir **Match RAG determinístico (pré-processado)**, use-o como fonte prioritária.

**Grupo '{group_name}':**
```{data_format.lower()}
{group_data}
```

{runbook_block}
{rag_match_block}

**RELATÓRIO DE ANÁLISE DO GRUPO**

## 1. ESTADO GERAL DO GRUPO

- **Classificação**: [Normal / Anômalo / Anômalo Crítico]
- **Justificativa**: Use `group_anomaly_analysis.z_score_group_vs_baseline` como o z-score principal do grupo. Valores > 2 ou < -2 indicam anomalia de volume. Cite explicitamente o z-score e compare `group_current_window.events_per_hour_avg` com `group_baseline.events_per_hour_avg`.
- **Volume Atual**: Use `group_current_window.total_events`, `group_current_window.hours_analyzed` e `group_current_window.events_per_hour_avg`.
- **Baseline do Grupo**: Use `group_baseline.events_per_hour_avg` e `group_baseline.events_per_hour_std`.
- **Taxa Crítica**: Compare `group_current_window.critical_events_ratio` com `group_baseline.critical_events_ratio_avg`.
- **Anomaly Score**: Use `group_anomaly_analysis.anomaly_score` como indicador complementar, mas não substitua o z-score.

## 2. PRINCIPAIS DESVIOS
- **Novos Problemas**: Liste `group_anomaly_analysis.new_problems`, se houver.
- **Anomalias por Proxy**: Destaque `group_anomaly_analysis.proxy_anomalies`, se houver.
- **Top 3 Problemas**: Use APENAS os dados de `top_problems`.
- **Hosts Críticos**: Use APENAS os dados de `top_hosts`.

## 3. AÇÕES RECOMENDADAS
### Ação Imediata:
1. [Ação específica com nome do host, proxy ou problema]
2. [Ação específica com nome do host, proxy ou problema]

Se houver RAG: incluir "Match RAG: <problema> -> <procedimento>".

---
**Resumo Executivo**: [2-3 linhas resumindo o estado do grupo, o desvio de baseline e a prioridade de ação]
"""

    def analyze_metrics(
        self,
        metrics_group: dict,
        group_name: str,
        metrics_env=None,
        use_toon=False,
        toon_depth=3,
        context_runbook: str = "",
        use_rag: bool = True,
    ) -> str:
        rag_match_info = ""
        selected_runbook_context = context_runbook or ""

        if use_rag:
            runbook_query = self._build_runbook_query(metrics_group, group_name, metrics_env=metrics_env)
            runbook_candidates = []

            if context_runbook:
                runbook_candidates.append(context_runbook)

            runbook_candidates.extend(
                self.rag_support.get_relevant_runbook_candidates(
                    runbook_query,
                    group_name=group_name,
                    limit=6,
                )
            )

            best_match = None
            best_context = ""
            for candidate_context in runbook_candidates:
                if not candidate_context:
                    continue
                match = self.rag_support.select_rag_match(metrics_group, candidate_context)
                if not match:
                    continue
                if not best_match or match.get("score", 0) > best_match.get("score", 0):
                    best_match = match
                    best_context = candidate_context

            if best_match:
                selected_runbook_context = best_context
                rag_match_info = (
                    f"Problema com match no RAG: {best_match['problem']} | "
                    f"Formato correspondente: {best_match['pattern']} | "
                    f"Procedimento aplicado: {best_match['procedure']}"
                )

        if metrics_env:
            return super().analyze_metrics(
                metrics_group,
                group_name,
                metrics_env=metrics_env,
                use_toon=use_toon,
                toon_depth=toon_depth,
                context_runbook=selected_runbook_context,
                use_rag=use_rag,
            )

        self.last_prompt = ""
        logs_dir = Path(__file__).parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        prompt_log_file = logs_dir / "last_ai_prompt.txt"

        try:
            final_report, combined_prompt = self.group_metrics_report.build_report(
                metrics_group,
                group_name,
                context_runbook=selected_runbook_context,
                rag_match_info=rag_match_info,
            )
            self.last_prompt = combined_prompt
            with open(prompt_log_file, "w", encoding="utf-8") as file:
                file.write(self.last_prompt)
            return final_report
        except requests.exceptions.Timeout:
            logger.exception("⏰ Timeout no fluxo fracionado de group-metrics | grupo=%s", group_name)
            return "Erro: Timeout no fluxo fracionado de métricas por grupo. Chamada ao Ollama excedeu o tempo limite."
        except requests.exceptions.RequestException as error:
            logger.exception("❌ Erro no fluxo fracionado de group-metrics | grupo=%s", group_name)
            return f"Erro de conectividade: {error}"
