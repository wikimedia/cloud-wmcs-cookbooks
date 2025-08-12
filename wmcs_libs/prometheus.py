import requests


def get_nodes_from_query(query: str, prometheus_url: str) -> list[str]:
    result = requests.get(f"{prometheus_url}/query", params={"query": query}, timeout=60)
    result.raise_for_status()
    result_data = result.json()
    return [
        result_entry.get("metric", {})["instance"]
        for result_entry in result_data.get("data", {}).get("result", [])
        if "instance" in result_entry.get("metric", {})
    ]
