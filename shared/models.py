from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Tuple

@dataclass
class EventData:
    event_id: str
    timestamp: datetime
    host_name: str
    hostgroups: List[str]
    proxy_name: Optional[str]
    severity: int
    problem_name: str
    is_control_group: bool
    correlation_window: str
    event_value: int  # 0=recovery, 1=problem
    r_eventid: Optional[str] = None  # ID do evento de problema (quando é recovery)
    event_duration: Optional[int] = None  # Duração em segundos (quando é recovery)
    runbook_context: Optional[str] = None

@dataclass
class BaselineMetrics:
    events_per_hour_avg: float
    events_per_hour_std: float
    critical_events_ratio_avg: float
    unique_hosts_avg: float
    top_problems_baseline: List[Tuple[str, float]]
    proxy_load_distribution: Dict[str, float]
    group_name: str

@dataclass
class AnomalyDetection:
    is_anomalous_volume: bool
    volume_deviation: float
    is_anomalous_critical_ratio: bool
    critical_ratio_deviation: float
    new_problems: List[str]
    proxy_anomalies: List[Dict]
    anomaly_score: float
    group_name: str
