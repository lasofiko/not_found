"""HTTP smoke checks for a running FloCast, without external providers or training."""

import argparse
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--expect-no-model", action="store_true")
    parser.add_argument("--expect-llm-disabled", action="store_true")
    args = parser.parse_args()

    def request(path: str, status: int = 200, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(args.base_url.rstrip("/") + path, data=body, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            assert response.code == status, (path, response.code, status)
            return content, response.headers

    def get_json(path: str, status: int = 200, payload: dict | None = None):
        body, headers = request(path, status, payload)
        assert "application/json" in headers.get("Content-Type", ""), path
        return json.loads(body)

    config = get_json("/config.json")
    prefix = config["apiBase"]
    assert prefix.startswith("/") and not prefix.startswith("//")
    assert get_json(prefix + "/health")["status"] == "ok"
    get_json(prefix + "/status")
    get_json(prefix + "/config")
    assert isinstance(get_json(prefix + "/scenarios"), list)
    catalog = get_json(prefix + "/port-forecasts")
    assert catalog["status"] == "ready" and catalog["primary_target"] == "kaliningrad_ferries"
    targets = {row["target_id"]: row for row in catalog["targets"]}
    primary = targets[catalog["primary_target"]]
    if args.expect_no_model:
        assert not any(row["model_connected"] for row in targets.values())
        assert get_json(prefix + "/port-forecasts/kaliningrad_ferries", 503)["status"] == "not_connected"
    elif primary["model_connected"]:
        port = get_json(prefix + "/port-forecasts/kaliningrad_ferries")
        assert port["model_version"] == primary["expected_model_version"]
        assert port["selected_model"] == "ridge_ar3_10" and port["display"] == "primary"
        assert len(port["predictions"]) == 12 and port["result_type"] == "forecast"
        assert "provenance" in port and all(p["status"] == "forecast" for p in port["predictions"])
        if targets["petropavlovsk_total"]["model_connected"]:
            history = get_json(prefix + "/port-forecasts/petropavlovsk_total")
            assert history["predictions"] == [] and history["result_type"] == "history"
    assert prefix + "/forecasts" in get_json("/openapi.json")["paths"]
    html, headers = request("/")
    assert "text/html" in headers.get("Content-Type", "")
    assert headers.get("Cache-Control") == "no-store"
    assets = re.findall(rb'(?:src|href)="(/assets/[^"?#]+)', html)
    assert assets, "React build assets missing"
    for asset in assets:
        assert request(asset.decode())[0]
    assert request("/a-direct-spa-route")[0] == html
    assert get_json(prefix + "/missing", 404)["error"]["code"] == "NOT_FOUND"
    assert get_json(prefix + "/health", 405, {})["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert get_json(prefix + "/scenarios/capacity", 422, {})["error"]["code"] == "VALIDATION_ERROR"
    result = get_json(
        prefix + "/scenarios/capacity",
        payload={
            "demand_tonnes": 100,
            "capacity_tonnes": 80,
            "capacity_delta_tonnes": 5,
            "period": "2027-01",
            "assumption_source": "Синтетический ввод smoke-проверки",
        },
    )
    assert result["status"] == "scenario" and result["unit"] == "tonnes"
    assert result["scenario_shortfall_tonnes"] == 15
    if args.expect_no_model:
        assert get_json(prefix + "/forecasts", 503)["status"] == "not_connected"
        assert get_json(prefix + "/status")["capabilities"]["forecast"] is False
    if args.expect_llm_disabled:
        assert get_json(prefix + "/llm/summary", 503)["error"]["code"] == "LLM_NOT_CONFIGURED"
        result = get_json(prefix + "/llm/ask", 503, {"question": "Проверка доступности"})
        assert result["error"]["code"] == "LLM_NOT_CONFIGURED"
    print("PASS: React assets, SPA, config, health, ports, API contracts, capacity, optional-service failures")


if __name__ == "__main__":
    main()
