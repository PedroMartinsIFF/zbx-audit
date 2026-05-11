from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests


def _validate_date(date_value: str) -> None:
    datetime.strptime(date_value, "%Y-%m-%d")


@dataclass
class NotificationHubClient:
    base_url: str
    bearer_token: str = ""
    cookie_header: str = ""
    timeout: int = 30

    def fetch_statistics(self, start_date: str, end_date: str, team: str = "") -> dict[str, Any]:
        _validate_date(start_date)
        _validate_date(end_date)

        params = {
            "start_date": start_date,
            "end_date": end_date,
        }
        if team and team.strip():
            params["team"] = team.strip()

        if self.bearer_token.strip():
            headers = {"Authorization": f"Bearer {self.bearer_token.strip()}"}
        else:
            headers = {"Cookie": self.cookie_header}

        response = requests.get(
            self.base_url,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict) or "data" not in payload:
            raise ValueError("Resposta inválida da API Notification Hub: chave 'data' ausente")

        return payload
