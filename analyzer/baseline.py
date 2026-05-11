import sys
from pathlib import Path
import statistics
from datetime import datetime, timedelta
from typing import Dict

# Adicionar diretório atual ao path para imports relativos
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.db import get_db_connection
from shared.models import BaselineMetrics, AnomalyDetection


class GroupBaseline:
    def calculate_baseline(self, group_name: str, days_back: int = 30) -> BaselineMetrics:
        print(f"📊 Calculando baseline do GRUPO '{group_name}' (últimos {days_back} dias)...")
        cutoff_time = int((datetime.now() - timedelta(days=days_back)).timestamp())

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                SELECT 
                    to_char(to_timestamp(timestamp), 'YYYY-MM-DD HH24') as hour,
                    COUNT(*) as event_count,
                    COUNT(DISTINCT host_name) as unique_hosts,
                    SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END) as critical_events
                FROM events 
                WHERE timestamp >= %s 
                AND hostgroups::jsonb ? %s
                GROUP BY hour
                ORDER BY hour
                """, (cutoff_time, group_name))
                hourly_data = cursor.fetchall()

                if not hourly_data:
                    print(f"⚠️ Sem dados históricos do grupo '{group_name}' para baseline")
                    return BaselineMetrics(0, 0, 0, 0, [], {}, group_name)

                event_counts = [row[1] for row in hourly_data]
                unique_hosts = [row[2] for row in hourly_data]
                critical_events = [row[3] for row in hourly_data]

                events_per_hour_avg = statistics.mean(event_counts)
                events_per_hour_std = statistics.stdev(event_counts) if len(event_counts) > 1 else 0
                unique_hosts_avg = statistics.mean(unique_hosts)

                total_events = sum(event_counts)
                total_critical = sum(critical_events)
                critical_events_ratio_avg = total_critical / total_events if total_events > 0 else 0

                cursor.execute("""
                SELECT problem_name, COUNT(*) as count
                FROM events 
                WHERE timestamp >= %s 
                AND hostgroups::jsonb ? %s
                GROUP BY problem_name
                ORDER BY count DESC
                LIMIT 20
                """, (cutoff_time, group_name))
                problem_data = cursor.fetchall()
                top_problems_baseline = [(prob, count / len(hourly_data)) for prob, count in problem_data]

                cursor.execute("""
                SELECT proxy_name, COUNT(*) as count
                FROM events 
                WHERE timestamp >= %s 
                AND hostgroups::jsonb ? %s
                AND proxy_name IS NOT NULL
                GROUP BY proxy_name
                """, (cutoff_time, group_name))
                proxy_data = cursor.fetchall()
                total_proxy_events = sum(count for _, count in proxy_data)
                proxy_load_distribution = {
                    proxy: (count / total_proxy_events) * 100
                    for proxy, count in proxy_data
                } if total_proxy_events > 0 else {}

        baseline = BaselineMetrics(
            events_per_hour_avg=events_per_hour_avg,
            events_per_hour_std=events_per_hour_std,
            critical_events_ratio_avg=critical_events_ratio_avg,
            unique_hosts_avg=unique_hosts_avg,
            top_problems_baseline=top_problems_baseline,
            proxy_load_distribution=proxy_load_distribution,
            group_name=group_name
        )
        print(f"✅ Baseline do grupo '{group_name}': {events_per_hour_avg:.1f}±{events_per_hour_std:.1f} eventos/hora")
        return baseline

    def detect_anomalies(self, current_metrics: Dict, baseline: BaselineMetrics, group_name: str) -> AnomalyDetection:
        print(f"🔍 Detectando anomalias do grupo '{group_name}'...")
        current_hourly_rate = current_metrics.get('total_events', 0) / max(1, current_metrics.get('hours_analyzed', 1))
        volume_deviation = 0
        is_anomalous_volume = False
        if baseline.events_per_hour_std > 0:
            volume_deviation = (current_hourly_rate - baseline.events_per_hour_avg) / baseline.events_per_hour_std
            is_anomalous_volume = abs(volume_deviation) > 2

        current_critical_ratio = current_metrics.get('critical_events', 0) / max(1, current_metrics.get('total_events', 1))
        critical_ratio_deviation = abs(current_critical_ratio - baseline.critical_events_ratio_avg)
        is_anomalous_critical_ratio = critical_ratio_deviation > 0.1

        current_problems = set(prob['name'] for prob in current_metrics.get('top_problems', []))
        baseline_problems = set(prob for prob, _ in baseline.top_problems_baseline[:10])
        new_problems = list(current_problems - baseline_problems)

        proxy_anomalies = []
        current_proxy_dist = current_metrics.get('proxy_analysis', {}).get('event_distribution', {})
        for proxy, current_pct in current_proxy_dist.items():
            baseline_pct = baseline.proxy_load_distribution.get(proxy, 0)
            if abs(current_pct - baseline_pct) > 15:
                proxy_anomalies.append({
                    'proxy': proxy,
                    'current_load': current_pct,
                    'baseline_load': baseline_pct,
                    'deviation': current_pct - baseline_pct
                })

        anomaly_factors = []
        if is_anomalous_volume:
            anomaly_factors.append(min(abs(volume_deviation) / 3, 1))
        if is_anomalous_critical_ratio:
            anomaly_factors.append(min(critical_ratio_deviation / 0.2, 1))
        if new_problems:
            anomaly_factors.append(min(len(new_problems) / 5, 1))
        if proxy_anomalies:
            anomaly_factors.append(min(len(proxy_anomalies) / 3, 1))

        anomaly_score = statistics.mean(anomaly_factors) if anomaly_factors else 0

        return AnomalyDetection(
            is_anomalous_volume=is_anomalous_volume,
            volume_deviation=volume_deviation,
            is_anomalous_critical_ratio=is_anomalous_critical_ratio,
            critical_ratio_deviation=critical_ratio_deviation,
            new_problems=new_problems,
            proxy_anomalies=proxy_anomalies,
            anomaly_score=anomaly_score,
            group_name=group_name
        )
