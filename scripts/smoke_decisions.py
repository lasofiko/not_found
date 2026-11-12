"""HTTP-проверка сравнения на синтетическом вводе, без платного LLM и обучения."""

import argparse
import csv
import io
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--expect-llm-disabled", action="store_true")
    args = parser.parse_args()

    def request(path, payload=None, expected=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = Request(args.base_url.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            assert response.code == expected, (path, response.code, expected, content[:200])
            return content

    base = json.loads(request("/config.json"))["apiBase"]
    body = {
        "direction": "inbound",
        "period": "2027",
        "demand_tonnes": 150,
        "capacity_tonnes": 100,
        "demand_source": "Синтетический ввод HTTP-проверки, не наблюдение",
        "capacity_source": "Синтетическое пользовательское допущение",
        "actions": {
            "reserve_ferry": {"delta_tonnes": 20, "source": "Синтетическое допущение А"},
            "additional_ferry": {"delta_tonnes": 70, "source": "Синтетическое допущение Б"},
        },
    }
    snapshot = json.loads(request(base + "/comparisons", body))
    path = base + "/comparisons/" + snapshot["comparison_id"]
    assert snapshot == json.loads(request(path))
    assert [v["scenario_shortfall_tonnes"] for v in snapshot["variants"]] == [50, 30, 0]
    assert snapshot["variants"][2]["scenario_spare_capacity_tonnes"] == 20
    assert snapshot["thresholds"]["required_delta_tonnes"] == 50
    assert snapshot["passports"]["thresholds.required_delta_tonnes"]["value"] == 50
    exported = json.loads(request(path + "/export?format=json"))
    assert exported["variants"] == snapshot["variants"]
    assert exported["comparison_id"] == snapshot["comparison_id"]
    rows = list(csv.DictReader(io.StringIO(request(path + "/export?format=csv").decode("utf-8-sig"))))
    assert [float(r["scenario_shortfall_tonnes"]) for r in rows] == [50, 30, 0]
    assert all(r["comparison_id"] == snapshot["comparison_id"] for r in rows)
    html = request(path + "/export?format=html").decode("utf-8")
    assert "Печать / Сохранить в PDF" in html and snapshot["comparison_id"] in html
    assert "Синтетическое допущение Б" in html
    request(base + "/comparisons", body | {"region": "kamchatka"}, expected=422)
    if args.expect_llm_disabled:
        for endpoint, payload in (("ask", {"question": "Объясни сравнение"}), ("analysis", {})):
            failure = json.loads(request(path + "/" + endpoint, payload, expected=503))
            assert failure["error"]["code"] == "LLM_NOT_CONFIGURED"
        assert json.loads(request(path))["variants"] == snapshot["variants"]
    print("PASS: comparison, thresholds, passports, immutable context, HTML/CSV/JSON, unsupported slice, optional LLM")


if __name__ == "__main__":
    main()
