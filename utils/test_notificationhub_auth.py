import requests

url = "https://api.notificationhub.globoi.com/statistics"
params = {
    "start_date": "2026-03-14",
    "end_date": "2026-04-13",
    "team": "Operação Vídeos",
}

bearer_token = "Z0FBQUFBQnAzVk92WGl4eDJNQVFCLU9aRkpSeWJJWkloUUR6c1ZfQmNCTWpIMDBfX3lRQnpSdEdhOWVNa3R3SDIxVWdwaC1xejJ4bWFPX216WlFmYlUybFQ2WUE0bEk4bUE9PQ=="

headers = {
    "Authorization": f"Bearer {bearer_token}",
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

resp = requests.get(url, params=params, headers=headers, timeout=30)
print(resp.status_code)
print(resp.text)