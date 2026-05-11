import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger_config import LoggerSetup


logger = LoggerSetup.get_logger(__name__)


class GroupMetricsReportBuilder:
    def __init__(self, format_number, execute_prompt):
        self._format_number = format_number
        self._execute_prompt = execute_prompt

    def build_section_1(self, metrics_group: dict) -> str:
        current_window = metrics_group.get("group_current_window") or {}
        baseline = metrics_group.get("group_baseline") or {}
        anomaly = metrics_group.get("group_anomaly_analysis") or {}

        z_score = float(anomaly.get("z_score_group_vs_baseline") or 0)
        total_events = current_window.get("total_events")
        hours = current_window.get("hours_analyzed")
        current_avg = current_window.get("events_per_hour_avg")
        baseline_avg = baseline.get("events_per_hour_avg")
        baseline_std = baseline.get("events_per_hour_std")
        current_critical_ratio = current_window.get("critical_events_ratio")
        baseline_critical_ratio = baseline.get("critical_events_ratio_avg")
        anomaly_score = anomaly.get("anomaly_score")

        classification = "NORMAL" if -2 <= z_score <= 2 else "ANÔMALO"
        if current_avg is not None and baseline_avg is not None:
            trend = (
                "abaixo"
                if float(current_avg) < float(baseline_avg)
                else "acima"
                if float(current_avg) > float(baseline_avg)
                else "em linha com"
            )
            justification = (
                f"O z_score_group_vs_baseline é {self._format_number(z_score)}, o que "
                f"{'está dentro' if -2 <= z_score <= 2 else 'está fora'} do intervalo normal (-2 a 2). "
                f"O grupo registrou {self._format_number(total_events)} eventos em {self._format_number(hours)} horas. "
                f"A taxa atual ({self._format_number(current_avg)} eventos/hora) está {trend} "
                f"a baseline do grupo ({self._format_number(baseline_avg)} eventos/hora)."
            )
        else:
            justification = (
                f"O z_score_group_vs_baseline é {self._format_number(z_score)}. "
                f"O grupo registrou {self._format_number(total_events)} eventos no período."
            )

        return "\n".join(
            [
                "## 1. ESTADO GERAL DO GRUPO",
                f"- **Classificação**: {classification}",
                f"- **Justificativa**: {justification}",
                f"- **Volume Atual**: {self._format_number(total_events)} eventos em {self._format_number(hours)} horas ({self._format_number(current_avg)} eventos/hora).",
                f"- **Baseline do Grupo**: {self._format_number(baseline_avg)} eventos/hora (desvio padrão {self._format_number(baseline_std)}).",
                f"- **Taxa Crítica**: {self._format_number(current_critical_ratio)} no período atual versus {self._format_number(baseline_critical_ratio)} na baseline.",
                f"- **Anomaly Score**: {self._format_number(anomaly_score)}.",
            ]
        )

    def build_section_2_2(self, metrics_group: dict) -> str:
        top_problems = metrics_group.get("top_problems") or []
        top_hosts = metrics_group.get("top_hosts") or []

        lines = ["### 2.2 Evidências Objetivas do Período"]

        if top_problems:
            lines.append("- **Top Problemas**:")
            for item in top_problems[:3]:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("problem_name") or item.get("problem") or "N/A"
                    count = self._format_number(item.get("count"))
                    lines.append(f"  - {name}: {count}")
        else:
            lines.append("- **Top Problemas**: N/A")

        if top_hosts:
            lines.append("- **Top Hosts**:")
            for item in top_hosts[:3]:
                if isinstance(item, (list, tuple)) and item:
                    host = item[0]
                    count = self._format_number(item[1] if len(item) > 1 else None)
                    lines.append(f"  - {host}: {count}")
                elif isinstance(item, dict):
                    host = item.get("host_name") or item.get("host") or "N/A"
                    count = self._format_number(item.get("count"))
                    lines.append(f"  - {host}: {count}")
        else:
            lines.append("- **Top Hosts**: N/A")

        return "\n".join(lines)

    def build_candidates(self, metrics_group: dict) -> list[dict]:
        top_problem_details = metrics_group.get("top_problem_details") or []
        top_hosts = metrics_group.get("top_hosts") or []
        host_problem_map: dict[str, list[dict]] = {}

        for item in top_problem_details:
            if not isinstance(item, dict):
                continue
            problem_name = item.get("problem_name")
            affected_entities = item.get("affected_entities") or []
            for entity in affected_entities:
                if not isinstance(entity, dict):
                    continue
                host_name = entity.get("host_name")
                if not host_name:
                    continue
                host_problem_map.setdefault(host_name, []).append(
                    {
                        "problem_name": problem_name,
                        "problem_event_count": entity.get("event_count"),
                        "severity": entity.get("severity"),
                    }
                )

        candidates: list[dict] = []
        for item in top_hosts[:3]:
            if not isinstance(item, (list, tuple)) or not item:
                continue
            host_name = item[0]
            host_event_count = item[1] if len(item) > 1 else None
            related_problems = host_problem_map.get(host_name, [])
            candidates.append(
                {
                    "type": "host",
                    "name": host_name,
                    "primary_metric_name": "event_count",
                    "primary_metric_value": host_event_count,
                    "problem_count": len({p.get("problem_name") for p in related_problems if p.get("problem_name")}),
                    "top_host_problems": related_problems[:3],
                    "context": "Host com maior concentração de eventos no período.",
                }
            )

        return candidates[:3]

    def create_deviation_prompt(self, candidates: list[dict]) -> str:
        serialized = json.dumps(candidates, indent=2, ensure_ascii=False)
        return f"""
Você é um SRE especialista em Zabbix.

Escreva APENAS a subseção abaixo, em português técnico, usando somente os dados fornecidos.
- Não escreva a seção 1, a seção 2.2, a seção 3 ou o resumo.
- Não invente entidades, métricas ou correlações.
- Use APENAS os itens listados.
- Mantenha exatamente este cabeçalho:

### 2.1 Interpretação dos Desvios

- Para ranquear, respeite a ordem em que os itens já foram fornecidos.
- Considere que a lista já foi pré-filtrada para relevância operacional.
- Trate `primary_metric_name` e `primary_metric_value` como métrica principal.
- Use os demais campos apenas como contexto adicional.
- Nesta análise por grupo, o foco principal deve ser o host.
- Para cada item, descreva o host, o volume de eventos no período e os principais problemas associados a ele, quando existirem.
- Problemas só devem ser mencionados quando estiverem associados ao host do item.
- Não interprete `event_count`, nomes de problemas ou concentração por host como evidência confirmada de indisponibilidade, falha real, causa raiz ou incidente confirmado sem evidência explícita.
- Não use expressões como `condição crítica`, `afeta disponibilidade`, `causa raiz`, `incidente confirmado`, `falha confirmada` ou equivalentes sem evidência explícita.
- Quando o item merecer atenção, prefira formulações como `deve ser priorizado para análise`, `merece avaliação detalhada`, `indica recorrência relevante no período` ou `concentra volume relevante de eventos`.
- Se não houver baseline específica por host nos dados, não invente comparação histórica por host.
- Use linguagem técnica, objetiva e sem conclusões causais.

**Itens para análise:**
```json
{serialized}
```
"""

    def create_actions_summary_prompt(
        self,
        metrics_group: dict,
        group_name: str,
        candidates: list[dict],
        context_runbook: str = "",
        rag_match_info: str = "",
    ) -> str:
        current_window = metrics_group.get("group_current_window") or {}
        baseline = metrics_group.get("group_baseline") or {}
        anomaly = metrics_group.get("group_anomaly_analysis") or {}

        payload = {
            "group_name": group_name,
            "classification": "NORMAL" if -2 <= float(anomaly.get("z_score_group_vs_baseline") or 0) <= 2 else "ANÔMALO",
            "z_score_group_vs_baseline": anomaly.get("z_score_group_vs_baseline"),
            "anomaly_score": anomaly.get("anomaly_score"),
            "events_per_hour_current_avg": current_window.get("events_per_hour_avg"),
            "baseline_events_per_hour_avg": baseline.get("events_per_hour_avg"),
            "critical_events": current_window.get("critical_events"),
            "critical_events_ratio": current_window.get("critical_events_ratio"),
            "baseline_critical_events_ratio_avg": baseline.get("critical_events_ratio_avg"),
            "candidates": candidates,
            "rag_match_info": rag_match_info or "",
            "runbook": context_runbook or "",
        }
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        return f"""
Você é um SRE especialista em Zabbix.

Escreva APENAS as seções abaixo, em português técnico:
- `## 3. AÇÕES RECOMENDADAS`
- `**Resumo Executivo**`

Regras obrigatórias:
- Não escreva as seções 1 ou 2.
- Em `### Ações Imediatas:`, escreva exatamente 2 itens numerados.
- Use os itens candidatos já fornecidos como base principal das ações.
- Na análise por grupo, priorize hosts com maior concentração de eventos como foco operacional principal.
- Ao recomendar ações, cite o host explicitamente e mencione os principais problemas associados apenas se estiverem presentes no item.
- Se o grupo estiver classificado como NORMAL e não houver evidência explícita de incidente ativo, prefira termos como `monitoramento`, `acompanhamento`, `observação` ou `avaliar persistência`.
- Mesmo com grupo classificado como NORMAL, é permitido priorizar hosts e problemas dominantes para análise operacional.
- Não trate variação estatística, concentração de eventos ou nome do problema, por si só, como incidente confirmado.
- Só use `investigar imediatamente`, `falha`, `incidente` ou `problema confirmado` quando houver evidência clara no contexto reduzido.
- Se existir `rag_match_info`, uma das 2 ações deve obrigatoriamente citar o problema correspondente e incorporar o procedimento recomendado na mesma ação, em linguagem natural.
- Se existir `rag_match_info`, não omita passos relevantes do procedimento resumido presente no contexto.
- Se existir `rag_match_info` e ele corresponder a um problema de um dos hosts candidatos, a ação que usa o RAG deve citar explicitamente esse host.
- Não substitua o procedimento do `rag_match_info` por frases genéricas como `monitorar`, `acompanhar` ou `avaliar`; descreva os passos operacionais já fornecidos no contexto.
- O `Resumo Executivo` pode ter entre 2 e 4 linhas curtas.
- No resumo executivo, diferencie estado global do grupo e prioridade operacional local dos itens dominantes.
- Não reutilize números ou exemplos que não estejam no contexto.

**Contexto reduzido:**
```json
{serialized}
```
"""

    @staticmethod
    def extract_section(text: str, start_pattern: str, end_pattern: str | None = None) -> str:
        if end_pattern:
            pattern = rf"{start_pattern}.*?(?={end_pattern}|\Z)"
        else:
            pattern = rf"{start_pattern}.*"
        match = re.search(pattern, text or "", flags=re.DOTALL)
        return match.group(0).strip() if match else ""

    @staticmethod
    def extract_actions_summary(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        direct = GroupMetricsReportBuilder.extract_section(raw, r"## 3\. AÇÕES RECOMENDADAS")
        if direct:
            return direct

        if "### Ações Imediatas:" in raw or "**Resumo Executivo**" in raw:
            if raw.startswith("### Ações Imediatas:") or raw.startswith("**Resumo Executivo**"):
                return "## 3. AÇÕES RECOMENDADAS\n" + raw
            actions_pos = raw.find("### Ações Imediatas:")
            summary_pos = raw.find("**Resumo Executivo**")
            start_positions = [pos for pos in (actions_pos, summary_pos) if pos != -1]
            if start_positions:
                return "## 3. AÇÕES RECOMENDADAS\n" + raw[min(start_positions):].strip()

        return ""

    @staticmethod
    def strip_rag_when_unmatched(report: str, rag_match_info: str) -> str:
        if rag_match_info:
            return report
        cleaned_lines = []
        for line in report.splitlines():
            if "Problema com match no RAG:" in line:
                continue
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    @staticmethod
    def assemble_full_prompt(deviation_prompt: str, actions_summary_prompt: str) -> str:
        return (
            "===== GROUP-METRICS / SEÇÃO 2.1 =====\n\n"
            f"{deviation_prompt.strip()}\n\n"
            "===== GROUP-METRICS / SEÇÃO 3 + RESUMO =====\n\n"
            f"{actions_summary_prompt.strip()}"
        )

    @staticmethod
    def assemble_report(section_1: str, section_2_1: str, section_2_2: str, actions_summary: str) -> str:
        parts = [
            section_1.strip(),
            "## 2. PRINCIPAIS DESVIOS",
            section_2_1.strip(),
            section_2_2.strip(),
            actions_summary.strip(),
        ]
        return "\n\n".join(part for part in parts if part)

    def build_report(
        self,
        metrics_group: dict,
        group_name: str,
        context_runbook: str = "",
        rag_match_info: str = "",
    ) -> tuple[str, str]:
        section_1 = self.build_section_1(metrics_group)
        section_2_2 = self.build_section_2_2(metrics_group)
        candidates = self.build_candidates(metrics_group)

        deviation_prompt = self.create_deviation_prompt(candidates)
        actions_summary_prompt = self.create_actions_summary_prompt(
            metrics_group,
            group_name,
            candidates,
            context_runbook=context_runbook,
            rag_match_info=rag_match_info,
        )
        combined_prompt = self.assemble_full_prompt(deviation_prompt, actions_summary_prompt)

        section_2_1_raw = self._execute_prompt(deviation_prompt, group_name, "group-metrics-section-2-1", num_predict=700)
        section_2_1 = self.extract_section(section_2_1_raw, r"### 2\.1 Interpretação dos Desvios")
        if not section_2_1:
            section_2_1 = "### 2.1 Interpretação dos Desvios\nN/A"

        actions_summary_raw = self._execute_prompt(
            actions_summary_prompt,
            group_name,
            "group-metrics-actions-summary",
            num_predict=900,
        )
        actions_summary = self.extract_actions_summary(actions_summary_raw)
        if not actions_summary:
            logger.warning(
                "Fallback em ações/resumo do group-metrics | grupo=%s | resposta_bruta=%r",
                group_name,
                actions_summary_raw[:1500],
            )
            actions_summary = (
                "## 3. AÇÕES RECOMENDADAS\n"
                "### Ações Imediatas:\n"
                "1. N/A\n"
                "2. N/A\n\n"
                "**Resumo Executivo**: N/A"
            )

        report = self.assemble_report(
            section_1,
            section_2_1,
            section_2_2,
            self.strip_rag_when_unmatched(actions_summary, rag_match_info),
        )
        return report, combined_prompt
