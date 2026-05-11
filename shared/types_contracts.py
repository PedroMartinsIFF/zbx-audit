from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple, TypedDict, Any


class ProxySummary(TypedDict):
    total_events: int
    avg_per_hour: float
    avg_per_hour_7_days: float
    max: int
    min: int
    last: int


class ProxyAnalysis(TypedDict):
    raw_data: List[Tuple[str, int, int]]
    event_distribution: Dict[str, float]
    summary: Dict[str, ProxySummary]


class BaselineHealth(TypedDict):
    normal_event_rate: str
    current_event_rate: str
    anomaly_score: float
    is_anomalous: bool


class BaselineAnalysis(TypedDict):
    baseline_metrics: Dict[str, Any]
    anomaly_detection: Dict[str, Any]
    trend_analysis: List[Dict[str, Any]]
    environment_health: BaselineHealth


class StructuredMetrics(TypedDict, total=False):
    group_name: str
    zabbix_runbook: str
    top_hostgroups: List[Tuple[str, int]]
    proxy_analysis: ProxyAnalysis
    time_correlations: List[Tuple[str, int, int]]
    critical_events: int
    proxy_failures: List[Tuple[str, str, int, int]]
    hosts_without_notification: List[Dict[str, Any]]
    analysis_period: str
    total_events: int
    hours_analyzed: int
    top_problems: List[Dict[str, Any]]
    top_hosts: List[Tuple[str, int]]
    baseline_analysis: BaselineAnalysis
    ai_summary: Any


class EventsPerHour24h(TypedDict):
    current_avg: float
    current_std: float
    last_hour: int
    z_score_last_hour: float


class Baseline30d(TypedDict):
    events_per_hour_avg: float
    events_per_hour_std: float
    critical_ratio: str
    top_problems: List[Tuple[str, float]]


class ProxyAudit(TypedDict):
    proxy: str
    total_events_24h: int
    avg_per_hour: float
    last_hour_events: int
    z_score: float
    z_score_last_hour: float


class ActiveEvent(TypedDict):
    event_id: str
    host: str
    severity: int
    problem: str


class EnvironmentAudit(TypedDict, total=False):
    period: str
    total_events_24h: int
    avg_events_24h: float
    z_score_24h_vs_30d: float
    events_per_hour_24h: EventsPerHour24h
    baseline_30d: Baseline30d
    proxies_top_anomalies: List[ProxyAudit]
    proxies_last_hour: List[ProxyAudit]
    active_events: List[ActiveEvent]
    message: str
    error: str
