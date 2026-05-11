import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger_config import LoggerSetup


logger = LoggerSetup.get_logger(__name__)


class EnvAuditReportBuilder:
    def __init__(self, format_number, execute_prompt):
        self._format_number = format_number
        self._execute_prompt = execute_prompt

    def build_section_1(self, metrics_env: dict) -> str:
        z_score = float(metrics_env.get("z_score_24h_vs_30d") or 0)
        total_events = metrics_env.get("total_events_24h")
        events_hour = metrics_env.get("events_per_hour_24h") or {}
        baseline = metrics_env.get("baseline_30d") or {}
        current_avg = events_hour.get("current_avg")
        baseline_avg = baseline.get("events_per_hour_avg")
        critical_ratio = baseline.get("critical_ratio")
        last_hour_z = events_hour.get("z_score_last_hour")

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
                f"O z_score_24h_vs_30d é {self._format_number(z_score)}, o que "
                f"{'está dentro' if -2 <= z_score <= 2 else 'está fora'} do intervalo normal (-2 a 2). "
                f"O volume total observado nas últimas 24 horas é {self._format_number(total_events)} eventos. "
                f"A taxa atual de eventos ({self._format_number(current_avg)} eventos/hora) está {trend} "
                f"a baseline de 30 dias ({self._format_number(baseline_avg)} eventos/hora)."
            )
        else:
            justification = (
                f"O z_score_24h_vs_30d é {self._format_number(z_score)}. "
                f"O volume total observado nas últimas 24 horas é {self._format_number(total_events)} eventos."
            )

        return "\n".join(
            [
                "## 1. ESTADO GERAL DO AMBIENTE",
                f"- **Classificação**: {classification}",
                f"- **Justificativa**: {justification}",
                f"- **Taxa de Eventos**: {self._format_number(current_avg)} eventos/hora versus {self._format_number(baseline_avg)} eventos/hora na baseline de 30 dias.",
                f"- **Taxa Crítica**: {critical_ratio if critical_ratio is not None else 'N/A'}",
                f"- **Última Hora**: z_score_last_hour = {self._format_number(last_hour_z)}.",
            ]
        )

    def build_section_2_2(self, metrics_env: dict) -> str:
        proxies_last_hour = metrics_env.get("proxies_last_hour") or []
        lines = ["### 2.2 Proxies Ativos na Última Hora"]

        if not proxies_last_hour:
            lines.append("N/A")
            return "\n".join(lines)

        for item in proxies_last_hour[:3]:
            if not isinstance(item, dict):
                continue
            proxy = item.get("proxy") or "N/A"
            last_hour_events = self._format_number(item.get("last_hour_events"))
            z_score_last_hour = item.get("z_score_last_hour")
            z_score_last_hour_text = self._format_number(z_score_last_hour)
            behavior = "pico recente" if z_score_last_hour is not None and float(z_score_last_hour) > 0 else "sem pico recente relevante"
            lines.append(
                f"- **{proxy}**: `last_hour_events` = {last_hour_events} | "
                f"`z_score_last_hour` = {z_score_last_hour_text} | {behavior}."
            )

        return "\n".join(lines)

    @staticmethod
    def _format_event_line(event: dict) -> str:
        severity = event.get("severity", "N/A")
        host = event.get("host") or "N/A"
        problem = event.get("problem") or "N/A"
        opened_at = event.get("opened_at") or "N/A"
        age = event.get("event_age_minutes", "N/A")
        return f"- **[SEV {severity}]** Host: {host} | Problema: {problem} | Aberto em: {opened_at} | Idade: {age} min"

    def build_section_3(self, metrics_env: dict) -> str:
        critical_events = metrics_env.get("critical_active_events") or []
        recent_events = metrics_env.get("recent_active_events") or []
        persistent_events = metrics_env.get("persistent_noncritical_events") or []

        lines = ["## 3. EVENTOS CRÍTICOS ATIVOS"]
        if not critical_events:
            lines.append("Não há eventos críticos ativos (severity >= 4) no contexto atual.")
        else:
            for event in critical_events:
                if isinstance(event, dict):
                    lines.append(self._format_event_line(event))

        if recent_events:
            lines.append("- **Eventos recentes**:")
            for event in recent_events:
                if isinstance(event, dict):
                    lines.append(self._format_event_line(event))
        else:
            lines.append("- **Eventos recentes**: N/A")

        if persistent_events:
            lines.append("- **Eventos antigos/persistentes**:")
            for event in persistent_events:
                if isinstance(event, dict):
                    lines.append(self._format_event_line(event))
        else:
            lines.append("- **Eventos antigos/persistentes**: N/A")

        return "\n".join(lines)

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

        direct = EnvAuditReportBuilder.extract_section(raw, r"## 4\. AÇÕES RECOMENDADAS")
        if direct:
            return direct

        if "### Ações Imediatas:" in raw or "**Resumo Executivo**" in raw:
            if raw.startswith("### Ações Imediatas:") or raw.startswith("**Resumo Executivo**"):
                return "## 4. AÇÕES RECOMENDADAS\n" + raw
            actions_pos = raw.find("### Ações Imediatas:")
            summary_pos = raw.find("**Resumo Executivo**")
            start_positions = [pos for pos in (actions_pos, summary_pos) if pos != -1]
            if start_positions:
                return "## 4. AÇÕES RECOMENDADAS\n" + raw[min(start_positions):].strip()

        return ""

    def build_proxy_candidates(self, metrics_env: dict) -> list[dict]:
        proxies = metrics_env.get("proxies_top_anomalies") or []
        correlations = {
            item.get("proxy"): item
            for item in (metrics_env.get("proxy_event_correlations") or [])
            if isinstance(item, dict) and item.get("proxy")
        }

        positive = []
        fallback = []
        for item in proxies:
            if not isinstance(item, dict):
                continue
            candidate = {
                "proxy": item.get("proxy"),
                "last_hour_events": item.get("last_hour_events"),
                "z_score_vs_own_baseline": item.get("z_score_vs_own_baseline"),
                "z_score": item.get("z_score"),
                "z_score_last_hour": item.get("z_score_last_hour"),
                "correlation_status": (correlations.get(item.get("proxy")) or {}).get("correlation_status"),
                "correlation_reason": (correlations.get(item.get("proxy")) or {}).get("correlation_reason"),
            }
            if (item.get("last_hour_events") or 0) > 0 and (item.get("z_score_vs_own_baseline") or 0) > 0:
                positive.append(candidate)
            elif (item.get("last_hour_events") or 0) > 0:
                fallback.append(candidate)

        positive.sort(key=lambda x: float(x.get("z_score_vs_own_baseline") or 0), reverse=True)
        fallback.sort(key=lambda x: float(x.get("z_score_vs_own_baseline") or 0), reverse=True)
        merged = positive[:3]
        if len(merged) < 3:
            merged.extend(fallback[: 3 - len(merged)])
        return merged[:3]

    def create_proxy_analysis_prompt(self, proxy_candidates: list[dict]) -> str:
        proxy_data = json.dumps(proxy_candidates, indent=2, ensure_ascii=False)
        return f"""
Você é um SRE especialista em Zabbix.

Escreva APENAS a subseção abaixo, em português técnico, usando somente os dados fornecidos.
- Não escreva a seção 1, a seção 2.2, a seção 3, a seção 4 ou o resumo.
- Não invente proxies, métricas ou correlações.
- Use APENAS os proxies listados.
- Mantenha exatamente este cabeçalho:

### 2.1 Detecção de Problemas

- Para ranquear, respeite a ordem em que os proxies já foram fornecidos.
- Considere que a lista já foi pré-filtrada para relevância operacional.
- Cite `z_score_vs_own_baseline` como métrica principal.
- Cite `z_score` ou `z_score_last_hour` apenas como contexto adicional.
- Se `correlation_status = none`, diga explicitamente que não há evento ativo correlato no próprio proxy.
- Não descreva o caso como `evento isolado`, `evento transitório`, `natureza transitória`, `melhora` ou equivalentes sem evidência explícita.
- Quando `correlation_status = none`, prefira formulações como `não há correlação ativa confirmada no próprio proxy ou host`.
- Ao descrever a intensidade, use termos neutros como `desvio relevante`, `desvio moderado` ou `desvio limitado`, evitando conclusões causais.

**Dados dos proxies para análise:**
```json
{proxy_data}
```
"""

    def create_actions_summary_prompt(
        self,
        metrics_env: dict,
        group_name: str,
        proxy_candidates: list[dict],
        context_runbook: str = "",
        rag_match_info: str = "",
    ) -> str:
        context_payload = {
            "group_name": group_name,
            "classification": "NORMAL" if -2 <= float(metrics_env.get("z_score_24h_vs_30d") or 0) <= 2 else "ANÔMALO",
            "z_score_24h_vs_30d": metrics_env.get("z_score_24h_vs_30d"),
            "events_per_hour_current_avg": (metrics_env.get("events_per_hour_24h") or {}).get("current_avg"),
            "baseline_events_per_hour_avg": (metrics_env.get("baseline_30d") or {}).get("events_per_hour_avg"),
            "proxy_candidates": proxy_candidates,
            "critical_active_events": metrics_env.get("critical_active_events") or [],
            "recent_active_events": metrics_env.get("recent_active_events") or [],
            "persistent_noncritical_events": metrics_env.get("persistent_noncritical_events") or [],
            "rag_match_info": rag_match_info or "",
            "runbook": context_runbook or "",
        }
        serialized = json.dumps(context_payload, indent=2, ensure_ascii=False)
        return f"""
Você é um SRE especialista em Zabbix.

Escreva APENAS as seções abaixo, em português técnico:
- `## 4. AÇÕES RECOMENDADAS`
- `**Resumo Executivo**`

Regras obrigatórias:
- Não escreva as seções 1, 2 ou 3.
- Em `### Ações Imediatas:`, escreva exatamente 2 itens numerados.
- Se não houver eventos ativos nem persistentes, baseie as ações nos proxies candidatos já fornecidos.
- Use `z_score_vs_own_baseline` como métrica principal para priorização das ações.
- Use `z_score` ou `z_score_last_hour` apenas como contexto adicional; não trate essas métricas como critério principal.
- Priorize proxies com `z_score_vs_own_baseline` positivo.
- Interprete `z_score_vs_own_baseline` positivo como aumento relativo de eventos ou desvio positivo em relação ao histórico, e NÃO como melhoria de desempenho.
- Não use termos como `melhora`, `melhoria`, `desempenho melhor` ou equivalentes para descrever desvio positivo de eventos.
- Não priorize proxy com `z_score_vs_own_baseline` negativo, exceto se houver correlação confirmada ou ausência de alternativas melhores no contexto fornecido.
- Se houver pelo menos 2 proxies com `z_score_vs_own_baseline` positivo no contexto fornecido, não escolha proxy com `z_score_vs_own_baseline` negativo como uma das 2 ações prioritárias.
- Se não houver correlação confirmada, não trate proxy anômalo como causa comprovada.
- Se `correlation_status = none`, deixe explícito que a ação é investigativa e não conclusiva.
- Variação de volume de eventos sem alerta ativo correlato, sem evento persistente e sem correlação confirmada NÃO deve ser tratada como incidente imediato.
- Quando houver apenas desvio de volume sem alarmes associados, prefira termos como `monitoramento`, `acompanhamento`, `observação` ou `avaliar persistência`, em vez de `investigação imediata`, `falha`, `incidente` ou `problema confirmado`.
- No resumo executivo, não conclua que aumento de eventos por si só indica problema real no ambiente.
- Se `rag_match_info` não estiver vazio e ele corresponder a um proxy com `correlation_status = confirmed` ou a um evento crítico ativo, a AÇÃO 1 deve obrigatoriamente:
  - citar explicitamente o nome do proxy;
  - citar explicitamente o problema correspondente;
  - descrever os passos operacionais do procedimento recomendado.
- Na AÇÃO 1, não omita o sujeito da ação. A frase deve deixar claro qual proxy e qual problema estão sendo tratados.
- A AÇÃO 2 não pode repetir o mesmo caso da AÇÃO 1. Use a AÇÃO 2 para outro proxy candidato relevante ou para monitoramento complementar.
- Se `rag_match_info` não estiver vazio, não omita passos relevantes do procedimento resumido presente no contexto.
- Não substitua o procedimento por frases genéricas como `monitorar`, `acompanhar` ou `aguardar normalização` quando o contexto já trouxer passos concretos.
- No resumo executivo, se houver evento crítico ativo correlacionado, descreva isso como `evento crítico ativo correlacionado ao proxy`, e NÃO como `problema crítico do ambiente`.
- O `Resumo Executivo` pode ter entre 2 e 4 linhas curtas.
- Não reutilize números ou exemplos que não estejam no contexto.

**Contexto reduzido para ações e resumo:**
```json
{serialized}
```
"""

    @staticmethod
    def assemble_report(section_1: str, section_2_1: str, section_2_2: str, section_3: str, actions_summary: str) -> str:
        parts = [
            section_1.strip(),
            "## 2. ANÁLISE DE PROXIES",
            section_2_1.strip(),
            section_2_2.strip(),
            section_3.strip(),
            actions_summary.strip(),
        ]
        return "\n\n".join(part for part in parts if part)

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
    def assemble_full_prompt(proxy_prompt: str, actions_summary_prompt: str) -> str:
        return (
            "===== ENV-AUDIT / SEÇÃO 2.1 =====\n\n"
            f"{proxy_prompt.strip()}\n\n"
            "===== ENV-AUDIT / SEÇÃO 4 + RESUMO =====\n\n"
            f"{actions_summary_prompt.strip()}"
        )

    def build_report(
        self,
        metrics_env: dict,
        group_name: str,
        context_runbook: str = "",
        rag_match_info: str = "",
    ) -> tuple[str, str]:
        section_1 = self.build_section_1(metrics_env)
        section_2_2 = self.build_section_2_2(metrics_env)
        section_3 = self.build_section_3(metrics_env)
        proxy_candidates = self.build_proxy_candidates(metrics_env)

        proxy_prompt = self.create_proxy_analysis_prompt(proxy_candidates)
        actions_summary_prompt = self.create_actions_summary_prompt(
            metrics_env,
            group_name,
            proxy_candidates,
            context_runbook=context_runbook,
            rag_match_info=rag_match_info,
        )
        combined_prompt = self.assemble_full_prompt(proxy_prompt, actions_summary_prompt)

        section_2_1_raw = self._execute_prompt(proxy_prompt, group_name, "env-audit-section-2-1", num_predict=700)
        section_2_1 = self.extract_section(section_2_1_raw, r"### 2\.1 Detecção de Problemas")
        if not section_2_1:
            section_2_1 = "### 2.1 Detecção de Problemas\nN/A"

        actions_summary_raw = self._execute_prompt(
            actions_summary_prompt,
            group_name,
            "env-audit-actions-summary",
            num_predict=900,
        )
        actions_summary = self.extract_actions_summary(actions_summary_raw)
        if not actions_summary:
            logger.warning(
                "Fallback em ações/resumo do env-audit | grupo=%s | resposta_bruta=%r",
                group_name,
                actions_summary_raw[:1500],
            )
            actions_summary = (
                "## 4. AÇÕES RECOMENDADAS\n"
                "### Ações Imediatas:\n"
                "1. N/A\n"
                "2. N/A\n\n"
                "**Resumo Executivo**: N/A"
            )

        report = self.assemble_report(
            section_1,
            section_2_1,
            section_2_2,
            section_3,
            self.strip_rag_when_unmatched(actions_summary, rag_match_info),
        )
        return report, combined_prompt
