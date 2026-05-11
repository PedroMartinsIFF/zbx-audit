import json
import sys
import time
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger_config import LoggerSetup
from analyzer.env_audit_report import EnvAuditReportBuilder
from ai.ollama_client import OllamaClient
from ai.rag_support import RagSupport

try:
    from shared.config import OLLAMA_TIMEOUT
except Exception:
    OLLAMA_TIMEOUT = 600

logger = LoggerSetup.get_logger(__name__)


class OllamaAnalyzer:
    def __init__(self, model: str, api_url: str):
        self.model = model
        self.api_url = api_url
        self.last_prompt = ""
        self.ollama_client = OllamaClient(model, api_url)
        self.rag_support = RagSupport(api_url)
        self.env_audit_report = EnvAuditReportBuilder(self._format_number, self._execute_prompt)

    @staticmethod
    def _format_number(value) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return str(value)

    def _build_runbook_query(self, metrics_group: dict, group_name: str, metrics_env=None) -> str:
        parts = [f"Grupo: {group_name}"]

        top_problems = metrics_group.get("top_problems")
        if isinstance(top_problems, list) and top_problems:
            problem_names = []
            for item in top_problems[:3]:
                if isinstance(item, dict):
                    problem_name = item.get("name") or item.get("problem") or item.get("problem_name")
                    if problem_name:
                        problem_names.append(str(problem_name))
                elif isinstance(item, str):
                    problem_names.append(item)
            if problem_names:
                parts.append(f"Top problemas: {', '.join(problem_names)}")

        active_events = metrics_group.get("active_events")
        if isinstance(active_events, list) and active_events:
            event_names = []
            for item in active_events[:3]:
                if isinstance(item, dict):
                    event_name = item.get("problem") or item.get("problem_name")
                    if event_name:
                        event_names.append(str(event_name))
            if event_names:
                parts.append(f"Eventos ativos: {', '.join(event_names)}")

        candidates = metrics_group.get("candidates")
        if isinstance(candidates, list) and candidates:
            candidate_problem_names = []
            seen_cp = set()
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                for problem in (candidate.get("top_host_problems") or []):
                    if not isinstance(problem, dict):
                        continue
                    pname = problem.get("problem_name") or problem.get("problem")
                    if pname and str(pname) not in seen_cp:
                        seen_cp.add(str(pname))
                        candidate_problem_names.append(str(pname))
            if candidate_problem_names:
                parts.append(f"Problemas nos hosts: {', '.join(candidate_problem_names[:6])}")

        if metrics_env and isinstance(metrics_env, dict):
            env_active_events = metrics_env.get("active_events")
            if isinstance(env_active_events, list) and env_active_events:
                env_event_names = []
                for item in env_active_events[:5]:
                    if isinstance(item, dict):
                        event_name = item.get("problem") or item.get("problem_name")
                        if event_name:
                            env_event_names.append(str(event_name))
                if env_event_names:
                    parts.append(f"Eventos ativos ambiente: {', '.join(env_event_names)}")

            proxies_top = metrics_env.get("proxies_top_anomalies")
            if isinstance(proxies_top, list) and proxies_top:
                proxy_names = []
                for item in proxies_top[:3]:
                    if isinstance(item, dict) and item.get("proxy"):
                        proxy_names.append(str(item.get("proxy")))
                if proxy_names:
                    parts.append(f"Proxies com anomalia: {', '.join(proxy_names)}")

        return " | ".join(parts)

    def get_relevant_runbook(self, problem_description: str, group_name: str = None) -> str:
        return self.rag_support.get_relevant_runbook(problem_description, group_name=group_name)

    @staticmethod
    def _build_group_alert_context(metrics_group: dict, group_name: str) -> dict:
        active_events = metrics_group.get("active_events") or []
        compact_active_events = []
        for item in active_events[:10]:
            if not isinstance(item, dict):
                continue
            compact_active_events.append(
                {
                    "host": item.get("host") or item.get("host_name"),
                    "problem": item.get("problem") or item.get("problem_name"),
                    "severity": item.get("severity"),
                    "opened_at": item.get("opened_at"),
                    "event_age_minutes": item.get("event_age_minutes"),
                    "proxy_name": item.get("proxy_name"),
                }
            )

        return {
            "group_name": group_name,
            "active_events": compact_active_events,
        }

    def _execute_prompt(self, prompt: str, group_name: str, label: str, num_predict: int = 1200) -> str:
        return self.ollama_client.generate(prompt, group_name, label, num_predict=num_predict)

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
        group_prompt_context = self._build_group_alert_context(metrics_group, group_name)

        if use_toon:
            # Importar a função de conversão
            from utils import convert_json_to_toon
            group_data = "\n".join(convert_json_to_toon(group_prompt_context, max_depth=toon_depth))
            env_data = "\n".join(convert_json_to_toon(metrics_env, max_depth=toon_depth)) if metrics_env else None
            data_format = "TOON"
            format_description = "TOON"
        else:
            group_data = json.dumps(group_prompt_context, indent=2, default=str, ensure_ascii=False)
            env_data = json.dumps(metrics_env, indent=2, default=str, ensure_ascii=False) if metrics_env else None
            data_format = "JSON"
            format_description = "JSON"

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


        env_block = ""
        if env_data:
            env_block = f"""
**Ambiente:**
```{data_format.lower()}
{env_data}
```
"""


        return f"""
Você é um SRE especialista em Zabbix. Analise os dados abaixo e gere um relatório técnico detalhado, seguindo as instruções:

**REGRAS DE SAÍDA (OBRIGATÓRIAS):**
- Responda APENAS em português.
- Responda APENAS em texto técnico; NÃO gere código, comandos, JSON, XML, HTML, pseudo-código, ou trechos com `import`, `def`, `class`.
- NÃO use entidades HTML (`&quot;`, `&#39;`, `&gt;`, etc).
- Se faltar dado para alguma seção, escreva "N/A" de forma explícita.
- Mantenha exatamente a estrutura solicitada no relatório.
- Se existir **Procedimento sugerido (RAG)**, identifique explicitamente qual problema dos dados faz match com esse procedimento.
- Quando houver match de RAG, cite textualmente em **Ações Imediatas**: (1) o problema correspondente e (2) o procedimento recomendado para resolução.
- NÃO reproduza regras/instruções do prompt no resultado final; entregue apenas o relatório final.
- Se existir **Match RAG determinístico (pré-processado)**, use-o como fonte prioritária para a seção de ações.

**IMPORTANTE:**
- Compare sempre médias com médias, totais com totais.
- `total_events_24h` = total de eventos nas últimas 24 horas.
- `avg_events_24h` = média simples derivada do total de eventos nas últimas 24 horas.
- `events_per_hour_24h.current_avg` = média de eventos por hora nas últimas 24 horas para comparação com baseline.
- `baseline_30d.events_per_hour_avg` = média de eventos por hora nos últimos 30 dias.
- Para comparar taxa de eventos, use exclusivamente `events_per_hour_24h.current_avg` versus `baseline_30d.events_per_hour_avg`.
- Não use `avg_events_24h` no lugar de `events_per_hour_24h.current_avg` na análise de taxa.
- `z_score_24h_vs_30d` = diferença estatística entre o total de eventos das últimas 24h e a média dos últimos 30 dias. Valores entre -2 e 2 são considerados normais.
- Só classifique como anômalo se z_score_24h_vs_30d > 2 ou < -2, mesmo que a média por hora esteja acima da baseline. Se a média por hora estiver acima da baseline, mas o z_score_24h_vs_30d for normal, classifique como NORMAL e apenas destaque a variação como observação.
- Sempre cite explicitamente o valor do z_score_24h_vs_30d na justificativa da classificação.
- Explique seu raciocínio e mostre os cálculos/comparações feitos.
- NÃO compare total de eventos com médias por hora.
- Na análise de proxies, use apenas métricas de proxy como `z_score_vs_own_baseline`, `z_score`, `z_score_last_hour` e `proxy_event_correlations`.
- NÃO use `z_score_24h_vs_30d` para descrever proxies.
- Só afirme correlação direta entre aumento em um proxy e falha do próprio proxy se `proxy_event_correlations.correlation_status = confirmed`.
- Se listas de eventos estiverem vazias, escreva `N/A` e não invente exemplos.

**Exemplo de análise correta:**
- "Se `z_score_24h_vs_30d` estiver entre -2 e 2, classifique o ambiente como NORMAL e trate diferenças de taxa apenas como observação."
- "Na seção de taxa de eventos, use `events_per_hour_24h.current_avg` e `baseline_30d.events_per_hour_avg`."

**Exemplo de análise incorreta (NÃO FAÇA):**
- "Usar números de exemplo ou valores que não existam nos dados fornecidos." (ERRADO)
- "Classifique como anômalo apenas porque a média por hora está acima da baseline, mesmo com z_score normal." (ERRADO)
- "Usar `avg_events_24h` no lugar de `events_per_hour_24h.current_avg` para a análise de taxa." (ERRADO)

{env_block}
**Alertas ativos do grupo '{group_name}':**
```{data_format.lower()}
{group_data}
```

{runbook_block}
{rag_match_block}

**RELATÓRIO DE ANÁLISE DO AMBIENTE**

## 1. ESTADO GERAL DO AMBIENTE
- **Classificação**: [Normal / Anômalo / Anômalo Crítico]
- **Justificativa**: Analise o z_score_24h_vs_30d (valores >2 ou <-2 são anômalos). Sempre cite explicitamente o valor do z_score_24h_vs_30d. Mencione total_events_24h apenas como volume total observado. Para comparar taxa, use exclusivamente `events_per_hour_24h.current_avg` versus `baseline_30d.events_per_hour_avg`. Se a taxa atual estiver acima ou abaixo da baseline, trate isso como observação, sem mudar a classificação quando o z_score_24h_vs_30d for normal.
- **Taxa de Eventos**: Compare events_per_hour_24h.current_avg com baseline_30d.events_per_hour_avg
- **Taxa Crítica**: Analise baseline_30d.critical_ratio
- **Última Hora**: Verifique events_per_hour_24h.z_score_last_hour para identificar picos recentes

## 2. ANÁLISE DE PROXIES
### 2.1 Detecção de Problemas
Identifique os proxies com maior variação no volume de eventos usando os campos próprios de proxy disponíveis em `proxies_top_anomalies` e destaque os top 3:
- Use APENAS os itens de `proxies_top_anomalies` nesta subseção.
- Para ranquear os top 3, priorize primeiro proxies com `z_score_vs_own_baseline` positivo e `last_hour_events > 0`.
- Entre proxies elegíveis, prefira os maiores valores de `z_score_vs_own_baseline`.
- Use `z_score` ou `z_score_last_hour` apenas como contexto adicional, sem trocar o ranking principal.
- Não use `proxies_last_hour` para escolher os top 3 desta subseção.
- Não use `proxy_event_correlations` para ranquear os top 3; use esse bloco apenas para validar correlação.
- NÃO chame métricas de proxy de `z_score_24h_vs_30d`, pois esse campo é exclusivo do ambiente global.
- Para correlação, use prioritariamente `proxy_event_correlations`.
- Só diga que há correlação direta se `correlation_status = confirmed`.
- Se `correlation_status = none`, diga explicitamente que não há evento ativo correlato no próprio proxy.

### 2.2 Proxies Ativos na Última Hora
Use APENAS os 3 itens de `proxies_last_hour`, na mesma ordem em que aparecem nos dados.
- Não substitua proxies desta lista por proxies de `proxies_top_anomalies`.
- Para cada proxy, cite `last_hour_events` e `z_score_last_hour`.
- Destaque como pico recente quando `z_score_last_hour` for positivo e elevado em relação ao baseline do próprio proxy.

## 3. EVENTOS CRÍTICOS ATIVOS
Use APENAS os campos `critical_active_events`, `recent_active_events` e `persistent_noncritical_events`.
- NÃO invente severidade. Use exatamente o valor informado nos dados.
- Se `critical_active_events` estiver vazio, escreva explicitamente: "Não há eventos críticos ativos (severity >= 4) no contexto atual."
- Em seguida, crie dois blocos:
  - **Eventos recentes**: use `recent_active_events` para apontar incidentes abertos recentemente.
  - **Eventos antigos/persistentes**: use `persistent_noncritical_events` para apontar problemas antigos ainda sem tratamento, mesmo que não sejam críticos.
- Para cada evento citado, use este formato:
  - **[SEV X]** Host: [host] | Problema: [problem] | Aberto em: [opened_at] | Idade: [event_age_minutes] min
- Se não houver eventos recentes ou persistentes em cada bloco, diga "N/A".
- Se `critical_active_events`, `recent_active_events` e `persistent_noncritical_events` estiverem todos vazios, escreva exatamente:
  - Não há eventos críticos ativos (severity >= 4) no contexto atual.
  - **Eventos recentes**: N/A
  - **Eventos antigos/persistentes**: N/A
- Não invente hosts, datas, severidades ou problemas quando as listas estiverem vazias.
- Quando todas as listas estiverem vazias, não escreva nenhum item adicional além das três linhas acima.

## 4. AÇÕES RECOMENDADAS
### Ações Imediatas:
1. [Ação específica com nome do recurso e justificativa]
2. [Ação específica com nome do recurso e justificativa]

As ações devem distinguir:
- uma ação para eventos recentes/novos que exigem triagem imediata;
- uma ação para eventos antigos/persistentes que indicam falta de saneamento ou correção estrutural.
- Se não houver eventos críticos, não chame eventos severity 1, 2 ou 3 de "críticos".
- Se não houver eventos ativos nem persistentes, proponha ações de investigação sobre os proxies com maior desvio, sem inventar incidente ativo.
- Se não houver correlação confirmada, não trate proxy anômalo como causa comprovada de falha.
- Só escreva a frase `Problema com match no RAG: ... | Procedimento aplicado: ...` se o bloco **Match RAG determinístico (pré-processado)** estiver presente e não vazio.

Se houver **Procedimento sugerido (RAG)**, inclua obrigatoriamente nas ações a frase:
"Problema com match no RAG: <problema> | Procedimento aplicado: <procedimento resumido>".

---
**Resumo Executivo**: [exatamente 2 frases. A primeira deve resumir o estado global do ambiente. A segunda deve resumir a prioridade operacional com base em proxies e eventos realmente presentes.]
- Não use mais de 2 frases.
- Não reutilize números ou exemplos que não existam literalmente nos dados fornecidos.
"""

    def _create_comparison_prompt(self, group_name: str, comparison: dict) -> str:
        comparison_payload = {
            "previous_timestamp": comparison.get("previous_timestamp"),
            "previous_classification": comparison.get("previous_classification"),
            "current_classification": comparison.get("current_classification"),
            "previous_main_problem": comparison.get("previous_main_problem"),
            "current_main_problem": comparison.get("current_main_problem"),
            "metric_changes": comparison.get("metric_changes") or [],
            "new_top_problems": comparison.get("new_top_problems") or [],
            "resolved_top_problems": comparison.get("resolved_top_problems") or [],
            "new_proxy_anomalies": comparison.get("new_proxy_anomalies") or [],
            "summary": comparison.get("summary") or [],
        }
        comparison_data = json.dumps(comparison_payload, indent=2, default=str, ensure_ascii=False)

        return f"""
Você é um SRE Zabbix. Compare a execução atual do grupo '{group_name}' com a execução anterior.

**REGRAS DE SAÍDA (OBRIGATÓRIAS):**
- Responda APENAS em português.
- Responda APENAS em texto técnico curto; NÃO gere código, comandos, JSON, XML ou HTML.
- Use somente as diferenças fornecidas.
- Se não houver piora relevante, diga isso explicitamente.
- Estruture a resposta exatamente assim:

**Leitura da Diferença**
- **Mudança principal**: ...
- **Impacto operacional**: ...
- **Prioridade**: ...
- **Próxima ação**: ...

**Diferenças da execução:**
```json
{comparison_data}
```
"""

    def _create_investigation_prompt(
        self,
        group_name: str,
        question: str,
        context_bundle: dict,
        chat_history: list | None = None,
    ) -> str:
        serialized_context = json.dumps(context_bundle, indent=2, default=str, ensure_ascii=False)
        serialized_history = json.dumps((chat_history or [])[-6:], indent=2, default=str, ensure_ascii=False)

        return f"""
Você é um SRE Zabbix atuando como assistente investigativo para o grupo '{group_name}'.

**REGRAS DE SAÍDA (OBRIGATÓRIAS):**
- Responda APENAS em português.
- Use APENAS o contexto fornecido.
- Não invente host, proxy, incidente, severidade ou procedimento que não esteja no contexto.
- Se a resposta depender de dado ausente, diga explicitamente "N/A no contexto atual".
- Responda de forma objetiva, operacional e explicativa.
- Não gere código, comandos, JSON, XML ou HTML.
- Não responda com uma única frase curta, exceto se o contexto realmente for insuficiente.
- Sempre desenvolva a resposta com contexto comparativo, evidências e implicação operacional.
- Quando a pergunta envolver piora, mudança, regressão ou comparação, explique:
  - o que mudou;
  - quais evidências sustentam a leitura;
  - qual o impacto provável;
  - o que precisa ser feito a seguir.
- Estruture a resposta exatamente assim:

**Resposta Direta**: [responda a pergunta em 2-4 frases]
**Contexto Relevante**:
- ...
- ...
**Conclusão**: ...
**Evidências**:
- ...
- ...
**Impacto Operacional**:
- ...
- ...
**Próxima ação**:
1. ...
2. ...
**Limitações**: ...

**Histórico recente da conversa:**
```json
{serialized_history}
```

**Contexto investigativo:**
```json
{serialized_context}
```

**Pergunta do analista:**
{question}
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
        if use_rag and not context_runbook:
            runbook_query = self._build_runbook_query(metrics_group, group_name, metrics_env=metrics_env)
            context_runbook = self.get_relevant_runbook(runbook_query, group_name=group_name)

        rag_match_info = ""
        if context_runbook:
            rag_match = self.rag_support.select_rag_match(metrics_group, context_runbook)
            if rag_match:
                rag_match_info = (
                    f"Problema com match no RAG: {rag_match['problem']} | "
                    f"Formato correspondente: {rag_match['pattern']} | "
                    f"Procedimento aplicado: {rag_match['procedure']}"
                )

        if metrics_env:
            self.last_prompt = ""

            logs_dir = Path(__file__).parent / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            prompt_log_file = logs_dir / "last_ai_prompt.txt"
            try:
                final_report, combined_prompt = self.env_audit_report.build_report(
                    metrics_env,
                    group_name,
                    context_runbook=context_runbook,
                    rag_match_info=rag_match_info,
                )
                self.last_prompt = combined_prompt
                with open(prompt_log_file, 'w', encoding='utf-8') as f:
                    f.write(self.last_prompt)
                return final_report
            except requests.exceptions.Timeout:
                logger.exception("⏰ Timeout no fluxo fracionado de env-audit | grupo=%s", group_name)
                return "Erro: Timeout no fluxo fracionado do env-audit. Chamada ao Ollama excedeu o tempo limite."
            except requests.exceptions.RequestException as e:
                logger.exception("❌ Erro no fluxo fracionado de env-audit | grupo=%s", group_name)
                return f"Erro de conectividade: {e}"

        prompt = self._create_prompt(
            metrics_group,
            group_name,
            metrics_env=metrics_env,
            use_toon=use_toon,
            toon_depth=toon_depth,
            context_runbook=context_runbook,
            rag_match_info=rag_match_info,
        )
        self.last_prompt = prompt
        logs_dir = Path(__file__).parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        prompt_log_file = logs_dir / "last_ai_prompt.txt"
        with open(prompt_log_file, 'w', encoding='utf-8') as f:
            f.write(prompt)

        start_time = time.time()
        try:
            ai_response = self.ollama_client.generate_with_default_logging(
                prompt,
                group_name,
                use_toon=use_toon,
                num_predict=1200,
            )

            if self.ollama_client.needs_output_rewrite(ai_response):
                logger.warning(
                    "⚠️ Resposta requer rewrite | grupo=%s | response_chars=%s",
                    group_name,
                    len(ai_response or ""),
                )
                try:
                    ai_response = self.ollama_client.rewrite_to_report_format(ai_response, group_name)
                except requests.exceptions.RequestException:
                    logger.exception("❌ Falha no rewrite da resposta IA | grupo=%s", group_name)
                    pass

            return ai_response
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            logger.error(
                "⏰ Timeout na chamada Ollama | grupo=%s | modelo=%s | timeout=%ss | prompt_chars=%s | duracao=%.1fs",
                group_name,
                self.model,
                OLLAMA_TIMEOUT,
                len(prompt or ""),
                elapsed,
            )
            return f"Erro: Timeout após {elapsed:.0f}s. Chamada ao Ollama excedeu o tempo limite."
        except requests.exceptions.RequestException as e:
            logger.error(
                "❌ Erro de conectividade com Ollama | grupo=%s | modelo=%s | prompt_chars=%s | erro=%s",
                group_name,
                self.model,
                len(prompt or ""),
                e,
            )
            return f"Erro de conectividade: {e}"

    def analyze_raw_prompt(self, prompt: str, group_name: str = "prompt-inject") -> str:
        self.last_prompt = prompt or ""
        start_time = time.time()
        try:
            ai_response = self.ollama_client.generate_with_default_logging(
                self.last_prompt,
                group_name,
                use_toon=False,
                num_predict=1200,
            )
            elapsed = time.time() - start_time
            logger.info(
                "✅ Prompt Inject concluído | grupo=%s | duracao=%.1fs | response_chars=%s",
                group_name,
                elapsed,
                len(ai_response or ""),
            )
            return ai_response
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            logger.error(
                "⏰ Timeout no Prompt Inject | grupo=%s | modelo=%s | timeout=%ss | prompt_chars=%s | duracao=%.1fs",
                group_name,
                self.model,
                OLLAMA_TIMEOUT,
                len(self.last_prompt),
                elapsed,
            )
            return f"Erro: Timeout após {elapsed:.0f}s. Chamada ao Ollama excedeu o tempo limite."
        except requests.exceptions.RequestException as error:
            logger.error(
                "❌ Erro de conectividade no Prompt Inject | grupo=%s | modelo=%s | prompt_chars=%s | erro=%s",
                group_name,
                self.model,
                len(self.last_prompt),
                error,
            )
            return f"Erro de conectividade: {error}"

    def analyze_execution_comparison(self, group_name: str, comparison: dict) -> str:
        if not comparison:
            return ""

        start_time = time.time()
        try:
            return self.ollama_client.generate(
                self._create_comparison_prompt(group_name, comparison),
                group_name,
                "execution-comparison",
                num_predict=220,
            ).strip()
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            return f"Erro: Timeout após {elapsed:.0f}s na análise da diferença."
        except requests.exceptions.RequestException as error:
            return f"Erro de conectividade na análise da diferença: {error}"

    def analyze_investigation(
        self,
        group_name: str,
        question: str,
        context_bundle: dict,
        chat_history: list | None = None,
    ) -> str:
        start_time = time.time()
        try:
            return self.ollama_client.generate(
                self._create_investigation_prompt(group_name, question, context_bundle, chat_history=chat_history),
                group_name,
                "investigation",
                num_predict=850,
            ).strip()
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            return f"Erro: Timeout após {elapsed:.0f}s no assistente investigativo."
        except requests.exceptions.RequestException as error:
            return f"Erro de conectividade no assistente investigativo: {error}"
