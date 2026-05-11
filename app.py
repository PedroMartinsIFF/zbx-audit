import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from typing import Any, Dict, List, Optional

import altair as alt
import pandas as pd
import streamlit as st

from analyzer.analyzer import GroupZabbixAnalyzer
from analyzer.analyzer import ENV_AUDIT_GROUP
from shared.config import OLLAMA_API_URL, OLLAMA_MODEL
from shared.config import DB_HOST, DB_PORT, DB_NAME, DB_USER
from shared.config import get_optional_env
from db.db import get_db_connection
from integrations.notification_hub import NotificationHubClient


st.set_page_config(page_title="Zabbix Audit", page_icon="📊", layout="wide")

DEBUG_LOGS_DIR = Path("/opt/audit-rag/logs")
DEBUG_LOG_FILES = [
    "env_audit_last_output.json",
    "last_ai_prompt.txt",
    "last_output.json",
    "zbx-audit-errors.log",
    "zbx-audit.log",
]

HUB_DEFAULT_URL = get_optional_env("NOTIFICATION_HUB_URL", "https://api.notificationhub.globoi.com/statistics")
HUB_DEFAULT_TOKEN = get_optional_env("NOTIFICATION_HUB_TOKEN", "")

PROMPT_INJECT_COMPONENTS = {
    "env_section_2_1": {
        "label": "Env-Audit: 2.1 Detecção de Problemas",
        "group_name": ENV_AUDIT_GROUP,
        "runner": "env",
        "placeholder": "Cole aqui apenas o prompt reduzido da seção 2.1 do env-audit.",
    },
    "env_actions_summary": {
        "label": "Env-Audit: 4 Ações + Resumo",
        "group_name": ENV_AUDIT_GROUP,
        "runner": "env",
        "placeholder": "Cole aqui apenas o prompt reduzido de ações e resumo do env-audit.",
    },
    "env_full": {
        "label": "Env-Audit: prompt completo",
        "group_name": ENV_AUDIT_GROUP,
        "runner": "env",
        "placeholder": "Cole aqui um prompt completo de env-audit.",
    },
    "group_section_2_1": {
        "label": "Grupo: 2.1 Interpretação dos Desvios",
        "group_name": "Zabbix/Servico/Proxy",
        "runner": "group",
        "placeholder": "Cole aqui apenas o prompt reduzido da seção 2.1 da análise por grupo.",
    },
    "group_actions_summary": {
        "label": "Grupo: 3 Ações + Resumo",
        "group_name": "Zabbix/Servico/Proxy",
        "runner": "group",
        "placeholder": "Cole aqui apenas o prompt reduzido de ações e resumo da análise por grupo.",
    },
    "group_full": {
        "label": "Grupo: prompt completo",
        "group_name": "Zabbix/Servico/Proxy",
        "runner": "group",
        "placeholder": "Cole aqui um prompt completo de métricas por grupo.",
    },
}


@st.cache_resource
def get_analyzer() -> GroupZabbixAnalyzer:
    return GroupZabbixAnalyzer()


def _to_serializable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_serializable(v) for v in value]
    if isinstance(value, tuple):
        return [_to_serializable(v) for v in value]
    return value


def _show_error(prefix: str, error: Exception) -> None:
    st.error(f"{prefix}: {error}")


def _read_debug_file(path: Path, max_chars: int = 20000) -> str:
    try:
        if not path.exists():
            return "Arquivo não encontrado."
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            return content[-max_chars:]
        return content
    except Exception as error:
        return f"Falha ao ler arquivo: {error}"


def _metric_cards(metrics: Dict[str, Any]) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Eventos", metrics.get("total_events", 0))
    col2.metric("Críticos", metrics.get("critical_events", 0))
    col3.metric("Período", metrics.get("analysis_period", "N/A"))

    proxy_raw = metrics.get("proxy_analysis", {}).get("raw_data", [])
    col4.metric("Proxies", len(proxy_raw))


def _render_ai_analysis_block(ai_text: str, comparison: Optional[Dict[str, Any]], text_area_key: str) -> None:
    if ai_text:
        st.markdown("### Análise da IA (execução atual)")
        st.text_area("Resposta", value=ai_text, height=260, disabled=True, key=text_area_key)
        _render_execution_comparison(comparison)
    else:
        st.info("Execute a rotina para exibir a análise da IA desta execução.")


def _render_investigation_messages(messages: List[Dict[str, str]]) -> None:
    for message in messages:
        role = message.get("role", "assistant")
        content = message.get("content", "")
        with st.chat_message("user" if role == "user" else "assistant"):
            st.markdown(content)


def _render_execution_comparison(comparison: Optional[Dict[str, Any]], title: str = "Comparação com a última execução") -> None:
    if not comparison:
        st.info("Sem execução anterior compatível para comparar.")
        return

    st.markdown(f"### {title}")
    previous_timestamp = comparison.get("previous_timestamp")
    if previous_timestamp:
        st.caption(f"Base de comparação: {_to_serializable(previous_timestamp)}")

    summary_items = comparison.get("summary") or []
    if summary_items:
        for item in summary_items:
            st.write(f"- {item}")

    metric_changes = comparison.get("metric_changes") or []
    if metric_changes:
        st.markdown("**Mudanças numéricas**")
        for item in metric_changes:
            st.write(f"- {item}")

    ai_analysis = comparison.get("ai_analysis")
    if ai_analysis:
        comparison_key = _to_serializable(previous_timestamp) or "current"
        st.markdown("**Leitura da IA sobre a diferença**")
        st.text_area(
            "Análise da diferença",
            value=ai_analysis,
            height=180,
            disabled=True,
            key=f"comparison_ai_analysis_{title}_{comparison_key}",
        )
    elif comparison.get("ai_analysis_error"):
        st.warning(comparison.get("ai_analysis_error"))

    if not summary_items and not metric_changes:
        st.write("- Nenhuma mudança relevante detectada em relação à última execução.")


def _valid_multiselect_defaults(options: List[str], saved_values: List[str]) -> List[str]:
    option_set = set(options)
    return [value for value in saved_values if value in option_set]


def _get_latest_ai_report(group_name: str) -> str:
    try:
        entries = get_analyzer().get_ollama_history(group_name=group_name, limit=1)
        if not entries:
            return ""
        return entries[0].get("response", "") or ""
    except Exception:
        return ""


