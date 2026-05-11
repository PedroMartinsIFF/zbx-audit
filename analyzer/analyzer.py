import json
import sys
import statistics
import re
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Adicionar diretório atual ao path para imports relativos
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import ZABBIX_URL, ZABBIX_USER, ZABBIX_PASSWORD, OLLAMA_MODEL, OLLAMA_API_URL, get_runtime_ollama_settings
from db.db import get_db_connection, setup_database, init_connection_pool
from shared.models import EventData
from db.event_repository import EventRepository
from shared.types_contracts import StructuredMetrics, EnvironmentAudit
from shared.utils import extract_proxy_name, is_control_group_event, get_correlation_window
from zabbix.zabbix_client import ZabbixCollector
from analyzer.baseline import GroupBaseline
from ai.ai import OllamaAnalyzer
from ai.ai_standard import StandardOllamaAnalyzer
from shared.logger_config import LoggerSetup

logger = LoggerSetup.get_logger(__name__)

ENV_AUDIT_GROUP = "Zabbix/Servico"


class GroupZabbixAnalyzer:
    def __init__(self):
        logger.debug("Inicializando GroupZabbixAnalyzer...")
        setup_database()
        init_connection_pool()
        logger.info("✅ Banco de dados inicializado")
        
        self.collector = ZabbixCollector(ZABBIX_URL, ZABBIX_USER, ZABBIX_PASSWORD)
        logger.debug("✅ ZabbixCollector inicializado")
        
        self.baseline_analyzer = GroupBaseline()
        logger.debug("✅ GroupBaseline inicializado")
        
        self.event_repository = EventRepository()
        logger.debug("✅ EventRepository inicializado")

        self.ollama_env_analyzer = None
        self.ollama_standard_analyzer = None
        self.last_execution_comparison = None

    @staticmethod
    def _get_runtime_ollama_model_config() -> tuple[str, str]:
        model, api_url, _timeout = get_runtime_ollama_settings()
        return model, api_url

    def store_event(self, event_data: EventData):
        """Armazena evento no banco de dados com logging estruturado"""
        try:
            event_status = "Recovery" if event_data.event_value == 0 else "Problem"

            logger.debug(f"Armazenando evento: {event_data.event_id} ({event_status})")
            self.event_repository.upsert_event(event_data, event_status)
            logger.debug(f"✅ Evento {event_data.event_id} armazenado com sucesso")
        except Exception as e:
            logger.error(f"❌ Erro ao armazenar evento {event_data.event_id}: {e}", exc_info=True)

    @staticmethod
    def _extract_ai_history_metadata(response: str) -> Dict[str, Any]:
        text = (response or "").strip()
        if not text:
            return {
                "classification": None,
                "risk_level": None,
                "main_problem": None,
                "summary": None,
                "recommended_actions": [],
            }

        def _extract(pattern: str) -> str | None:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if not match:
                return None
            value = match.group(1).strip()
            return value or None

        def _extract_block(start_pattern: str) -> str | None:
            match = re.search(
                rf"{start_pattern}\s*:?\s*(.+?)(?:\n##\s|\Z)",
                text,
                flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
            )
            if not match:
                return None
            value = match.group(1).strip()
            return value or None

        classification = _extract(r"\*\*Classificação\*\*:\s*(.+)")
        if not classification:
            classification = _extract(r"\*\*Estado:\*\*\s*([^\n-]+)")

        risk_level = None
        if classification:
            normalized = classification.lower()
            if "cr[ií]tico" in normalized:
                risk_level = "critico"
            elif "an[oô]malo" in normalized:
                risk_level = "alto"
            elif "normal" in normalized:
                risk_level = "baixo"

        top_problem_match = re.search(r"\*\*Top 3 Problemas:\*\*\s*(.+)", text, flags=re.IGNORECASE)
        main_problem = None
        if top_problem_match:
            top_problem_text = top_problem_match.group(1).strip()
            main_problem = top_problem_text.split(" - ")[0].split(",")[0].strip() or None

        if not main_problem:
            rag_problem = _extract(r"Problema com match no RAG:\s*([^|.\n]+)")
            main_problem = rag_problem

        summary = _extract_block(r"\*\*Resumo Executivo\*\*")
        if not summary:
            summary = _extract(r"\*\*Justificativa\*\*:\s*(.+)")

        recommended_actions = []
        action_matches = re.findall(r"^\s*\d+\.\s+(.+)$", text, flags=re.MULTILINE)
        for action in action_matches[:5]:
            action = action.strip()
            if action:
                recommended_actions.append(action)

        return {
            "classification": classification,
            "risk_level": risk_level,
            "main_problem": main_problem,
            "summary": summary,
            "recommended_actions": recommended_actions,
        }

    @staticmethod
    def _build_metrics_snapshot(
        metrics: StructuredMetrics,
        prompt_variant: str = "default",
        metrics_env: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if prompt_variant == "env-audit" and metrics_env:
            return {
                "prompt_variant": prompt_variant,
                "total_events": metrics_env.get("total_events_24h", 0),
                "critical_events": len(
                    [
                        event for event in (metrics_env.get("active_events") or [])
                        if isinstance(event, dict) and int(event.get("severity", 0)) >= 4
                    ]
                ),
                "events_per_hour_avg": metrics_env.get("avg_events_24h", 0),
                "anomaly_score": metrics_env.get("z_score_24h_vs_30d", 0),
                "z_score": metrics_env.get("z_score_24h_vs_30d", 0),
                "top_problems": [],
                "top_hosts": [],
                "proxy_anomalies": [
                    str(item.get("proxy"))
                    for item in (metrics_env.get("proxies_top_anomalies") or [])[:5]
                    if isinstance(item, dict) and item.get("proxy")
                ],
            }

        baseline_analysis = metrics.get("baseline_analysis", {})
        anomaly_detection = baseline_analysis.get("anomaly_detection", {})
        current_hourly_rate = metrics.get("total_events", 0) / max(1, metrics.get("hours_analyzed", 1))
        return {
            "prompt_variant": prompt_variant,
            "total_events": metrics.get("total_events", 0),
            "critical_events": metrics.get("critical_events", 0),
            "events_per_hour_avg": round(current_hourly_rate, 2),
            "anomaly_score": round(anomaly_detection.get("anomaly_score", 0), 3),
            "z_score": round(anomaly_detection.get("volume_deviation", 0), 3),
            "top_problems": [
                str(item.get("name") or item.get("problem_name") or item.get("problem"))
                for item in (metrics.get("top_problems") or [])[:5]
                if isinstance(item, dict) and (item.get("name") or item.get("problem_name") or item.get("problem"))
            ],
            "top_hosts": [
                str(item[0] if isinstance(item, tuple) else item.get("host_name") or item.get("host"))
                for item in (metrics.get("top_hosts") or [])[:5]
                if (
                    isinstance(item, tuple)
                    or (isinstance(item, dict) and (item.get("host_name") or item.get("host")))
                )
            ],
            "proxy_anomalies": [
                str(item.get("proxy"))
                for item in anomaly_detection.get("proxy_anomalies", [])[:5]
                if isinstance(item, dict) and item.get("proxy")
            ],
        }

    @staticmethod
    def _build_execution_comparison(
        previous_entry: Optional[Dict[str, Any]],
        current_metadata: Dict[str, Any],
        current_snapshot: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not previous_entry:
            return None

        previous_snapshot = previous_entry.get("metrics_snapshot") or {}
        metric_changes: List[str] = []

        def _append_delta(label: str, key: str, precision: int = 2) -> None:
            previous_value = previous_snapshot.get(key)
            current_value = current_snapshot.get(key)
            if previous_value is None or current_value is None:
                return
            delta = current_value - previous_value
            if abs(delta) < (0.001 if precision > 0 else 1):
                return
            metric_changes.append(
                f"{label}: {previous_value:.{precision}f} -> {current_value:.{precision}f} ({delta:+.{precision}f})"
            )

        _append_delta("Eventos totais", "total_events", precision=0)
        _append_delta("Eventos críticos", "critical_events", precision=0)
        _append_delta("Eventos/hora", "events_per_hour_avg", precision=2)
        _append_delta("Anomaly score", "anomaly_score", precision=3)
        _append_delta("Z-score", "z_score", precision=3)

        previous_classification = previous_entry.get("classification")
        current_classification = current_metadata.get("classification")
        classification_changed = previous_classification != current_classification and (
            previous_classification or current_classification
        )

        previous_problem = previous_entry.get("main_problem")
        current_problem = current_metadata.get("main_problem")
        main_problem_changed = previous_problem != current_problem and (previous_problem or current_problem)

        previous_top_problems = set(previous_snapshot.get("top_problems") or [])
        current_top_problems = set(current_snapshot.get("top_problems") or [])
        new_top_problems = sorted(current_top_problems - previous_top_problems)
        resolved_top_problems = sorted(previous_top_problems - current_top_problems)

        previous_proxy_anomalies = set(previous_snapshot.get("proxy_anomalies") or [])
        current_proxy_anomalies = set(current_snapshot.get("proxy_anomalies") or [])

        highlights = []
        if classification_changed:
            highlights.append(
                f"Classificação alterada: {previous_classification or 'N/A'} -> {current_classification or 'N/A'}"
            )
        if main_problem_changed:
            highlights.append(
                f"Problema principal alterado: {previous_problem or 'N/A'} -> {current_problem or 'N/A'}"
            )
        if new_top_problems:
            highlights.append(f"Novos problemas relevantes: {', '.join(new_top_problems[:3])}")
        if resolved_top_problems:
            highlights.append(f"Problemas que saíram do topo: {', '.join(resolved_top_problems[:3])}")

        new_proxy_anomalies = sorted(current_proxy_anomalies - previous_proxy_anomalies)
        if new_proxy_anomalies:
            highlights.append(f"Novas anomalias por proxy: {', '.join(new_proxy_anomalies[:3])}")

        return {
            "previous_timestamp": previous_entry.get("timestamp"),
            "previous_classification": previous_classification,
            "current_classification": current_classification,
            "previous_main_problem": previous_problem,
            "current_main_problem": current_problem,
            "metric_changes": metric_changes,
            "new_top_problems": new_top_problems,
            "resolved_top_problems": resolved_top_problems,
            "new_proxy_anomalies": new_proxy_anomalies,
            "summary": highlights,
        }

    @staticmethod
    def _normalize_identifier(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @classmethod
    def _is_proxy_host_match(cls, proxy_name: Any, host_name: Any) -> bool:
        proxy_norm = cls._normalize_identifier(proxy_name)
        host_norm = cls._normalize_identifier(host_name)
        if not proxy_norm or not host_norm:
            return False
        return proxy_norm == host_norm or proxy_norm in host_norm or host_norm in proxy_norm

    @classmethod
    def _classify_proxy_correlation(
        cls,
        proxy_name: str,
        event: Dict[str, Any],
        last_hour_events: Any,
        baseline_last_hour_avg: Any,
        z_score_vs_own_baseline: Any,
    ) -> tuple[str, str]:
        event_proxy = event.get("proxy_name")
        event_host = event.get("host")
        has_literal_match = (
            (event_proxy and cls._is_proxy_host_match(proxy_name, event_proxy))
            or cls._is_proxy_host_match(proxy_name, event_host)
        )
        if not has_literal_match:
            return "suspected", "Evento próximo ao contexto do proxy, mas sem vínculo literal explícito."

        try:
            last_hour_value = float(last_hour_events or 0)
        except Exception:
            last_hour_value = 0.0
        try:
            baseline_value = float(baseline_last_hour_avg or 0)
        except Exception:
            baseline_value = 0.0
        try:
            own_zscore = float(z_score_vs_own_baseline or 0)
        except Exception:
            own_zscore = 0.0
        event_age_minutes = event.get("event_age_minutes")
        try:
            event_age_minutes = int(event_age_minutes) if event_age_minutes is not None else None
        except Exception:
            event_age_minutes = None

        has_relevant_increase = own_zscore >= 2
        has_recent_event = event_age_minutes is not None and event_age_minutes <= 90

        if has_relevant_increase and has_recent_event:
            return "confirmed", "Evento ativo no próprio proxy com aumento relevante acima do baseline do proxy."

        if not has_relevant_increase and last_hour_value > 0 and baseline_value > 0:
            return "suspected", "Há evento ativo no próprio proxy, mas o desvio em relação ao baseline do proxy ainda é baixo."

        if not has_recent_event:
            return "suspected", "Há evento no próprio proxy, mas ele está antigo para explicar o aumento recente."

        return "suspected", "Há evento ativo no próprio proxy, mas sem aumento relevante no baseline do proxy."

    def store_ollama_response(
        self,
        group_name: str,
        response: str,
        ai_prompt: Optional[str] = None,
        metrics_snapshot: Optional[Dict[str, Any]] = None,
    ):
        """Armazena resposta da IA com logging estruturado"""
        try:
            logger.debug(f"Armazenando resposta da IA para grupo: {group_name}")
            structured_data = self._extract_ai_history_metadata(response)
            self.event_repository.insert_ollama_response(
                group_name,
                response,
                self._get_runtime_ollama_model_config()[0],
                ai_prompt=ai_prompt,
                structured_data=structured_data,
                metrics_snapshot=metrics_snapshot,
            )
            logger.info(f"✅ Resposta da IA armazenada para grupo '{group_name}'")
        except Exception as e:
            logger.error(f"❌ Erro ao armazenar resposta da IA para {group_name}: {e}", exc_info=True)

    def get_structured_metrics(
        self,
        group_name: str,
        hours_back: int = 24,
        include_baseline: bool = True,
    ) -> StructuredMetrics:
        """Obtém métricas estruturadas para um grupo com logging detalhado"""
        try:
            logger.debug(f"Consultando métricas para grupo '{group_name}' (últimas {hours_back} horas)")
            raw_metrics = self.event_repository.fetch_group_metrics_bundle(group_name, hours_back)

            zabbix_runbook = raw_metrics['zabbix_runbook']
            total_events = raw_metrics['total_events']
            proxy_analysis = raw_metrics['proxy_analysis']
            top_hostgroups = raw_metrics['top_hostgroups']
            time_correlations = raw_metrics['time_correlations']
            proxy_failures = raw_metrics['proxy_failures']
            critical_events = raw_metrics['critical_events']
            top_problems_raw = raw_metrics['top_problems_raw']
            problem_host_pairs = raw_metrics['problem_host_pairs']
            top_hosts = raw_metrics['top_hosts']
            rows_24h = raw_metrics['rows_24h']
            rows_7d = raw_metrics['rows_7d']
            trend_raw = raw_metrics['trend_raw']
            trend_problems = raw_metrics['trend_problems']

            logger.debug(f"✅ Consultadas métricas: {total_events} eventos totais, {len(proxy_analysis)} proxies")

            critical_count = len(critical_events)
            proxy_event_distribution = {}
            total_proxy_events = sum(count for _, count, _ in proxy_analysis)
            if total_proxy_events > 0:
                proxy_event_distribution = {
                    proxy: (count / total_proxy_events) * 100 for proxy, count, _ in proxy_analysis
                }
            top_problems = [{'name': name, 'count': count} for name, count in top_problems_raw]
            critical_events_details = [
                {
                    "host_name": host_name,
                    "problem_name": problem_name,
                    "timestamp": timestamp,
                    "proxy_name": proxy_name,
                }
                for host_name, problem_name, timestamp, proxy_name in critical_events
            ]
            problem_host_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for problem_name, host_name, proxy_name, last_seen, max_severity, event_count in problem_host_pairs:
                problem_host_map[str(problem_name)].append(
                    {
                        "host_name": host_name,
                        "proxy_name": proxy_name,
                        "last_seen": last_seen,
                        "severity": max_severity,
                        "event_count": event_count,
                    }
                )

            top_problem_details = []
            for item in top_problems[:10]:
                problem_name = item["name"]
                top_problem_details.append(
                    {
                        "problem_name": problem_name,
                        "count": item["count"],
                        "affected_entities": problem_host_map.get(problem_name, [])[:5],
                    }
                )

            proxy_24h = defaultdict(list)
            proxy_7d = defaultdict(list)
            for hour, proxy, count in rows_24h:
                proxy_24h[proxy].append(count)
            for hour, proxy, count in rows_7d:
                proxy_7d[proxy].append(count)

            summary_proxy_events = {}
            for proxy in proxy_24h:
                counts_24h = proxy_24h[proxy]
                counts_7d = proxy_7d.get(proxy, [])
                summary_proxy_events[proxy] = {
                    "total_events": sum(counts_24h),
                    "avg_per_hour": round(sum(counts_24h) / max(1, len(counts_24h)), 2),
                    "avg_per_hour_7_days": round(sum(counts_7d) / max(1, len(counts_7d)), 2) if counts_7d else 0,
                    "max": max(counts_24h) if counts_24h else 0,
                    "min": min(counts_24h) if counts_24h else 0,
                    "last": counts_24h[-1] if counts_24h else 0,
                }

            hourly_data = defaultdict(lambda: {
                'event_count': 0,
                'unique_hosts': 0,
                'unique_hostgroups': 0,
                'critical_events': 0,
                'proxy_events': defaultdict(int),
                'severity_distribution': defaultdict(int),
                'problems': defaultdict(int)
            })

            for hour, event_count, unique_hosts, unique_hostgroups, critical_events_count, proxy_name, severity in trend_raw:
                data = hourly_data[hour]
                data['event_count'] += event_count
                data['critical_events'] += critical_events_count
                data['unique_hosts'] = max(data['unique_hosts'], unique_hosts)
                data['unique_hostgroups'] = max(data['unique_hostgroups'], unique_hostgroups)
                if proxy_name:
                    data['proxy_events'][proxy_name] += event_count
                data['severity_distribution'][str(severity)] += event_count

            for hour, problem_name, count in trend_problems:
                hourly_data[hour]['problems'][problem_name] += count

            trend_analysis = []
            for hour, data in sorted(hourly_data.items()):
                top_problems_hour = sorted(data['problems'].items(), key=lambda x: x[1], reverse=True)[:5]
                trend_analysis.append({
                    'period': 'hourly',
                    'timestamp': datetime.strptime(hour, '%Y-%m-%d %H:%M:%S'),
                    'event_count': data['event_count'],
                    'unique_hosts': data['unique_hosts'],
                    'unique_hostgroups': data['unique_hostgroups'],
                    'critical_events': data['critical_events'],
                    'proxy_events': dict(data['proxy_events']),
                    'severity_distribution': dict(data['severity_distribution']),
                    'top_problems': top_problems_hour,
                })

            base_metrics = {
                'group_name': group_name,
                'zabbix_runbook': zabbix_runbook,
                'top_hostgroups': top_hostgroups,
                'proxy_analysis': {
                    'raw_data': proxy_analysis,
                    'event_distribution': proxy_event_distribution,
                    'summary': summary_proxy_events,
                },
                'time_correlations': time_correlations,
                'critical_events': critical_count,
                'critical_events_details': critical_events_details,
                'proxy_failures': proxy_failures,
                'hosts_without_notification': self.get_hosts_without_notification(group_name, hours_back),
                'analysis_period': f"{hours_back} horas",
                'total_events': total_events,
                'hours_analyzed': hours_back,
                'top_problems': top_problems,
                'top_problem_details': top_problem_details,
                'top_hosts': top_hosts,
            }

            if include_baseline:
                baseline = self.baseline_analyzer.calculate_baseline(group_name)
                anomalies = self.baseline_analyzer.detect_anomalies(base_metrics, baseline, group_name)
                base_metrics.update({
                    'baseline_analysis': {
                        'baseline_metrics': asdict(baseline),
                        'anomaly_detection': asdict(anomalies),
                        'trend_analysis': trend_analysis[-24:] if trend_analysis else [],
                        'environment_health': {
                            'normal_event_rate': f"{baseline.events_per_hour_avg:.1f}±{baseline.events_per_hour_std:.1f} eventos/hora",
                            'current_event_rate': f"{total_events / max(1, hours_back):.1f} eventos/hora",
                            'anomaly_score': anomalies.anomaly_score,
                            'is_anomalous': anomalies.anomaly_score > 0.3,
                        },
                    }
                })
            logger.info(f"✅ Métricas calculadas para '{group_name}': {total_events} eventos")
            return base_metrics
        
        except Exception as e:
            logger.error(f"❌ Erro ao obter métricas para {group_name}: {e}", exc_info=True)
            raise

    def build_investigation_context(
        self,
        group_name: str,
        hours_back: int = 24,
    ) -> Dict[str, Any]:
        metrics = self.get_structured_metrics(
            group_name=group_name,
            hours_back=hours_back,
            include_baseline=True,
        )
        history_entries = self.get_ollama_history(group_name=group_name, limit=2)
        latest_history = history_entries[0] if history_entries else None
        previous_history = history_entries[1] if len(history_entries) > 1 else None

        last_saved_comparison = None
        if latest_history and previous_history:
            latest_metadata = {
                "classification": latest_history.get("classification"),
                "risk_level": latest_history.get("risk_level"),
                "main_problem": latest_history.get("main_problem"),
                "summary": latest_history.get("summary"),
                "recommended_actions": latest_history.get("recommended_actions") or [],
            }
            last_saved_comparison = self._build_execution_comparison(
                previous_history,
                latest_metadata,
                latest_history.get("metrics_snapshot") or {},
            )

        baseline_analysis = metrics.get("baseline_analysis", {})
        anomaly_detection = baseline_analysis.get("anomaly_detection", {})
        baseline_metrics = baseline_analysis.get("baseline_metrics", {})
        proxy_raw = metrics.get("proxy_analysis", {}).get("raw_data", [])
        top_problem_details = metrics.get("top_problem_details", [])
        critical_events_details = metrics.get("critical_events_details", [])
        new_problems = anomaly_detection.get("new_problems", [])[:5]
        new_problem_details = []
        for problem_name in new_problems:
            matching_detail = next(
                (item for item in top_problem_details if item.get("problem_name") == problem_name),
                None,
            )
            if matching_detail:
                new_problem_details.append(matching_detail)
            else:
                new_problem_details.append(
                    {
                        "problem_name": problem_name,
                        "count": None,
                        "affected_entities": [],
                    }
                )

        return {
            "group_name": group_name,
            "analysis_period": metrics.get("analysis_period"),
            "hours_back": hours_back,
            "current_metrics": {
                "total_events": metrics.get("total_events", 0),
                "critical_events": metrics.get("critical_events", 0),
                "critical_events_details": critical_events_details[:10],
                "top_problems": metrics.get("top_problems", [])[:5],
                "top_problem_details": top_problem_details[:10],
                "top_hosts": metrics.get("top_hosts", [])[:5],
                "top_proxies": [
                    {
                        "proxy_name": row[0],
                        "event_count": row[1],
                        "affected_hosts": row[2],
                    }
                    for row in proxy_raw[:5]
                ],
                "hosts_without_notification_count": len(metrics.get("hosts_without_notification", [])),
            },
            "baseline_summary": {
                "events_per_hour_avg": baseline_metrics.get("events_per_hour_avg", 0),
                "events_per_hour_std": baseline_metrics.get("events_per_hour_std", 0),
                "critical_events_ratio_avg": baseline_metrics.get("critical_events_ratio_avg", 0),
                "anomaly_score": anomaly_detection.get("anomaly_score", 0),
                "volume_z_score": anomaly_detection.get("volume_deviation", 0),
                "new_problems": new_problems,
                "new_problem_details": new_problem_details,
                "proxy_anomalies": anomaly_detection.get("proxy_anomalies", [])[:5],
            },
            "latest_ai_history": (
                {
                    "timestamp": latest_history.get("timestamp"),
                    "classification": latest_history.get("classification"),
                    "risk_level": latest_history.get("risk_level"),
                    "main_problem": latest_history.get("main_problem"),
                    "summary": latest_history.get("summary"),
                    "recommended_actions": latest_history.get("recommended_actions") or [],
                }
                if latest_history
                else None
            ),
            "last_execution_comparison": last_saved_comparison,
            "runbook_excerpt": (metrics.get("zabbix_runbook") or "")[:2000],
        }

    def run_investigation_chat(
        self,
        group_name: str,
        question: str,
        context_bundle: Dict[str, Any],
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        runtime_model, runtime_api_url = self._get_runtime_ollama_model_config()
        self._ensure_ai_analyzer_model(runtime_model, runtime_api_url)

        response = self.ollama_standard_analyzer.analyze_investigation(
            group_name,
            question,
            context_bundle,
            chat_history=chat_history,
        )
        return response

    def initialize_ai_analyzer(self, model: str, api_url: str):
        """Inicializa analisador de IA com logging"""
        try:
            logger.info(f"Inicializando OllamaAnalyzer com modelo: {model}")
            self.ollama_env_analyzer = OllamaAnalyzer(model, api_url)
            self.ollama_standard_analyzer = StandardOllamaAnalyzer(model, api_url)
            logger.debug(f"✅ OllamaAnalyzer pronto para {api_url}")
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar IA: {e}", exc_info=True)
            raise

    def _ensure_ai_analyzer_model(self, model: str, api_url: str) -> None:
        current_env_model = getattr(self.ollama_env_analyzer, "model", None)
        current_standard_model = getattr(self.ollama_standard_analyzer, "model", None)

        if (
            not self.ollama_env_analyzer
            or not self.ollama_standard_analyzer
            or current_env_model != model
            or current_standard_model != model
        ):
            logger.info(
                "🔄 Reinicializando analyzers de IA | modelo_atual_env=%s | modelo_atual_standard=%s | modelo_desejado=%s",
                current_env_model,
                current_standard_model,
                model,
            )
            self.initialize_ai_analyzer(model, api_url)

    def run_ai_analysis(
        self,
        metrics: StructuredMetrics,
        group_name: str,
        use_toon=False,
        toon_depth=3,
        prompt_variant: str = "default",
        metrics_env=None,
    ):
        """Executa análise com IA e registra resultado"""
        try:
            runtime_model, runtime_api_url = self._get_runtime_ollama_model_config()
            self._ensure_ai_analyzer_model(runtime_model, runtime_api_url)
            
            logger.debug(f"Iniciando análise de IA para grupo '{group_name}'")

            baseline_analysis = metrics.get('baseline_analysis', {})
            baseline_metrics = baseline_analysis.get('baseline_metrics', {})
            anomaly_detection = baseline_analysis.get('anomaly_detection', {})
            current_hourly_rate = metrics.get('total_events', 0) / max(1, metrics.get('hours_analyzed', 1))
            current_critical_ratio = metrics.get('critical_events', 0) / max(1, metrics.get('total_events', 1))
            
            metrics_for_ai = {
                'group_name': metrics.get('group_name'),
                'zabbix_runbook': metrics.get('zabbix_runbook', ''),
                'top_problems': metrics.get('top_problems', [])[:5],
                'top_problem_details': metrics.get('top_problem_details', [])[:10],
                'top_hosts': metrics.get('top_hosts', [])[:3],
                'group_current_window': {
                    'total_events': metrics.get('total_events', 0),
                    'hours_analyzed': metrics.get('hours_analyzed', 1),
                    'events_per_hour_avg': round(current_hourly_rate, 2),
                    'critical_events': metrics.get('critical_events', 0),
                    'critical_events_ratio': round(current_critical_ratio, 4),
                },
                'group_baseline': {
                    'events_per_hour_avg': round(baseline_metrics.get('events_per_hour_avg', 0), 2),
                    'events_per_hour_std': round(baseline_metrics.get('events_per_hour_std', 0), 2),
                    'critical_events_ratio_avg': round(baseline_metrics.get('critical_events_ratio_avg', 0), 4),
                    'unique_hosts_avg': round(baseline_metrics.get('unique_hosts_avg', 0), 2),
                    'top_problems_baseline': baseline_metrics.get('top_problems_baseline', [])[:5],
                },
                'group_anomaly_analysis': {
                    'z_score_group_vs_baseline': round(anomaly_detection.get('volume_deviation', 0), 3),
                    'critical_ratio_deviation': round(anomaly_detection.get('critical_ratio_deviation', 0), 4),
                    'is_anomalous_volume': anomaly_detection.get('is_anomalous_volume', False),
                    'is_anomalous_critical_ratio': anomaly_detection.get('is_anomalous_critical_ratio', False),
                    'new_problems': anomaly_detection.get('new_problems', [])[:5],
                    'proxy_anomalies': anomaly_detection.get('proxy_anomalies', [])[:5],
                    'anomaly_score': round(anomaly_detection.get('anomaly_score', 0), 3),
                },
            }

            context_runbook = metrics_for_ai.get('zabbix_runbook') or ""
            use_rag = False
            if not context_runbook:
                logger.info("🔍 Zabbix sem runbook para '%s'. Usando fallback RAG.", group_name)
                use_rag = True

            selected_analyzer = (
                self.ollama_env_analyzer
                if prompt_variant == "env-audit"
                else self.ollama_standard_analyzer
            )
            
            ai_response = selected_analyzer.analyze_metrics(
                metrics_for_ai,
                group_name,
                metrics_env=metrics_env,
                use_toon=use_toon,
                toon_depth=toon_depth,
                context_runbook=context_runbook,
                use_rag=use_rag,
            )
            
            if ai_response and not ai_response.startswith("Erro de conectividade:"):
                logger.info(f"✅ Resposta da IA recebida para '{group_name}'")
                previous_entry_raw = self.event_repository.fetch_latest_ollama_response(group_name)
                previous_entry = None
                if previous_entry_raw:
                    (
                        prev_timestamp,
                        prev_groupname,
                        prev_response,
                        prev_ai_prompt,
                        prev_model,
                        prev_classification,
                        prev_risk_level,
                        prev_main_problem,
                        prev_summary,
                        prev_recommended_actions,
                        prev_metrics_snapshot,
                    ) = previous_entry_raw
                    previous_entry = {
                        "timestamp": datetime.fromtimestamp(prev_timestamp),
                        "groupname": prev_groupname,
                        "response": prev_response,
                        "ai_prompt": prev_ai_prompt,
                        "model": prev_model,
                        "classification": prev_classification,
                        "risk_level": prev_risk_level,
                        "main_problem": prev_main_problem,
                        "summary": prev_summary,
                        "recommended_actions": prev_recommended_actions or [],
                        "metrics_snapshot": prev_metrics_snapshot or {},
                    }

                current_metadata = self._extract_ai_history_metadata(ai_response)
                current_snapshot = self._build_metrics_snapshot(
                    metrics,
                    prompt_variant=prompt_variant,
                    metrics_env=metrics_env,
                )
                self.last_execution_comparison = self._build_execution_comparison(
                    previous_entry,
                    current_metadata,
                    current_snapshot,
                )
                self.store_ollama_response(
                    group_name,
                    ai_response,
                    ai_prompt=getattr(selected_analyzer, "last_prompt", "") or "",
                    metrics_snapshot=current_snapshot,
                )
            else:
                logger.warning(f"⚠️ Resposta da IA indisponível para '{group_name}'")
                self.last_execution_comparison = None
            
            return ai_response
        
        except Exception as e:
            logger.error(f"❌ Erro ao executar análise de IA para {group_name}: {e}", exc_info=True)
            raise

    def run_env_audit_prompt_inject(self, prompt_text: str, group_name: str = ENV_AUDIT_GROUP) -> str:
        runtime_model, runtime_api_url = self._get_runtime_ollama_model_config()
        self._ensure_ai_analyzer_model(runtime_model, runtime_api_url)

        logger.info("🧪 Executando Prompt Inject do env-audit para '%s'", group_name)
        return self.ollama_env_analyzer.analyze_raw_prompt(prompt_text, group_name=group_name)

    def run_group_prompt_inject(self, prompt_text: str, group_name: str) -> str:
        runtime_model, runtime_api_url = self._get_runtime_ollama_model_config()
        self._ensure_ai_analyzer_model(runtime_model, runtime_api_url)

        logger.info("🧪 Executando Prompt Inject de métricas por grupo para '%s'", group_name)
        return self.ollama_standard_analyzer.analyze_raw_prompt(prompt_text, group_name=group_name)

    def get_hosts_without_notification(self, group_name: str, hours_back: int = 24) -> List[Dict]:
        try:
            since_timestamp = int((datetime.now() - timedelta(hours=hours_back)).timestamp())
            hosts_data = self.event_repository.fetch_hosts_with_groups(group_name, since_timestamp)

            notification_keywords = ['_Notificacao/', 'Operacao/', '_Operacao/', 'Notificacao/']
            hosts_without_notification = []

            for host_name, hostgroups_json in hosts_data:
                try:
                    hostgroups = hostgroups_json if isinstance(hostgroups_json, list) else json.loads(hostgroups_json)
                    has_notification = any(
                        any(keyword in group for keyword in notification_keywords)
                        for group in hostgroups
                    )

                    if not has_notification:
                        hosts_without_notification.append({
                            'host': host_name,
                            'grupos': hostgroups,
                        })
                except Exception:
                    continue

            return hosts_without_notification[:50]
        except Exception as e:
            logger.warning(f"⚠️ Erro ao buscar hosts sem notificação: {e}")
            return []

    def get_environment_audit(self, active_group_name: Optional[str] = None) -> EnvironmentAudit:
        """Gera auditoria do ambiente com baseline global e métricas por proxy"""
        try:
            active_group_name = active_group_name or ENV_AUDIT_GROUP
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # Períodos de análise
                    cutoff_24h = int((datetime.now() - timedelta(hours=24)).timestamp())
                    cutoff_30d = int((datetime.now() - timedelta(days=30)).timestamp())
                    
                    # Total eventos últimas 24h
                    cursor.execute(
                        """
                        SELECT COUNT(*) 
                        FROM events 
                        WHERE timestamp >= %s
                          AND event_status = 'Problem'
                        """,
                        (cutoff_24h,)
                    )
                    total_events_24h = cursor.fetchone()[0]
                    
                    if total_events_24h == 0:
                        return {
                            'total_events_24h': 0,
                            'message': 'Nenhum evento nas últimas 24 horas',
                        }
                    
                    # Média de eventos por hora nas últimas 24h
                    avg_events_24h = total_events_24h / 24.0
                    
                    # Baseline dos últimos 30 dias (excluindo últimas 24h para comparação)
                    cursor.execute(
                        """
                        SELECT 
                            to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24') as hour,
                            COUNT(*) as event_count,
                            COUNT(DISTINCT host_name) as unique_hosts,
                            SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END) as critical_events
                        FROM events 
                        WHERE timestamp >= %s 
                        AND timestamp < %s
                        AND event_status = 'Problem'
                        GROUP BY hour
                        ORDER BY hour
                        """,
                        (cutoff_30d, cutoff_24h)
                    )
                    baseline_hourly_data = cursor.fetchall()
                    
                    # Dados das últimas 24h por hora
                    cursor.execute(
                        """
                        SELECT 
                            to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24') as hour,
                            COUNT(*) as event_count,
                            COUNT(DISTINCT host_name) as unique_hosts,
                            SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END) as critical_events
                        FROM events 
                        WHERE timestamp >= %s
                        AND event_status = 'Problem'
                        GROUP BY hour
                        ORDER BY hour
                        """,
                        (cutoff_24h,)
                    )
                    current_24h_hourly_data = cursor.fetchall()
                    
                    # Calcular baseline (30 dias excluindo últimas 24h)
                    baseline_event_counts = [row[1] for row in baseline_hourly_data]
                    baseline_events_per_hour_avg = statistics.mean(baseline_event_counts) if baseline_event_counts else 0
                    baseline_events_per_hour_std = statistics.stdev(baseline_event_counts) if len(baseline_event_counts) > 1 else 0
                    
                    # Z-score das últimas 24h comparado ao baseline de 30 dias
                    z_score_24h = ((avg_events_24h - baseline_events_per_hour_avg) / baseline_events_per_hour_std) if baseline_events_per_hour_std > 0 else 0
                    
                    # Dados por hora das últimas 24h
                    current_event_counts = [row[1] for row in current_24h_hourly_data]
                    current_events_per_hour_avg = statistics.mean(current_event_counts) if current_event_counts else 0
                    current_events_per_hour_std = statistics.stdev(current_event_counts) if len(current_event_counts) > 1 else 0
                    
                    # Z-score de eventos por hora (variação dentro das 24h)
                    last_hour_count = current_event_counts[-1] if current_event_counts else 0
                    z_score_events_per_hour = ((last_hour_count - current_events_per_hour_avg) / current_events_per_hour_std) if current_events_per_hour_std > 0 else 0
                    
                    # Top problemas baseline
                    cursor.execute(
                        """
                        SELECT problem_name, COUNT(*) as count
                        FROM events 
                        WHERE timestamp >= %s
                          AND event_status = 'Problem'
                        GROUP BY problem_name
                        ORDER BY count DESC
                        LIMIT 20
                        """,
                        (cutoff_30d,)
                    )
                    problem_data = cursor.fetchall()
                    baseline_hours = len(baseline_event_counts) if baseline_event_counts else 1
                    top_problems_baseline = [(prob, count / baseline_hours) for prob, count in problem_data]

                    # Estatísticas de proxies (últimas 24h)
                    cutoff_last_hour = int((datetime.now() - timedelta(hours=1)).timestamp())
                    cursor.execute(
                        """
                        SELECT proxy_name, COUNT(*) as count
                        FROM events 
                        WHERE timestamp >= %s
                          AND proxy_name IS NOT NULL
                          AND event_status = 'Problem'
                        GROUP BY proxy_name
                        ORDER BY count DESC
                        """,
                        (cutoff_24h,)
                    )
                    proxy_counts_24h = cursor.fetchall()
                    proxy_count_values = [count for _, count in proxy_counts_24h]
                    proxy_mean = statistics.mean(proxy_count_values) if proxy_count_values else 0
                    proxy_std = statistics.stdev(proxy_count_values) if len(proxy_count_values) > 1 else 0

                    cursor.execute(
                        """
                        SELECT
                            proxy_name,
                            COUNT(*) as total_events,
                            SUM(CASE WHEN timestamp >= %s THEN 1 ELSE 0 END) as last_hour_events
                        FROM events
                        WHERE timestamp >= %s
                          AND proxy_name IS NOT NULL
                          AND event_status = 'Problem'
                        GROUP BY proxy_name
                        ORDER BY total_events DESC
                        """,
                        (cutoff_last_hour, cutoff_24h)
                    )
                    proxy_rows = cursor.fetchall()

                    cursor.execute(
                        """
                        SELECT
                            proxy_name,
                            to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24') as hour_bucket,
                            COUNT(*) as event_count
                        FROM events
                        WHERE timestamp >= %s
                          AND timestamp < %s
                          AND proxy_name IS NOT NULL
                          AND event_status = 'Problem'
                        GROUP BY proxy_name, hour_bucket
                        ORDER BY proxy_name, hour_bucket
                        """,
                        (cutoff_30d, cutoff_24h)
                    )
                    proxy_baseline_rows = cursor.fetchall()

            proxy_stats = []
            last_hour_values = [row[2] or 0 for row in proxy_rows]
            last_hour_mean = statistics.mean(last_hour_values) if last_hour_values else 0
            last_hour_std = statistics.stdev(last_hour_values) if len(last_hour_values) > 1 else 0
            proxy_hourly_baseline = defaultdict(list)
            for proxy_name, _hour_bucket, event_count in proxy_baseline_rows:
                if proxy_name:
                    proxy_hourly_baseline[str(proxy_name)].append(int(event_count or 0))

            for proxy, total_24h, last_hour_events in proxy_rows:
                baseline_counts = proxy_hourly_baseline.get(str(proxy), [])
                own_baseline_avg = statistics.mean(baseline_counts) if baseline_counts else 0
                own_baseline_std = statistics.stdev(baseline_counts) if len(baseline_counts) > 1 else 0
                z_score = (total_24h - proxy_mean) / proxy_std if proxy_std > 0 else 0
                z_score_last_hour = (last_hour_events - last_hour_mean) / last_hour_std if last_hour_std > 0 else 0
                z_score_own_baseline = (
                    (last_hour_events - own_baseline_avg) / own_baseline_std
                    if own_baseline_std > 0
                    else 0
                )
                proxy_stats.append({
                    'proxy': proxy,
                    'total_events_24h': total_24h,
                    'avg_per_hour': round(total_24h / 24.0, 2),
                    'last_hour_events': last_hour_events or 0,
                    'z_score': round(z_score, 3),
                    'z_score_last_hour': round(z_score_last_hour, 3),
                    'baseline_last_hour_avg_30d': round(own_baseline_avg, 2),
                    'baseline_last_hour_std_30d': round(own_baseline_std, 2),
                    'z_score_vs_own_baseline': round(z_score_own_baseline, 3),
                })

            # Calcular taxa crítica 24h
            critical_events_24h = sum([row[3] for row in current_24h_hourly_data])
            critical_ratio_24h = (critical_events_24h / total_events_24h) if total_events_24h > 0 else 0

            try:
                self.collector.connect()
                hostgroups = self.collector.zapi.hostgroup.get(
                    filter={'name': active_group_name},
                    output=['groupid']
                )
                if hostgroups:
                    group_id = hostgroups[0]['groupid']
                    problems = self.collector.zapi.problem.get(
                        groupids=[group_id],
                        output=['eventid', 'clock', 'name', 'severity'],
                        sortfield=['clock'],
                        sortorder='DESC'
                    )
                    event_ids = [p.get('eventid') for p in problems if p.get('eventid')]
                    hosts_map = {}
                    if event_ids:
                        events = self.collector.zapi.event.get(
                            eventids=event_ids,
                            output=['eventid'],
                            selectHosts=['host', 'hostid']
                        )
                        for event in events:
                            hosts = event.get('hosts') or []
                            if hosts:
                                hosts_map[event.get('eventid')] = hosts[0].get('host')

                    active_events_formatted = [
                        {
                            'event_id': problem.get('eventid'),
                            'host': hosts_map.get(problem.get('eventid')),
                            'severity': int(problem.get('severity', 0)) if str(problem.get('severity', '')).isdigit() else problem.get('severity'),
                            'problem': problem.get('name'),
                            'opened_at': datetime.fromtimestamp(int(problem.get('clock'))).isoformat() if str(problem.get('clock', '')).isdigit() else None,
                            'event_age_minutes': (
                                int((datetime.now() - datetime.fromtimestamp(int(problem.get('clock')))).total_seconds() // 60)
                                if str(problem.get('clock', '')).isdigit()
                                else None
                            ),
                        }
                        for problem in problems
                    ][:10]  # Limitar a 10 eventos ativos
                else:
                    active_events_formatted = []
            except Exception as e:
                logger.warning(f"⚠️ Erro ao buscar problems ativos do grupo {active_group_name}: {e}")
                active_events_formatted = []
            finally:
                try:
                    self.collector.disconnect()
                except Exception:
                    pass

            if not active_events_formatted:
                try:
                    active_rows = self.event_repository.fetch_active_events_for_group(active_group_name, limit=10)
                    active_events_formatted = [
                        {
                            'event_id': event_id,
                            'host': host_name,
                            'severity': severity,
                            'problem': problem_name,
                            'opened_at': datetime.fromtimestamp(timestamp).isoformat() if timestamp else None,
                            'event_age_minutes': (
                                int((datetime.now() - datetime.fromtimestamp(timestamp)).total_seconds() // 60)
                                if timestamp
                                else None
                            ),
                            'proxy_name': proxy_name,
                            'source': 'db_fallback',
                        }
                        for event_id, host_name, severity, problem_name, timestamp, proxy_name in active_rows
                        if timestamp and int((datetime.now() - datetime.fromtimestamp(timestamp)).total_seconds() // 60) <= 90
                    ]
                except Exception as fallback_error:
                    logger.warning(
                        "⚠️ Erro no fallback de active_events via banco para grupo %s: %s",
                        active_group_name,
                        fallback_error,
                    )

            active_events_formatted = sorted(
                active_events_formatted,
                key=lambda item: item.get('opened_at') or "",
                reverse=True,
            )
            critical_active_events = [
                event for event in active_events_formatted
                if isinstance(event, dict) and int(event.get('severity', 0)) >= 4
            ]
            recent_active_events = [
                event for event in active_events_formatted
                if isinstance(event, dict) and event.get('event_age_minutes') is not None and event.get('event_age_minutes') <= 60
            ]
            persistent_active_events = [
                event for event in active_events_formatted
                if isinstance(event, dict) and event.get('event_age_minutes') is not None and event.get('event_age_minutes') >= 180
            ]
            persistent_noncritical_events = [
                event for event in persistent_active_events
                if int(event.get('severity', 0)) < 4
            ]

            sorted_proxy_stats = sorted(
                proxy_stats,
                key=lambda item: max(
                    abs(float(item.get('z_score_vs_own_baseline', 0) or 0)),
                    abs(float(item.get('z_score_last_hour', 0) or 0)),
                    abs(float(item.get('z_score', 0) or 0)),
                ),
                reverse=True,
            )

            proxy_event_correlations = []
            for proxy_item in sorted_proxy_stats[:5]:
                proxy_name = proxy_item.get('proxy')
                if not proxy_name:
                    continue

                matching_events = []
                for event in active_events_formatted:
                    if not isinstance(event, dict):
                        continue
                    event_proxy = event.get('proxy_name')
                    event_host = event.get('host')
                    if (
                        self._is_proxy_host_match(proxy_name, event_proxy)
                        or self._is_proxy_host_match(proxy_name, event_host)
                    ):
                        matching_events.append(event)

                matching_events = sorted(
                    matching_events,
                    key=lambda item: (
                        item.get('event_age_minutes') is None,
                        item.get('event_age_minutes') or 10 ** 9,
                        -(int(item.get('severity', 0)) if str(item.get('severity', '')).isdigit() else 0),
                    ),
                )

                correlation_status = "none"
                correlation_reason = "Sem evento ativo no mesmo proxy ou host do proxy."
                if matching_events:
                    correlation_status, correlation_reason = self._classify_proxy_correlation(
                        proxy_name,
                        matching_events[0],
                        proxy_item.get('last_hour_events', 0),
                        proxy_item.get('baseline_last_hour_avg_30d', 0),
                        proxy_item.get('z_score_vs_own_baseline', 0),
                    )

                proxy_event_correlations.append({
                    'proxy': proxy_name,
                    'last_hour_events': proxy_item.get('last_hour_events', 0),
                    'total_events_24h': proxy_item.get('total_events_24h', 0),
                    'baseline_last_hour_avg_30d': proxy_item.get('baseline_last_hour_avg_30d', 0),
                    'baseline_last_hour_std_30d': proxy_item.get('baseline_last_hour_std_30d', 0),
                    'z_score_vs_own_baseline': proxy_item.get('z_score_vs_own_baseline', 0),
                    'z_score_last_hour_peers': proxy_item.get('z_score_last_hour', 0),
                    'proxy_host_match': bool(matching_events),
                    'correlation_status': correlation_status,
                    'correlation_reason': correlation_reason,
                    'matching_active_events': matching_events[:3],
                })

            # Resultado compacto e focado nas últimas 24h
            return {
                'period': '24h',
                'total_events_24h': total_events_24h,
                'avg_events_24h': round(avg_events_24h, 2),
                'z_score_24h_vs_30d': round(z_score_24h, 3),
                'events_per_hour_24h': {
                    'current_avg': round(current_events_per_hour_avg, 2),
                    'current_std': round(current_events_per_hour_std, 2),
                    'last_hour': last_hour_count,
                    'z_score_last_hour': round(z_score_events_per_hour, 3),
                },
                'baseline_30d': {
                    'events_per_hour_avg': round(baseline_events_per_hour_avg, 2),
                    'events_per_hour_std': round(baseline_events_per_hour_std, 2),
                    'critical_ratio': f"{critical_ratio_24h*100:.1f}%",
                    'top_problems': top_problems_baseline[:3],
                },
                'proxies_top_anomalies': sorted_proxy_stats[:5],
                'proxies_last_hour': sorted([p for p in proxy_stats if p['last_hour_events'] > 0], key=lambda x: x['last_hour_events'], reverse=True)[:3],
                'proxy_event_correlations': proxy_event_correlations,
                'active_events_group': active_group_name,
                'active_events': active_events_formatted,
                'critical_active_events': critical_active_events[:10],
                'recent_active_events': recent_active_events[:10],
                'persistent_noncritical_events': persistent_noncritical_events[:10],
            }

        except Exception as e:
            logger.error(f"❌ Erro ao gerar auditoria do ambiente: {e}", exc_info=True)
            return {'error': str(e)}

    def collect_all_events(self, hours_back: int = 24) -> int:
        """Coleta TODOS os eventos do Zabbix e armazena no banco de dados"""
        try:
            logger.info(f"🌍 COLETA GLOBAL: Buscando TODOS os eventos do Zabbix (últimas {hours_back} horas)")
            
            self.collector.connect()
            events = self.collector.get_all_events(hours_back)
            
            if not events:
                logger.warning(f"⚠️ Nenhum evento encontrado no Zabbix")
                self.collector.disconnect()
                return 0
            
            self.collector.preload_host_cache(events)
            logger.info(f"💾 Processando {len(events)} eventos do Zabbix...")
            stored_count = 0
            
            for i, event in enumerate(events):
                try:
                    hosts = event.get('hosts', [])
                    if not hosts:
                        continue
                    
                    host_info = self.collector.get_host_info(hosts[0]['hostid'])
                    if not host_info:
                        continue
                    
                    hostgroups = [hg['name'] for hg in host_info.get('hostgroups', [])]
                    event_timestamp = datetime.fromtimestamp(int(event['clock']))
                    
                    # Calcular duração para eventos de recovery
                    event_value = int(event.get('value', 1))
                    event_duration = None
                    r_eventid = event.get('r_eventid', '0')
                    
                    if event_value == 0 and r_eventid != '0':  # É recovery
                        problem_timestamp = self.collector.get_problem_event_timestamp(r_eventid)
                        if problem_timestamp:
                            event_duration = int(event['clock']) - problem_timestamp
                            logger.debug(f"Duração calculada: {event_duration}s")
                    
                    event_data = EventData(
                        event_id=event['eventid'],
                        timestamp=event_timestamp,
                        host_name=host_info.get('host', 'Desconhecido'),
                        hostgroups=hostgroups,
                        proxy_name=extract_proxy_name(hostgroups),
                        severity=int(event['severity']),
                        problem_name=event.get('name', 'Problema sem nome'),
                        is_control_group=is_control_group_event(hostgroups),
                        correlation_window=get_correlation_window(event_timestamp),
                        event_value=event_value,
                        r_eventid=r_eventid if r_eventid != '0' else None,
                        event_duration=event_duration,
                    )
                    self.store_event(event_data)
                    stored_count += 1
                except Exception as e:
                    logger.debug(f"Erro ao processar evento: {e}")
                    continue
                
                if i % 500 == 0 and i > 0:
                    progress = (i / len(events)) * 100
                    logger.info(f"   📊 Progresso: {progress:.1f}% ({i}/{len(events)})")
            
            self.collector.disconnect()
            logger.info(f"✅ {stored_count}/{len(events)} eventos armazenados com sucesso")
            return stored_count
        
        except Exception as e:
            logger.error(f"❌ Erro ao coletar todos os eventos: {e}", exc_info=True)
            raise

    def get_ollama_history(self, group_name: str = None, limit: int = 10) -> List[Dict]:
        results = self.event_repository.fetch_ollama_history(group_name, limit)
        history = []
        for timestamp, groupname, response, ai_prompt, model, classification, risk_level, main_problem, summary, recommended_actions, metrics_snapshot in results:
            history.append({
                'timestamp': datetime.fromtimestamp(timestamp),
                'groupname': groupname,
                'response': response,
                'ai_prompt': ai_prompt,
                'model': model,
                'classification': classification,
                'risk_level': risk_level,
                'main_problem': main_problem,
                'summary': summary,
                'recommended_actions': recommended_actions or [],
                'metrics_snapshot': metrics_snapshot or {},
            })
        return history

    def get_last_execution_comparison(self) -> Optional[Dict[str, Any]]:
        return self.last_execution_comparison

    def generate_comparison_ai_analysis(
        self,
        group_name: str,
        comparison: Dict[str, Any],
        prompt_variant: str = "default",
    ) -> Dict[str, Any]:
        if not comparison:
            return comparison

        runtime_model, runtime_api_url = self._get_runtime_ollama_model_config()
        self._ensure_ai_analyzer_model(runtime_model, runtime_api_url)

        selected_analyzer = (
            self.ollama_env_analyzer
            if prompt_variant == "env-audit"
            else self.ollama_standard_analyzer
        )

        updated_comparison = dict(comparison)
        try:
            comparison_ai_analysis = selected_analyzer.analyze_execution_comparison(
                group_name,
                updated_comparison,
            )
            if comparison_ai_analysis and not comparison_ai_analysis.startswith("Erro"):
                updated_comparison["ai_analysis"] = comparison_ai_analysis
                updated_comparison.pop("ai_analysis_error", None)
            elif comparison_ai_analysis:
                updated_comparison["ai_analysis_error"] = comparison_ai_analysis
        except Exception as comparison_error:
            error_message = str(comparison_error)
            updated_comparison["ai_analysis_error"] = error_message
            logger.warning(
                "⚠️ Falha ao gerar análise de IA da diferença para '%s': %s",
                group_name,
                comparison_error,
            )

        self.last_execution_comparison = updated_comparison
        return updated_comparison

    def get_database_stats(self) -> Dict:
        """Retorna estatísticas do banco de dados"""
        try:
            return self.event_repository.get_basic_stats()
        except Exception as e:
            logger.error(f"❌ Erro ao obter estatísticas do banco: {e}", exc_info=True)
            return {'error': str(e)}

    def clear_database(self) -> Dict:
        """Limpa todos os eventos do banco de dados"""
        try:
            deleted_count = self.event_repository.clear_events_and_responses()
            logger.info(f"✅ Banco de dados limpo: {deleted_count} eventos deletados")
            return {'success': True, 'deleted_count': deleted_count}
        except Exception as e:
            logger.error(f"❌ Erro ao limpar banco de dados: {e}", exc_info=True)
            return {'error': str(e)}
