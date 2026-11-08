"""Версии и автономные исследовательские отчёты."""
import base64
from datetime import datetime, timezone
import html
import json
from pathlib import Path

from pipeline.common import digest, write_json


def fingerprint(prefix: str, inputs: dict, code_paths: list[Path]):
    data = {**inputs, "code": {p.name: digest(p.read_bytes()) for p in sorted(code_paths)}}
    return prefix + digest(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())[:12], data


def saved_time(path: Path):
    return (json.loads(path.read_text())["generated_at"] if path.exists()
            else datetime.now(timezone.utc).isoformat(timespec="seconds"))


def save_model(path: Path, artifact: dict):
    json.dumps(artifact, allow_nan=False)
    write_json(path, artifact)
    sha = digest(path.read_bytes())
    path.with_suffix(".json.sha256").write_text(sha + "\n")
    return sha


def finish_manifest(output: Path, metadata: dict):
    write_json(output / "run_manifest.json", {**metadata,
               "outputs": {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir())
                           if p.is_file() and p.name != "run_manifest.json"},
               "human_methodology_review": "pending"})


def image_tag(path: Path):
    return "<img alt='График прогноза и проверки' src='data:image/png;base64," + base64.b64encode(path.read_bytes()).decode() + "'>"


def table(headers, rows):
    return ("<div class='scroll'><table><thead><tr>" + "".join("<th>" + html.escape(str(x)) + "</th>" for x in headers)
            + "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + html.escape(str(x)) + "</td>" for x in row)
                                              + "</tr>" for row in rows) + "</tbody></table></div>")


def number(value, digits=1):
    return "не определено" if value is None else f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def page(title, contents):
    return ("<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>" + html.escape(title) + "</title><style>"
            "body{font:16px/1.55 system-ui;color:#17343c;background:#f5f7f6;margin:0}"
            "main{max-width:1180px;margin:24px auto;padding:24px;background:white;border:1px solid #dce4e2;border-radius:12px}"
            "h1{line-height:1.2}h2{margin-top:32px}img{width:100%}.scroll{overflow:auto}"
            "table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;border:1px solid #dce4e2;text-align:left}"
            "th{background:#eaf3ef}a{color:#166c8c}.notice{padding:15px;background:#fff1e8;border-left:4px solid #be592f}"
            "code{overflow-wrap:anywhere}li{margin:6px 0}p{overflow-wrap:anywhere}"
            "@media(max-width:700px){main{margin:0;padding:14px}}"
            "</style><main><h1>" + html.escape(title) + "</h1>" + contents + "</main></html>")