def _fetch_dashboard_data(
    group_name: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(hours=24)
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT problem_name, COUNT(*) AS total
                FROM events
                                WHERE timestamp >= %s
                                    AND timestamp <= %s
                  AND (%s IS NULL OR hostgroups::jsonb ? %s)
                GROUP BY problem_name
                ORDER BY total DESC
                LIMIT 5;
                """,
                                (start_ts, end_ts, group_name, group_name),
            )
            top_problems_24h = [
                {"problem_name": row[0], "total": row[1]} for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT
                    proxy_name,
                    COUNT(*) AS total_eventos_24h,
                    COUNT(DISTINCT host_name) AS hosts_afetados_24h
                FROM events
                                WHERE timestamp >= %s
                                    AND timestamp <= %s
                  AND (%s IS NULL OR hostgroups::jsonb ? %s)
                GROUP BY proxy_name
                ORDER BY total_eventos_24h DESC;
                """,
                                (start_ts, end_ts, group_name, group_name),
            )
            proxy_distribution_24h = [
                {
                    "proxy_name": row[0] or "SEM_PROXY",
                    "total_eventos_24h": row[1],
                    "hosts_afetados_24h": row[2],
                }
                for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT
                    date_trunc('hour', to_timestamp(timestamp)) AS time,
                    COUNT(*) AS total_eventos
                FROM events
                                WHERE timestamp >= %s
                                    AND timestamp <= %s
                                    AND (%s IS NULL OR hostgroups::jsonb ? %s)
                GROUP BY time
                ORDER BY time;
                """,
                                (start_ts, end_ts, group_name, group_name),
            )
            events_by_hour = [
                {"time": row[0].isoformat(), "total_eventos": row[1]}
                for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT host_name, COUNT(*) AS total_eventos_24h
                FROM events
                                WHERE timestamp >= %s
                                    AND timestamp <= %s
                  AND (%s IS NULL OR hostgroups::jsonb ? %s)
                GROUP BY host_name
                ORDER BY total_eventos_24h DESC
                LIMIT 10;
                """,
                                (start_ts, end_ts, group_name, group_name),
            )
            top_hosts_24h = [
                {"host_name": row[0], "total_eventos_24h": row[1]} for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT
                    date_trunc('hour', to_timestamp(timestamp)) AS time,
                    proxy_name AS metric,
                    COUNT(*) AS value
                FROM events
                WHERE timestamp >= %s
                  AND timestamp <= %s
                  AND (%s IS NULL OR hostgroups::jsonb ? %s)
                GROUP BY time, metric
                ORDER BY time, metric;
                """,
                                (start_ts, end_ts, group_name, group_name),
            )
            events_by_proxy_hour = [
                {"time": row[0].isoformat(), "metric": row[1] or "SEM_PROXY", "value": row[2]}
                for row in cursor.fetchall()
            ]

    return {
        "top_problems_24h": top_problems_24h,
        "proxy_distribution_24h": proxy_distribution_24h,
        "events_by_hour": events_by_hour,
        "top_hosts_24h": top_hosts_24h,
        "events_by_proxy_hour": events_by_proxy_hour,
    }


def _render_pie_chart(values: List[Dict[str, Any]], label_field: str, value_field: str, title: str) -> None:
    if not values:
        st.info(f"Sem dados para {title.lower()}.")
        return

    df = pd.DataFrame(values)
    if df.empty or value_field not in df.columns or label_field not in df.columns:
        st.info(f"Sem dados para {title.lower()}.")
        return

    df[value_field] = pd.to_numeric(df[value_field], errors="coerce").fillna(0)
    df = df[df[value_field] > 0]
    if df.empty:
        st.info(f"Sem dados para {title.lower()}.")
        return

    chart = (
        alt.Chart(df)
        .mark_arc(outerRadius=120)
        .encode(
            theta=alt.Theta(field=value_field, type="quantitative"),
            color=alt.Color(field=label_field, type="nominal"),
            tooltip=[label_field, value_field],
        )
        .properties(title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_line_chart(values: List[Dict[str, Any]], x_field: str, y_field: str, title: str, color_field: str = None) -> None:
    if not values:
        st.info(f"Sem dados para {title.lower()}.")
        return

    df = pd.DataFrame(values)
    if df.empty or x_field not in df.columns or y_field not in df.columns:
        st.info(f"Sem dados para {title.lower()}.")
        return

    df[x_field] = pd.to_datetime(df[x_field], errors="coerce")
    df[y_field] = pd.to_numeric(df[y_field], errors="coerce")
    df = df.dropna(subset=[x_field, y_field])
    df = df.sort_values(by=[x_field] + ([color_field] if color_field and color_field in df.columns else []))
    if df.empty:
        st.info(f"Sem dados para {title.lower()}.")
        return

    if color_field and color_field in df.columns:
        chart = (
            alt.Chart(df)
            .mark_line(point=True)
            .encode(
                x=alt.X(f"{x_field}:T", title="Tempo"),
                y=alt.Y(f"{y_field}:Q"),
                color=alt.Color(f"{color_field}:N"),
                tooltip=[x_field, y_field, color_field],
            )
            .properties(title=title)
        )
    else:
        chart = (
            alt.Chart(df)
            .mark_line()
            .encode(
                x=alt.X(f"{x_field}:T", title="Tempo"),
                y=alt.Y(f"{y_field}:Q"),
                tooltip=[x_field, y_field],
            )
            .properties(title=title)
        )

    st.altair_chart(chart, use_container_width=True)


def _render_proxy_24h_chart_with_filter(
    values: List[Dict[str, Any]],
    title: str,
    key_prefix: str,
    default_show_all: bool = False,
) -> None:
    if not values:
        st.info("Sem dados para eventos por proxy por hora.")
        return

    proxy_options = sorted({str(item.get("metric")) for item in values if item.get("metric")})
    if not proxy_options:
        st.info("Sem proxies disponíveis para filtrar.")
        return

    show_all = st.checkbox(
        "Mostrar todos os proxies",
        value=default_show_all,
        key=f"{key_prefix}_show_all_proxies",
    )

    default_selected = proxy_options[: min(10, len(proxy_options))]
    selected_proxies = st.multiselect(
        "Proxies exibidos",
        options=proxy_options,
        default=proxy_options if show_all else default_selected,
        disabled=show_all,
        key=f"{key_prefix}_selected_proxies",
    )

    selected_proxy_names = proxy_options if show_all else selected_proxies
    if not selected_proxy_names:
        st.info("Selecione ao menos um proxy para exibir o gráfico.")
        return

    filtered_values = [row for row in values if row.get("metric") in selected_proxy_names]
    if not filtered_values:
        st.info("Nenhum dado para os proxies selecionados.")
        return

    _render_line_chart(
        filtered_values,
        x_field="time",
        y_field="value",
        title=title,
        color_field="metric",
    )


def _render_dashboard_sections(
    dashboard_data: Dict[str, Any],
    key_prefix: str,
    empty_message: str,
    include_raw_expander: bool = False,
) -> None:
    if not dashboard_data:
        st.info(empty_message)
        return

    st.markdown("### TOP Problemas")
    st.dataframe(dashboard_data.get("top_problems_24h", []), use_container_width=True)
    _render_pie_chart(
        dashboard_data.get("top_problems_24h", []),
        label_field="problem_name",
        value_field="total",
        title="Top problemas (24h)",
    )

    st.markdown("### Distribuição de eventos por proxy")
    st.dataframe(dashboard_data.get("proxy_distribution_24h", []), use_container_width=True)

    st.markdown("### Variação de eventos por hora")
    _render_line_chart(
        dashboard_data.get("events_by_hour", []),
        x_field="time",
        y_field="total_eventos",
        title="Variação de eventos por hora",
    )

    st.markdown("### Top hosts")
    st.dataframe(dashboard_data.get("top_hosts_24h", []), use_container_width=True)
    _render_pie_chart(
        dashboard_data.get("top_hosts_24h", []),
        label_field="host_name",
        value_field="total_eventos_24h",
        title="Top hosts (24h)",
    )

    st.markdown("### Eventos por proxy por hora")
    _render_proxy_24h_chart_with_filter(
        dashboard_data.get("events_by_proxy_hour", []),
        title="Eventos por proxy por hora",
        key_prefix=key_prefix,
    )

    if include_raw_expander:
        with st.expander("Ver dados brutos do dashboard"):
            st.json(_to_serializable(dashboard_data))


def _extract_report_block(report: str, start_header: str, next_headers: List[str]) -> str:
    raw = (report or "").strip()
    if not raw or start_header not in raw:
        return ""

    start = raw.find(start_header)
    end_positions = [raw.find(header, start + len(start_header)) for header in next_headers if raw.find(header, start + len(start_header)) != -1]
    end = min(end_positions) if end_positions else len(raw)
    return raw[start:end].strip()


def _render_env_overview_panels(env_audit: Dict[str, Any]) -> None:
    baseline_30d = env_audit.get("baseline_30d", {})
    events_per_hour_24h = env_audit.get("events_per_hour_24h", {})
    z_score = float(env_audit.get("z_score_24h_vs_30d") or 0)
    classification = "NORMAL" if -2 <= z_score <= 2 else "ANÔMALO"

    row1 = st.columns(4)
    row1[0].metric("Classificação", classification)
    row1[1].metric("Z-score 24h x 30d", f"{z_score:.3f}")
    row1[2].metric("Total de Eventos 24h", env_audit.get("total_events_24h", 0))
    row1[3].metric("Taxa Crítica", baseline_30d.get("critical_ratio", "N/A"))

    current_avg = events_per_hour_24h.get("current_avg", env_audit.get("avg_events_24h", 0))
    baseline_avg = baseline_30d.get("events_per_hour_avg", 0)
    last_hour_z = events_per_hour_24h.get("z_score_last_hour", 0)
    last_hour_events = events_per_hour_24h.get("last_hour", 0)

    row2 = st.columns(4)
    row2[0].metric("Taxa Atual", f"{current_avg} ev/h")
    row2[1].metric("Baseline 30d", f"{baseline_avg} ev/h")
    row2[2].metric("Eventos na Última Hora", last_hour_events)
    row2[3].metric("Z-score Última Hora", f"{last_hour_z:.3f}" if isinstance(last_hour_z, (int, float)) else str(last_hour_z))


def _render_env_last_hour_chart(proxies_last_hour: List[Dict[str, Any]]) -> None:
    if not proxies_last_hour:
        st.info("Sem dados de proxies ativos na última hora.")
        return

    rows = []
    for item in proxies_last_hour[:3]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "proxy": item.get("proxy") or "N/A",
                "last_hour_events": pd.to_numeric(item.get("last_hour_events"), errors="coerce"),
                "z_score_last_hour": pd.to_numeric(item.get("z_score_last_hour"), errors="coerce"),
            }
        )

    df = pd.DataFrame(rows).dropna(subset=["last_hour_events"])
    if df.empty:
        st.info("Sem dados de proxies ativos na última hora.")
        return

    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6)
        .encode(
            x=alt.X("proxy:N", sort=None, title="Proxy"),
            y=alt.Y("last_hour_events:Q", title="Eventos na última hora"),
            color=alt.Color("z_score_last_hour:Q", title="Z-score última hora"),
            tooltip=["proxy", "last_hour_events", "z_score_last_hour"],
        )
        .properties(title="Proxies ativos na última hora")
    )
    st.altair_chart(chart, use_container_width=True)


def _render_env_events_table(env_audit: Dict[str, Any]) -> None:
    rows = []
    for bucket_name, events in (
        ("Crítico", env_audit.get("critical_active_events") or []),
        ("Recente", env_audit.get("recent_active_events") or []),
        ("Persistente", env_audit.get("persistent_noncritical_events") or []),
    ):
        for item in events:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "tipo": bucket_name,
                    "severity": item.get("severity"),
                    "host": item.get("host"),
                    "problema": item.get("problem"),
                    "aberto_em": item.get("opened_at"),
                    "idade_min": item.get("event_age_minutes"),
                }
            )

    if not rows:
        st.info("Não há eventos críticos ativos nem eventos recentes/persistentes no contexto atual.")
        return

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _render_env_actions_and_summary(group_name: str) -> None:
    latest_report = _get_latest_ai_report(group_name)
    if not latest_report:
        st.info("Ainda não há relatório IA salvo para exibir ações e resumo.")
        return

    actions_block = _extract_report_block(latest_report, "## 4. AÇÕES RECOMENDADAS", ["**Resumo Executivo**"])
    summary_block = _extract_report_block(latest_report, "**Resumo Executivo**", [])

    if actions_block:
        actions_block = actions_block.replace("## 4. AÇÕES RECOMENDADAS", "", 1).strip()
        st.markdown("### Ações Recomendadas")
        st.markdown(actions_block)
    else:
        st.info("Seção de ações não encontrada no último relatório IA.")

    if summary_block:
        summary_block = summary_block.replace("**Resumo Executivo**", "", 1).lstrip(":").strip()
        st.markdown("### Resumo Executivo")
        st.markdown(summary_block)


def _render_group_ai_report(group_name: str) -> None:
    latest_report = _get_latest_ai_report(group_name)
    if not latest_report:
        st.info("Ainda não há relatório IA salvo para exibir a camada narrativa do grupo.")
        return

    section_2_1 = _extract_report_block(
        latest_report,
        "### 2.1 Interpretação dos Desvios",
        ["### 2.2 Evidências Objetivas do Período", "## 3. AÇÕES RECOMENDADAS", "**Resumo Executivo**"],
    )
    actions_block = _extract_report_block(latest_report, "## 3. AÇÕES RECOMENDADAS", ["**Resumo Executivo**"])
    summary_block = _extract_report_block(latest_report, "**Resumo Executivo**", [])

    if section_2_1:
        st.markdown("### Interpretação dos Desvios")
        cleaned = section_2_1.replace("### 2.1 Interpretação dos Desvios", "", 1).strip()
        st.markdown(cleaned)
    else:
        st.info("Seção de interpretação dos desvios não encontrada no último relatório IA.")

    if actions_block:
        cleaned = actions_block.replace("## 3. AÇÕES RECOMENDADAS", "", 1).strip()
        st.markdown("### Ações Recomendadas")
        st.markdown(cleaned)
    else:
        st.info("Seção de ações não encontrada no último relatório IA.")

    if summary_block:
        cleaned = summary_block.replace("**Resumo Executivo**", "", 1).lstrip(":").strip()
        st.markdown("### Resumo Executivo")
        st.markdown(cleaned)


def _render_group_overview_panels(metrics: Dict[str, Any]) -> None:
    baseline_analysis = metrics.get("baseline_analysis", {})
    anomaly_detection = baseline_analysis.get("anomaly_detection", {})
    environment_health = baseline_analysis.get("environment_health", {})

    row1 = st.columns(4)
    row1[0].metric("Eventos", metrics.get("total_events", 0))
    row1[1].metric("Críticos", metrics.get("critical_events", 0))
    row1[2].metric("Período", metrics.get("analysis_period", "N/A"))
    row1[3].metric("Horas", metrics.get("hours_analyzed", 0))

    row2 = st.columns(4)
    row2[0].metric("Anomaly Score", round(float(anomaly_detection.get("anomaly_score", 0) or 0), 3))
    row2[1].metric("Volume Z-score", round(float(anomaly_detection.get("volume_deviation", 0) or 0), 3))
    row2[2].metric("Taxa Atual", environment_health.get("current_event_rate", "N/A"))
    row2[3].metric("Taxa Normal", environment_health.get("normal_event_rate", "N/A"))


def _render_group_proxy_table(metrics: Dict[str, Any]) -> None:
    proxy_raw = metrics.get("proxy_analysis", {}).get("raw_data", []) or []
    proxy_summary = metrics.get("proxy_analysis", {}).get("summary", {}) or {}
    rows = []
    for item in proxy_raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        proxy_name = item[0]
        total_events = item[1]
        maybe_failures = item[2] if len(item) > 2 else None
        summary = proxy_summary.get(proxy_name, {})
        rows.append(
            {
                "proxy": proxy_name,
                "total_events": total_events,
                "avg_per_hour": summary.get("avg_per_hour"),
                "avg_per_hour_7_days": summary.get("avg_per_hour_7_days"),
                "last": summary.get("last"),
                "proxy_failures": maybe_failures,
            }
        )

    if not rows:
        st.info("Sem dados de proxy no contexto atual.")
        return

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _render_group_critical_events_table(metrics: Dict[str, Any]) -> None:
    rows = []
    for item in metrics.get("critical_events_details", []) or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "host": item.get("host_name"),
                "problema": item.get("problem_name"),
                "proxy": item.get("proxy_name"),
                "timestamp": item.get("timestamp"),
            }
        )

    if not rows:
        st.info("Sem eventos críticos detalhados no período.")
        return

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _build_hub_client(
    base_url: str,
    token: str,
) -> NotificationHubClient:
    return NotificationHubClient(
        base_url=base_url.strip(),
        bearer_token=token.strip(),
        timeout=30,
    )


def _render_hub_bar_chart(items: List[Dict[str, Any]], title: str, x_label: str, y_label: str) -> None:
    if not items:
        st.info(f"Sem dados para {title.lower()}.")
        return

    df = pd.DataFrame(items)
    if df.empty or "name" not in df.columns or "value" not in df.columns:
        st.info(f"Sem dados para {title.lower()}.")
        return

    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    df = df[df["value"] > 0].sort_values("value", ascending=False)
    if df.empty:
        st.info(f"Sem dados para {title.lower()}.")
        return

    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6)
        .encode(
            x=alt.X("value:Q", title=x_label),
            y=alt.Y("name:N", sort="-x", title=y_label),
            tooltip=["name", "value"],
        )
        .properties(title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_hub_view(payload: Dict[str, Any]) -> None:
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        st.warning("Resposta do HUB sem bloco 'data'.")
        return

    cards_row = st.columns(4)
    cards_row[0].metric("Eventos Criados", data.get("total_created_events", 0))
    cards_row[1].metric("Eventos Atualizados", data.get("total_updated_events", 0))
    cards_row[2].metric("Eventos Pendentes", data.get("total_pending_events", 0))
    cards_row[3].metric("Eventos Ignorados", data.get("total_ignored_events", 0))

    cards_row2 = st.columns(4)
    cards_row2[0].metric("Incidentes Criados", data.get("total_created_incidents", 0))
    cards_row2[1].metric("Incidentes Atualizados", data.get("total_updated_incidents", 0))
    cards_row2[2].metric("Incidentes Pendentes", data.get("total_pending_incidents", 0))
    cards_row2[3].metric("Tickets Fechados", data.get("total_closed_tickets_by_hub", 0))

    col_app, col_host = st.columns(2)
    with col_app:
        _render_hub_bar_chart(data.get("top_application_events", []), "Top Aplicações (Eventos)", "Eventos", "Aplicação")
        st.dataframe(data.get("top_application_events", []), use_container_width=True)
    with col_host:
        _render_hub_bar_chart(data.get("top_host_events", []), "Top Hosts (Eventos)", "Eventos", "Host")
        st.dataframe(data.get("top_host_events", []), use_container_width=True)

    col_proxy, col_team = st.columns(2)
    with col_proxy:
        _render_hub_bar_chart(data.get("top_proxy_events", []), "Top Proxies (Eventos)", "Eventos", "Proxy")
        st.dataframe(data.get("top_proxy_events", []), use_container_width=True)
    with col_team:
        _render_hub_bar_chart(data.get("top_team_events", []), "Top Times (Eventos)", "Eventos", "Time")
        st.dataframe(data.get("top_team_events", []), use_container_width=True)

    with st.expander("Ver JSON completo do HUB"):
        st.json(_to_serializable(payload))


st.title("📊 Zabbix Audit Dashboard")
st.caption("MVP de interface para coleta, análise por grupo, auditoria de ambiente e histórico de IA")


def _show_db_connection_help(error: Exception) -> None:
    st.error(f"Falha ao conectar no PostgreSQL: {error}")
    st.markdown("### Diagnóstico rápido")
    st.write(f"- Host: `{DB_HOST}`")
    st.write(f"- Porta: `{DB_PORT}`")
    st.write(f"- Banco: `{DB_NAME}`")
    st.write(f"- Usuário: `{DB_USER}`")
    st.markdown("### Como corrigir")
    st.write("1. Verifique se o PostgreSQL está em execução.")
    st.write("2. Confirme as variáveis no arquivo `.env` (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).")
    st.write("3. Teste conectividade com `pg_isready -h <host> -p <porta>`.")
    st.write("4. Reinicie o app após ajustar (`streamlit run app.py`).")


analyzer = None
startup_error = None
try:
    analyzer = get_analyzer()
except Exception as error:
    startup_error = error

if startup_error:
    _show_db_connection_help(startup_error)
    col_retry, _ = st.columns([1, 3])
    if col_retry.button("Tentar reconectar", type="primary"):
        get_analyzer.clear()
        st.rerun()
    st.stop()


tab_collect, tab_routine, tab_metrics, tab_assistant, tab_audit, tab_hub, tab_status, tab_history = st.tabs(
    ["Coleta", "Auditoria do Ambiente", "Métricas por Grupo", "Assistente Investigativo", "Métricas do Ambiente", "HUB", "Stats & Debug", "Histórico IA"]
)

with tab_collect:
    st.subheader("Coleta Global de Eventos")
    hours_collect = st.number_input("Horas para coleta", min_value=1, max_value=720, value=24, step=1)

    if st.button("Iniciar Coleta Global", type="primary"):
        with st.spinner("Coletando eventos do Zabbix..."):
            try:
                stored = analyzer.collect_all_events(int(hours_collect))
                st.success(f"Coleta finalizada: {stored} eventos armazenados.")
            except Exception as error:
                _show_error("Falha na coleta global", error)

with tab_routine:
    st.subheader(f"Rotina {ENV_AUDIT_GROUP}")
    st.caption(f'Executa equivalente a: python cli.py --group-name "{ENV_AUDIT_GROUP}" --hours <N> --with-ai-toon --env-audit')

    routine_hours = st.number_input(
        "Horas da rotina",
        min_value=1,
        max_value=24,
        value=1,
        step=1,
        key="routine_hours",
    )

    latest_project_report = _get_latest_ai_report(ENV_AUDIT_GROUP)
    st.markdown(f"### Último relatório IA - {ENV_AUDIT_GROUP}")
    if latest_project_report:
        st.text_area("Relatório mais recente", value=latest_project_report, height=220, disabled=True)
    else:
        st.info(f"Ainda não há relatório de IA salvo para {ENV_AUDIT_GROUP}.")

    run_routine = st.button(f"Executar rotina {ENV_AUDIT_GROUP}", type="primary")
    refresh_dashboard = st.button("Atualizar visualizações do banco")

    if run_routine:
        with st.spinner(f"Executando rotina {ENV_AUDIT_GROUP}..."):
            try:
                stored = analyzer.collect_all_events(int(routine_hours))
                metrics = analyzer.get_structured_metrics(
                    group_name=ENV_AUDIT_GROUP,
                    hours_back=int(routine_hours),
                    include_baseline=True,
                )
                env_audit = analyzer.get_environment_audit(active_group_name=ENV_AUDIT_GROUP)

                analyzer.initialize_ai_analyzer(OLLAMA_MODEL, OLLAMA_API_URL)
                ai_response = analyzer.run_ai_analysis(
                    metrics,
                    ENV_AUDIT_GROUP,
                    use_toon=True,
                    prompt_variant="env-audit",
                    metrics_env=env_audit,
                )

                st.session_state["routine_ai_response"] = ai_response or ""
                st.session_state["routine_last_comparison"] = analyzer.get_last_execution_comparison()
                st.session_state["routine_dashboard_data"] = _fetch_dashboard_data(ENV_AUDIT_GROUP)

                st.success(f"Rotina executada com sucesso. Eventos coletados: {stored}")

            except Exception as error:
                _show_error("Falha ao executar rotina", error)

    if refresh_dashboard:
        try:
            st.session_state["routine_dashboard_data"] = _fetch_dashboard_data(ENV_AUDIT_GROUP)
            st.success("Visualizações atualizadas com sucesso.")
        except Exception as error:
            _show_error("Falha ao atualizar visualizações", error)

    ai_text = st.session_state.get("routine_ai_response", "")
    _render_ai_analysis_block(
        ai_text,
        st.session_state.get("routine_last_comparison"),
        text_area_key="routine_ai_text",
    )

    dashboard_data = st.session_state.get("routine_dashboard_data")
    _render_dashboard_sections(
        dashboard_data,
        key_prefix="routine",
        empty_message="Execute a rotina ou clique em 'Atualizar visualizações do banco' para carregar os gráficos e tabelas.",
        include_raw_expander=True,
    )

with tab_metrics:
    st.subheader("Métricas por Grupo")
    st.caption('Executa equivalente a: python cli.py --group-name "<grupo>" --hours <N> --with-ai-toon')

    col_a, col_b = st.columns([2, 1])
    group_name = col_a.text_input("Nome do grupo", value="Zabbix/Servico/Proxy")
    hours_metrics = col_b.number_input("Horas", min_value=1, max_value=720, value=24, step=1)

    run_group_metrics = st.button("Executar métricas do grupo", type="primary")
    refresh_group_dashboard = st.button("Atualizar visualizações do grupo")

    if run_group_metrics:
        if not group_name.strip():
            st.warning("Informe um group name válido para executar a rotina.")
        else:
            with st.spinner("Executando rotina de métricas por grupo..."):
                try:
                    stored = analyzer.collect_all_events(int(hours_metrics))

                    metrics = analyzer.get_structured_metrics(
                        group_name=group_name.strip(),
                        hours_back=int(hours_metrics),
                        include_baseline=True,
                    )

                    st.session_state["metrics_group"] = group_name.strip()
                    st.session_state["metrics_result"] = metrics
                    st.session_state["metrics_dashboard_data"] = _fetch_dashboard_data(group_name.strip())

                    try:
                        analyzer.initialize_ai_analyzer(OLLAMA_MODEL, OLLAMA_API_URL)
                        ai_response = analyzer.run_ai_analysis(
                            metrics,
                            group_name.strip(),
                            use_toon=True,
                        )
                        st.session_state["metrics_ai_response"] = ai_response or ""
                        st.session_state["metrics_last_comparison"] = analyzer.get_last_execution_comparison()
                    except Exception as ai_error:
                        st.session_state["metrics_ai_response"] = ""
                        st.session_state["metrics_last_comparison"] = None
                        st.warning(f"As métricas estruturadas foram carregadas, mas a análise da IA falhou: {ai_error}")

                    st.success(f"Rotina do grupo executada com sucesso. Eventos coletados: {stored}")
                except Exception as error:
                    _show_error("Falha ao executar métricas do grupo", error)

    if refresh_group_dashboard:
        selected_group = (st.session_state.get("metrics_group") or group_name).strip()
        if not selected_group:
            st.warning("Informe um group name para atualizar as visualizações.")
        else:
            try:
                st.session_state["metrics_group"] = selected_group
                st.session_state["metrics_dashboard_data"] = _fetch_dashboard_data(selected_group)
                st.success("Visualizações do grupo atualizadas com sucesso.")
            except Exception as error:
                _show_error("Falha ao atualizar visualizações do grupo", error)

    metrics_result = st.session_state.get("metrics_result")
    if metrics_result:
        st.markdown("### Estado Geral do Grupo")
        _render_group_overview_panels(metrics_result)

        st.markdown("### Top Problemas")
        st.dataframe(metrics_result.get("top_problems", []), use_container_width=True)

        st.markdown("### Top Hosts")
        st.dataframe(metrics_result.get("top_hosts", []), use_container_width=True)

        selected_metrics_group = (st.session_state.get("metrics_group") or group_name).strip()
        if selected_metrics_group:
            st.markdown("### Relatório do Grupo")
            _render_group_ai_report(selected_metrics_group)
            _render_execution_comparison(st.session_state.get("metrics_last_comparison"))
        else:
            st.info("Informe um grupo para exibir o relatório narrativo.")

        detail_col1, detail_col2 = st.columns(2)
        with detail_col1:
            st.markdown("### Proxies do Grupo")
            _render_group_proxy_table(metrics_result)
        with detail_col2:
            st.markdown("### Eventos Críticos")
            _render_group_critical_events_table(metrics_result)

        with st.expander("Ver JSON completo das métricas"):
            st.json(_to_serializable(metrics_result))

    metrics_dashboard_data = st.session_state.get("metrics_dashboard_data")
    _render_dashboard_sections(
        metrics_dashboard_data,
        key_prefix="metrics",
        empty_message="Execute a rotina do grupo ou clique em 'Atualizar visualizações do grupo' para carregar os gráficos e tabelas.",
        include_raw_expander=False,
    )

with tab_assistant:
    st.subheader("Assistente Investigativo")
    st.caption("Chat investigativo sobre um grupo e janela de tempo usando métricas estruturadas, baseline, histórico recente e contexto de runbook.")

    assistant_col1, assistant_col2 = st.columns([2, 1])
    assistant_group = assistant_col1.text_input("Grupo para investigação", value="Zabbix/Servico/Proxy", key="assistant_group")
    assistant_hours = assistant_col2.number_input("Janela (horas)", min_value=1, max_value=720, value=24, step=1, key="assistant_hours")

    if st.button("Carregar contexto investigativo", type="primary"):
        selected_group = assistant_group.strip()
        if not selected_group:
            st.warning("Informe um grupo válido para carregar o contexto.")
        else:
            with st.spinner("Montando contexto investigativo..."):
                try:
                    context_bundle = analyzer.build_investigation_context(selected_group, int(assistant_hours))
                    st.session_state["investigation_context"] = context_bundle
                    st.session_state["investigation_scope"] = f"{selected_group}|{int(assistant_hours)}"
                    st.session_state["investigation_messages"] = []
                    st.success("Contexto investigativo carregado.")
                except Exception as error:
                    _show_error("Falha ao montar contexto investigativo", error)

    investigation_context = st.session_state.get("investigation_context")
    investigation_scope = st.session_state.get("investigation_scope")
    current_scope = f"{assistant_group.strip()}|{int(assistant_hours)}" if assistant_group.strip() else None

    if investigation_context and investigation_scope == current_scope:
        latest_history = investigation_context.get("latest_ai_history") or {}
        current_metrics = investigation_context.get("current_metrics") or {}
        baseline_summary = investigation_context.get("baseline_summary") or {}

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Eventos", current_metrics.get("total_events", 0))
        col2.metric("Críticos", current_metrics.get("critical_events", 0))
        col3.metric("Z-score", round(float(baseline_summary.get("volume_z_score", 0) or 0), 3))
        col4.metric("Anomaly Score", round(float(baseline_summary.get("anomaly_score", 0) or 0), 3))

        if latest_history:
            st.markdown("**Última análise registrada**")
            st.write(f"- Classificação: {latest_history.get('classification') or 'N/A'}")
            st.write(f"- Problema principal: {latest_history.get('main_problem') or 'N/A'}")
            st.write(f"- Resumo: {latest_history.get('summary') or 'N/A'}")

        if investigation_context.get("last_execution_comparison"):
            _render_execution_comparison(
                investigation_context.get("last_execution_comparison"),
                title="Última comparação salva",
            )

        quick_question = None
        st.markdown("**Perguntas guiadas**")
        qcol1, qcol2 = st.columns(2)
        if qcol1.button("O que piorou desde a última execução?", key="investigate_q1"):
            quick_question = "O que piorou desde a última execução?"
        if qcol2.button("Quais proxies concentram mais eventos?", key="investigate_q2"):
            quick_question = "Quais proxies concentram mais eventos e por quê?"

        qcol3, qcol4 = st.columns(2)
        if qcol3.button("Há anomalia real ou ruído?", key="investigate_q3"):
            quick_question = "Há indício de anomalia real ou isso parece ruído operacional?"
        if qcol4.button("Quais ações imediatas são recomendadas?", key="investigate_q4"):
            quick_question = "Quais ações imediatas são recomendadas com base no contexto atual?"

        with st.form("investigation_question_form"):
            free_question = st.text_area(
                "Pergunta livre",
                value="",
                placeholder="Ex.: quais hosts parecem concentrar os problemas críticos?",
                height=100,
            )
            ask_free_question = st.form_submit_button("Perguntar")

        pending_question = quick_question or (free_question.strip() if ask_free_question else "")
        if pending_question:
            messages = st.session_state.setdefault("investigation_messages", [])
            messages.append({"role": "user", "content": pending_question})
            with st.spinner("Investigando com IA..."):
                try:
                    ai_answer = analyzer.run_investigation_chat(
                        assistant_group.strip(),
                        pending_question,
                        investigation_context,
                        chat_history=messages[:-1],
                    )
                except Exception as error:
                    ai_answer = f"Erro ao executar assistente investigativo: {error}"
            messages.append({"role": "assistant", "content": ai_answer})
            st.session_state["investigation_messages"] = messages

        messages = st.session_state.get("investigation_messages", [])
        if messages:
            st.markdown("### Conversa")
            _render_investigation_messages(messages)

        with st.expander("Ver contexto investigativo"):
            st.json(_to_serializable(investigation_context))
    else:
        st.info("Carregue o contexto investigativo para iniciar a conversa.")

with tab_audit:
    st.subheader("Métricas do Ambiente")

    if st.button("Extrair métricas", type="primary"):
        with st.spinner("Extraindo métricas do ambiente..."):
            try:
                audit = analyzer.get_environment_audit()
                if "error" in audit:
                    st.error(audit["error"])
                else:
                    st.session_state["env_audit_result"] = audit
                    st.session_state["env_dashboard_data"] = _fetch_dashboard_data(group_name=None)
                    st.success("Métricas e visualizações do ambiente atualizadas com sucesso.")
            except Exception as error:
                _show_error("Falha na auditoria", error)

    env_audit = st.session_state.get("env_audit_result")
    if env_audit:
        st.markdown("### Estado Geral do Ambiente")
        _render_env_overview_panels(env_audit)

        st.markdown("### Proxies Ativos na Última Hora")
        _render_env_last_hour_chart(env_audit.get("proxies_last_hour", []))

        detail_col1, detail_col2 = st.columns(2)
        with detail_col1:
            st.markdown("### Top Anomalias de Proxy")
            st.dataframe(env_audit.get("proxies_top_anomalies", []), use_container_width=True)
        with detail_col2:
            st.markdown("### Eventos Críticos, Recentes e Persistentes")
            _render_env_events_table(env_audit)

        st.markdown("### Relatório do Ambiente")
        _render_env_actions_and_summary(ENV_AUDIT_GROUP)

        with st.expander("Ver JSON completo"):
            st.json(_to_serializable(env_audit))

    env_dashboard_data = st.session_state.get("env_dashboard_data")
    if env_dashboard_data:
        st.markdown("### Top Problemas")
        st.dataframe(env_dashboard_data.get("top_problems_24h", []), use_container_width=True)

        st.markdown("### Top Problemas 24h")
        _render_pie_chart(
            env_dashboard_data.get("top_problems_24h", []),
            label_field="problem_name",
            value_field="total",
            title="Top problemas (24h)",
        )

        st.markdown("### Variação de eventos por hora")
        _render_line_chart(
            env_dashboard_data.get("events_by_hour", []),
            x_field="time",
            y_field="total_eventos",
            title="Variação de eventos por hora (24h)",
        )

        st.markdown("### Variação de eventos por hora por proxy")
        _render_proxy_24h_chart_with_filter(
            env_dashboard_data.get("events_by_proxy_hour", []),
            title="Variação de eventos por hora por proxy (24h)",
            key_prefix="env_proxy_hour_full",
            default_show_all=True,
        )

        st.markdown("### Distribuição de eventos por proxy")
        st.dataframe(env_dashboard_data.get("proxy_distribution_24h", []), use_container_width=True)

with tab_hub:
    st.subheader("HUB - Notification Hub")
    st.caption("Consulta estatísticas do HUB por time e período com visualização gráfica.")

    current_date = datetime.now().date()

    filter_col1, filter_col2, filter_col3 = st.columns([2, 1, 1])
    hub_team = filter_col1.text_input("Time/Grupo (opcional)", value="", key="hub_team", placeholder="Deixe em branco para todos os times")
    hub_start_date = filter_col2.date_input("Data inicial", value=current_date - timedelta(days=30), key="hub_start_date")
    hub_end_date = filter_col3.date_input("Data final", value=current_date, key="hub_end_date")

    hub_url = st.text_input("Endpoint statistics", value=HUB_DEFAULT_URL, key="hub_url")

    if st.button("Consultar HUB", type="primary"):
        if hub_start_date > hub_end_date:
            st.warning("Data inicial não pode ser maior que a data final.")
        elif not HUB_DEFAULT_TOKEN.strip():
            st.warning("NOTIFICATION_HUB_TOKEN não configurado no .env.")
        else:
            with st.spinner("Consultando dados do HUB..."):
                try:
                    client = _build_hub_client(
                        base_url=hub_url,
                        token=HUB_DEFAULT_TOKEN,
                    )
                    hub_payload = client.fetch_statistics(
                        start_date=hub_start_date.strftime("%Y-%m-%d"),
                        end_date=hub_end_date.strftime("%Y-%m-%d"),
                        team=hub_team.strip() if hub_team else "",
                    )
                    st.session_state["hub_payload"] = hub_payload
                    st.session_state["hub_last_query"] = {
                        "team": hub_team.strip(),
                        "start_date": hub_start_date.strftime("%Y-%m-%d"),
                        "end_date": hub_end_date.strftime("%Y-%m-%d"),
                    }
                    st.success("Consulta HUB concluída com sucesso.")
                except Exception as error:
                    _show_error("Falha ao consultar HUB", error)

    hub_last_query = st.session_state.get("hub_last_query")
    if hub_last_query:
        st.caption(
            f"Última consulta: team={hub_last_query.get('team')} | "
            f"período={hub_last_query.get('start_date')} até {hub_last_query.get('end_date')}"
        )

    if st.session_state.get("hub_payload"):
        _render_hub_view(st.session_state["hub_payload"])
    else:
        st.info("Preencha os filtros e clique em 'Consultar HUB' para visualizar os gráficos.")

with tab_status:
    st.subheader("Stats & Debug")
    st.caption('Executa equivalente a: python cli.py --group-name "Zabbix" --show-stats')

    if st.button("Buscar Stats & Debug", type="primary"):
        with st.spinner("Consultando status do banco..."):
            try:
                stats = analyzer.get_database_stats()
                if "error" in stats:
                    st.error(stats["error"])
                else:
                    st.session_state["db_stats"] = stats
                    st.success("Status do banco atualizado com sucesso.")
            except Exception as error:
                _show_error("Falha ao obter status do banco", error)

    db_stats = st.session_state.get("db_stats")
    if db_stats:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total de Eventos", f"{int(db_stats.get('total_events', 0)):,}")
        col2.metric("Hosts Únicos", f"{int(db_stats.get('hosts_count', 0)):,}")
        col3.metric("Proxies Únicos", f"{int(db_stats.get('proxies_count', 0)):,}")
        col4.metric("Período dos Dados", db_stats.get("date_range", "N/A"))

        st.markdown("### Informações Detalhadas")
        st.json(_to_serializable(db_stats))
    else:
        st.info("Clique em 'Buscar Stats & Debug' para consultar as estatísticas.")

    st.markdown("### Prompt Inject por Componente")
    st.caption("Executa apenas a análise da IA com prompts reduzidos ou completos, separados por fluxo de ambiente e grupo.")

    selected_inject_component = st.selectbox(
        "Componente para teste",
        options=list(PROMPT_INJECT_COMPONENTS.keys()),
        format_func=lambda key: PROMPT_INJECT_COMPONENTS[key]["label"],
        key="prompt_inject_component",
    )
    inject_component_config = PROMPT_INJECT_COMPONENTS[selected_inject_component]

    prompt_group_name = st.text_input(
        "Grupo alvo do Prompt Inject",
        value=st.session_state.get("prompt_inject_group_name", inject_component_config["group_name"]),
        key="prompt_inject_group_name",
    )

    prompt_inject_text = st.text_area(
        "Prompt Inject",
        value=st.session_state.get("prompt_inject_text", ""),
        height=260,
        placeholder=inject_component_config["placeholder"],
        key="prompt_inject_text",
    )

    if st.button("Executar Prompt Inject", type="primary"):
        if not prompt_inject_text.strip():
            st.warning("Informe um prompt para executar o teste.")
        else:
            with st.spinner("Executando Prompt Inject na IA..."):
                try:
                    analyzer.initialize_ai_analyzer(OLLAMA_MODEL, OLLAMA_API_URL)
                    if inject_component_config["runner"] == "env":
                        injected_response = analyzer.run_env_audit_prompt_inject(
                            prompt_inject_text.strip(),
                            group_name=(prompt_group_name.strip() or ENV_AUDIT_GROUP),
                        )
                    else:
                        injected_response = analyzer.run_group_prompt_inject(
                            prompt_inject_text.strip(),
                            group_name=(prompt_group_name.strip() or inject_component_config["group_name"]),
                        )
                    st.session_state["prompt_inject_response"] = injected_response or ""
                    st.success("Prompt Inject executado com sucesso.")
                except Exception as error:
                    _show_error("Falha no Prompt Inject", error)

    prompt_inject_response = st.session_state.get("prompt_inject_response", "")
    if prompt_inject_response:
        st.text_area(
            "Resposta do Prompt Inject",
            value=prompt_inject_response,
            height=320,
            disabled=True,
            key="prompt_inject_response_view",
        )

    st.markdown("### Logs e Artefatos de Debug")
    st.caption(f"Origem: `{DEBUG_LOGS_DIR}`")

    selected_debug_file = st.selectbox(
        "Arquivo de debug",
        options=DEBUG_LOG_FILES,
        index=0,
        key="debug_selected_file",
    )

    selected_debug_path = DEBUG_LOGS_DIR / selected_debug_file
    debug_file_content = _read_debug_file(selected_debug_path)

    file_col1, file_col2 = st.columns([1, 3])
    file_col1.metric("Existe", "Sim" if selected_debug_path.exists() else "Não")
    file_col2.write(f"**Arquivo selecionado:** `{selected_debug_path}`")

    if selected_debug_file.endswith(".json"):
        try:
            parsed_json = json.loads(debug_file_content)
            st.json(_to_serializable(parsed_json))
        except Exception:
            st.text_area(
                "Conteúdo do arquivo",
                value=debug_file_content,
                height=320,
                disabled=True,
                key=f"debug_file_content_{selected_debug_file}",
            )
    else:
        st.text_area(
            "Conteúdo do arquivo",
            value=debug_file_content,
            height=320,
            disabled=True,
            key=f"debug_file_content_{selected_debug_file}",
        )

with tab_history:
    st.subheader("Histórico de Respostas da IA")

    col_h1, col_h2 = st.columns([2, 1])
    history_group = col_h1.text_input("Filtrar por grupo (opcional)", value="")
    history_limit = col_h2.number_input("Limite", min_value=1, max_value=200, value=20, step=1)

    if st.button("Buscar Histórico", type="primary"):
        try:
            entries: List[Dict[str, Any]] = analyzer.get_ollama_history(
                history_group.strip() if history_group.strip() else None,
                int(history_limit),
            )
            st.session_state["history_entries"] = entries
        except Exception as error:
            _show_error("Falha ao buscar histórico", error)

    history_entries = st.session_state.get("history_entries")
    if history_entries is not None:
        if not history_entries:
            st.info("Nenhum histórico encontrado.")
        else:
            classification_options = sorted({str(entry.get("classification")) for entry in history_entries if entry.get("classification")})
            risk_options = sorted({str(entry.get("risk_level")) for entry in history_entries if entry.get("risk_level")})
            main_problem_options = sorted({str(entry.get("main_problem")) for entry in history_entries if entry.get("main_problem")})

            if "history_filter_classification" not in st.session_state:
                st.session_state["history_filter_classification"] = classification_options
            if "history_filter_risk" not in st.session_state:
                st.session_state["history_filter_risk"] = risk_options
            if "history_filter_main_problem" not in st.session_state:
                st.session_state["history_filter_main_problem"] = main_problem_options[: min(10, len(main_problem_options))] if main_problem_options else []

            classification_defaults = _valid_multiselect_defaults(
                classification_options,
                st.session_state.get("history_filter_classification", classification_options),
            )
            risk_defaults = _valid_multiselect_defaults(
                risk_options,
                st.session_state.get("history_filter_risk", risk_options),
            )
            main_problem_defaults = _valid_multiselect_defaults(
                main_problem_options,
                st.session_state.get("history_filter_main_problem", []),
            )

            filter_col1, filter_col2, filter_col3 = st.columns(3)
            selected_classifications = filter_col1.multiselect(
                "Classificação",
                options=classification_options,
                default=classification_defaults,
                key="history_filter_classification",
            )
            selected_risks = filter_col2.multiselect(
                "Nível de risco",
                options=risk_options,
                default=risk_defaults,
                key="history_filter_risk",
            )
            selected_main_problems = filter_col3.multiselect(
                "Problema principal",
                options=main_problem_options,
                default=main_problem_defaults,
                key="history_filter_main_problem",
            )

            filtered_entries = []
            for entry in history_entries:
                classification_match = not selected_classifications or entry.get("classification") in selected_classifications
                risk_match = not selected_risks or entry.get("risk_level") in selected_risks
                main_problem_match = not selected_main_problems or entry.get("main_problem") in selected_main_problems
                if classification_match and risk_match and main_problem_match:
                    filtered_entries.append(entry)

            if not filtered_entries:
                st.info("Nenhum histórico encontrado com os filtros selecionados.")
            else:
                formatted_rows = []
                for entry in filtered_entries:
                    prompt_text = entry.get("ai_prompt", "") or ""
                    formatted_rows.append(
                        {
                            "timestamp": _to_serializable(entry.get("timestamp")),
                            "groupname": entry.get("groupname"),
                            "model": entry.get("model"),
                            "classification": entry.get("classification"),
                            "risk_level": entry.get("risk_level"),
                            "main_problem": entry.get("main_problem"),
                            "summary": entry.get("summary"),
                            "recommended_actions": " | ".join(entry.get("recommended_actions") or []),
                            "prompt_chars": len(prompt_text),
                            "prompt_preview": (prompt_text[:180] + "...") if len(prompt_text) > 180 else prompt_text,
                            "executive_report": entry.get("response"),
                        }
                    )
                st.dataframe(formatted_rows, use_container_width=True)

                with st.expander("Ver respostas completas e prompts"):
                    for index, entry in enumerate(filtered_entries, start=1):
                        st.markdown(
                            f"### {index}. {entry.get('groupname')} - {_to_serializable(entry.get('timestamp'))}"
                        )
                        st.text_area(
                            f"Resposta completa {index}",
                            value=entry.get("response", ""),
                            height=220,
                            disabled=True,
                            key=f"history_response_{index}",
                        )
                        if entry.get("ai_prompt"):
                            st.markdown("**Prompt salvo**")
                            st.text_area(
                                f"Prompt salvo {index}",
                                value=entry.get("ai_prompt", "") or "",
                                height=320,
                                disabled=True,
                                key=f"history_prompt_{index}",
                            )
                        else:
                            st.caption("Prompt não salvo nesta entrada.")

st.divider()
st.caption("Dica: execute com `streamlit run app.py`")
