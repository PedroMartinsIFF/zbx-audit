from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.db import get_db_connection
from shared.models import EventData


class EventRepository:
    def upsert_event(self, event_data: EventData, event_status: str) -> None:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO events 
                    (event_id, timestamp, host_name, hostgroups, proxy_name, 
                     severity, problem_name, is_control_group, correlation_window, 
                     event_status, event_duration, r_eventid, runbook_context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO UPDATE SET
                        timestamp = EXCLUDED.timestamp,
                        host_name = EXCLUDED.host_name,
                        hostgroups = EXCLUDED.hostgroups,
                        proxy_name = EXCLUDED.proxy_name,
                        severity = EXCLUDED.severity,
                        problem_name = EXCLUDED.problem_name,
                        is_control_group = EXCLUDED.is_control_group,
                        correlation_window = EXCLUDED.correlation_window,
                        event_status = EXCLUDED.event_status,
                        event_duration = EXCLUDED.event_duration,
                        r_eventid = EXCLUDED.r_eventid,
                        runbook_context = COALESCE(EXCLUDED.runbook_context, events.runbook_context);
                    """,
                    (
                        event_data.event_id,
                        int(event_data.timestamp.timestamp()),
                        event_data.host_name,
                        json.dumps(event_data.hostgroups),
                        event_data.proxy_name,
                        event_data.severity,
                        event_data.problem_name,
                        event_data.is_control_group,
                        event_data.correlation_window,
                        event_status,
                        event_data.event_duration,
                        event_data.r_eventid,
                        getattr(event_data, "runbook_context", None),
                    ),
                )

    def insert_ollama_response(
        self,
        group_name: str,
        response: str,
        model: str,
        ai_prompt: Optional[str] = None,
        structured_data: Optional[Dict[str, Any]] = None,
        metrics_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        structured_data = structured_data or {}
        metrics_snapshot = metrics_snapshot or {}
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ollama_response (
                        groupname,
                        response,
                        ai_prompt,
                        model,
                        classification,
                        risk_level,
                        main_problem,
                        summary,
                        recommended_actions,
                        metrics_snapshot
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        group_name,
                        response,
                        ai_prompt,
                        model,
                        structured_data.get("classification"),
                        structured_data.get("risk_level"),
                        structured_data.get("main_problem"),
                        structured_data.get("summary"),
                        json.dumps(structured_data.get("recommended_actions", []), ensure_ascii=False),
                        json.dumps(metrics_snapshot, ensure_ascii=False),
                    ),
                )

    def fetch_latest_ollama_response(self, group_name: str) -> Optional[Tuple[Any, ...]]:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        timestamp,
                        groupname,
                        response,
                        ai_prompt,
                        model,
                        classification,
                        risk_level,
                        main_problem,
                        summary,
                        recommended_actions,
                        metrics_snapshot
                    FROM ollama_response
                    WHERE groupname = %s
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (group_name,),
                )
                return cursor.fetchone()

    def fetch_group_metrics_bundle(self, group_name: str, hours_back: int) -> Dict[str, Any]:
        since_timestamp = int((datetime.now() - timedelta(hours=hours_back)).timestamp())
        cutoff_24h = int((datetime.now() - timedelta(hours=24)).timestamp())
        cutoff_7d = int((datetime.now() - timedelta(days=7)).timestamp())
        trend_hours = min(hours_back * 7, 168)
        cutoff_trend = int((datetime.now() - timedelta(hours=trend_hours)).timestamp())

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                zabbix_runbook = ""
                try:
                    cursor.execute(
                        """
                        SELECT runbook_context
                        FROM events
                        WHERE timestamp > %s
                          AND hostgroups::jsonb ? %s
                          AND runbook_context IS NOT NULL
                          AND runbook_context <> ''
                        ORDER BY timestamp DESC
                        LIMIT 1
                        """,
                        (since_timestamp, group_name),
                    )
                    runbook_row = cursor.fetchone()
                    zabbix_runbook = runbook_row[0] if runbook_row else ""
                except Exception:
                    zabbix_runbook = ""

                cursor.execute(
                    """
                    SELECT COUNT(*) as total_count
                    FROM events 
                    WHERE timestamp > %s 
                    AND hostgroups::jsonb ? %s
                    """,
                    (since_timestamp, group_name),
                )
                total_events_result = cursor.fetchone()
                total_events = total_events_result[0] if total_events_result else 0

                cursor.execute(
                    """
                    SELECT proxy_name, COUNT(*) as event_count, COUNT(DISTINCT host_name) as affected_hosts
                    FROM events 
                    WHERE timestamp > %s 
                    AND proxy_name IS NOT NULL 
                    AND hostgroups::jsonb ? %s
                    GROUP BY proxy_name
                    ORDER BY event_count DESC
                    """,
                    (since_timestamp, group_name),
                )
                proxy_analysis = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT hostgroups::text, COUNT(*) as event_count
                    FROM events 
                    WHERE timestamp > %s 
                    AND hostgroups::jsonb ? %s
                    GROUP BY hostgroups
                    ORDER BY event_count DESC
                    LIMIT 10
                    """,
                    (since_timestamp, group_name),
                )
                top_hostgroups = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT correlation_window, COUNT(*) as event_count, COUNT(DISTINCT host_name) as affected_hosts
                    FROM events 
                    WHERE timestamp > %s 
                    AND hostgroups::jsonb ? %s
                    GROUP BY correlation_window
                    HAVING COUNT(*) >= 3
                    ORDER BY event_count DESC
                    LIMIT 10
                    """,
                    (since_timestamp, group_name),
                )
                time_correlations = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT proxy_name, correlation_window, COUNT(*) as event_count, 
                           COUNT(DISTINCT host_name) as affected_hosts
                    FROM events 
                    WHERE timestamp > %s 
                    AND proxy_name IS NOT NULL
                    AND hostgroups::jsonb ? %s
                    GROUP BY proxy_name, correlation_window
                    HAVING COUNT(*) >= 3
                    ORDER BY event_count DESC
                    """,
                    (since_timestamp, group_name),
                )
                proxy_failures = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT host_name, problem_name, to_timestamp(timestamp), proxy_name
                    FROM events 
                    WHERE timestamp > %s 
                    AND severity >= 4 
                    AND hostgroups::jsonb ? %s
                    ORDER BY timestamp DESC
                    LIMIT 20
                    """,
                    (since_timestamp, group_name),
                )
                critical_events = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT problem_name, COUNT(*) as count
                    FROM events 
                    WHERE timestamp > %s 
                    AND hostgroups::jsonb ? %s
                    GROUP BY problem_name
                    ORDER BY count DESC
                    LIMIT 15
                    """,
                    (since_timestamp, group_name),
                )
                top_problems_raw = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT
                        problem_name,
                        host_name,
                        proxy_name,
                        MAX(to_timestamp(timestamp)) as last_seen,
                        MAX(severity) as max_severity,
                        COUNT(*) as event_count
                    FROM events
                    WHERE timestamp > %s
                    AND hostgroups::jsonb ? %s
                    GROUP BY problem_name, host_name, proxy_name
                    ORDER BY last_seen DESC, event_count DESC
                    LIMIT 100
                    """,
                    (since_timestamp, group_name),
                )
                problem_host_pairs = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT host_name, COUNT(*) as event_count
                    FROM events 
                    WHERE timestamp > %s 
                    AND hostgroups::jsonb ? %s
                    GROUP BY host_name
                    ORDER BY event_count DESC
                    LIMIT 10
                    """,
                    (since_timestamp, group_name),
                )
                top_hosts = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT
                        to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24') as hour,
                        proxy_name,
                        COUNT(*) as event_count
                    FROM events
                    WHERE timestamp >= %s 
                    AND proxy_name IS NOT NULL
                    AND hostgroups::jsonb ? %s
                    GROUP BY hour, proxy_name
                    ORDER BY hour, proxy_name
                    """,
                    (cutoff_24h, group_name),
                )
                rows_24h = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT
                        to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24') as hour,
                        proxy_name,
                        COUNT(*) as event_count
                    FROM events
                    WHERE timestamp >= %s 
                    AND proxy_name IS NOT NULL
                    AND hostgroups::jsonb ? %s
                    GROUP BY hour, proxy_name
                    ORDER BY hour, proxy_name
                    """,
                    (cutoff_7d, group_name),
                )
                rows_7d = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT 
                        to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24:00:00') as hour,
                        COUNT(*) as event_count,
                        COUNT(DISTINCT host_name) as unique_hosts,
                        COUNT(DISTINCT hostgroups->>0) as unique_hostgroups,
                        SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END) as critical_events,
                        proxy_name,
                        severity
                    FROM events 
                    WHERE timestamp >= %s 
                    AND hostgroups::jsonb ? %s
                    GROUP BY hour, proxy_name, severity
                    ORDER BY hour
                    """,
                    (cutoff_trend, group_name),
                )
                trend_raw = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT 
                        to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24:00:00') as hour,
                        problem_name,
                        COUNT(*) as count
                    FROM events 
                    WHERE timestamp >= %s 
                    AND hostgroups::jsonb ? %s
                    GROUP BY hour, problem_name
                    ORDER BY hour, count DESC
                    """,
                    (cutoff_trend, group_name),
                )
                trend_problems = cursor.fetchall()

        return {
            'zabbix_runbook': zabbix_runbook,
            'total_events': total_events,
            'proxy_analysis': proxy_analysis,
            'top_hostgroups': top_hostgroups,
            'time_correlations': time_correlations,
            'proxy_failures': proxy_failures,
            'critical_events': critical_events,
            'top_problems_raw': top_problems_raw,
            'problem_host_pairs': problem_host_pairs,
            'top_hosts': top_hosts,
            'rows_24h': rows_24h,
            'rows_7d': rows_7d,
            'trend_raw': trend_raw,
            'trend_problems': trend_problems,
        }

    def fetch_hosts_with_groups(self, group_name: str, since_timestamp: int) -> List[Tuple[str, Any]]:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT host_name, hostgroups
                    FROM events 
                    WHERE timestamp > %s 
                    AND hostgroups::jsonb ? %s
                    """,
                    (since_timestamp, group_name),
                )
                return cursor.fetchall()

    def fetch_active_events_for_group(self, group_name: str, limit: int = 20) -> List[Tuple[Any, ...]]:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        e.event_id,
                        e.host_name,
                        e.severity,
                        e.problem_name,
                        e.timestamp,
                        e.proxy_name
                    FROM events e
                    WHERE e.event_status = 'Problem'
                      AND e.hostgroups::jsonb ? %s
                      AND e.r_eventid IS NULL
                    ORDER BY e.timestamp DESC
                    LIMIT %s
                    """,
                    (group_name, limit),
                )
                return cursor.fetchall()

    def fetch_ollama_history(self, group_name: Optional[str], limit: int) -> List[Tuple[Any, ...]]:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if group_name:
                    cursor.execute(
                        """
                        SELECT timestamp, groupname, response, ai_prompt, model, classification, risk_level, main_problem, summary, recommended_actions, metrics_snapshot
                        FROM ollama_response 
                        WHERE groupname = %s 
                        ORDER BY timestamp DESC 
                        LIMIT %s
                        """,
                        (group_name, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT timestamp, groupname, response, ai_prompt, model, classification, risk_level, main_problem, summary, recommended_actions, metrics_snapshot
                        FROM ollama_response 
                        ORDER BY timestamp DESC 
                        LIMIT %s
                        """,
                        (limit,),
                    )
                return cursor.fetchall()

    def get_basic_stats(self) -> Dict[str, Any]:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM events")
                total_events = cursor.fetchone()[0]
                if total_events == 0:
                    return {
                        'total_events': 0,
                        'date_range': 'Nenhum evento',
                        'hosts_count': 0,
                        'proxies_count': 0,
                    }

                cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events")
                min_date, max_date = cursor.fetchone()
                date_range = f"{datetime.fromtimestamp(min_date).strftime('%Y-%m-%d')} até {datetime.fromtimestamp(max_date).strftime('%Y-%m-%d')}"

                cursor.execute("SELECT COUNT(DISTINCT host_name) FROM events")
                hosts_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(DISTINCT proxy_name) FROM events WHERE proxy_name IS NOT NULL")
                proxies_count = cursor.fetchone()[0]

                return {
                    'total_events': total_events,
                    'date_range': date_range,
                    'hosts_count': hosts_count,
                    'proxies_count': proxies_count,
                }

    def clear_events_and_responses(self) -> int:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM events")
                deleted_count = cursor.rowcount
                cursor.execute("DELETE FROM ollama_response")
                conn.commit()
                return deleted_count
