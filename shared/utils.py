import json
from datetime import datetime
from typing import List


def convert_json_to_toon(json_data, max_depth=3, current_depth=0, parent_key=""):
    result = []
    indent = "  " * current_depth

    if current_depth >= max_depth:
        if isinstance(json_data, (dict, list)) and len(str(json_data)) > 50:
            return [f"{indent}[Dados truncados - profundidade máxima atingida]"]
        else:
            return [f"{indent}{json_data}"]

    if isinstance(json_data, dict):
        for key, value in json_data.items():
            if isinstance(value, dict):
                result.append(f"{indent}{key}:")
                result.extend(convert_json_to_toon(value, max_depth, current_depth + 1, key))
            elif isinstance(value, list):
                result.append(f"{indent}{key}:")
                result.extend(convert_json_to_toon(value, max_depth, current_depth + 1, key))
            else:
                result.append(f"{indent}{key}: {value}")

    elif isinstance(json_data, list):
        for i, item in enumerate(json_data):
            if isinstance(item, (dict, list)):
                result.append(f"{indent}({i + 1})")
                result.extend(convert_json_to_toon(item, max_depth, current_depth + 1, f"{parent_key}[{i}]") )
            else:
                result.append(f"{indent}({i + 1}) {item}")

    else:
        result.append(f"{indent}{json_data}")

    return result


def extract_proxy_name(hostgroups: List[str]):
    for group in hostgroups:
        if group.startswith("Zabbix/Proxy"):
            return group.replace("Zabbix/Proxy", "").strip("/").strip()
    return None


def is_control_group_event(hostgroups: List[str]) -> bool:
    # O filtro de grupos de controle foi removido do fluxo.
    # Mantemos a função para não quebrar os pontos de chamada atuais.
    return False


def get_correlation_window(timestamp: datetime) -> str:
    window_start = timestamp.replace(second=0, microsecond=0)
    minutes = (window_start.minute // 5) * 5
    window_start = window_start.replace(minute=minutes)
    return window_start.strftime('%Y-%m-%d %H:%M')
