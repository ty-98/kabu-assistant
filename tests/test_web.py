import importlib.util
import json
import threading
import urllib.error
import urllib.request
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import pytest
import yaml

from kabu.prices import FileProvider
from kabu.web import MAX_HOLDINGS, RequestError, check

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures"
BASE = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
RUN_DATE = date(2026, 11, 13)
HOLDINGS = [
    {"ticker": "7203", "name": "サンプル自動車", "market": "JP", "quantity": "10", "avg_cost": "3000", "cost_currency": "JPY"},
    {"ticker": "9984", "market": "JP", "quantity": 2, "avg_cost": 9000},
    {"ticker": "aapl", "market": "US", "quantity": "0.5", "avg_cost": "30000", "cost_currency": "JPY"},
]


def run(payload):
    return check(payload, FileProvider(FIX / "prices.json"), RUN_DATE, BASE)


def test_check_matches_cli_numbers():
    rep = run({"holdings": HOLDINGS})
    assert rep["status"] == "ok"
    assert rep["totals"]["value_jpy"] == pytest.approx(58200)
    assert {p["ticker"] for p in rep["positions"]} == {"7203", "9984", "AAPL"}  # 小文字は大文字に
    assert "評価額 58,200円" in rep["summary_text"]


def test_rules_from_request_override_and_trade_rules_off():
    rep = run({
        "holdings": HOLDINGS,
        "rules": {
            "concentration": {"enabled": True, "max_weight": 0.5},
            "drawdown_from_cost": {"enabled": False},
            "daily_move": {"threshold": 0.1},
        },
    })
    rules = {a["rule"] for a in rep["alerts"]}
    assert rules == set()  # 比率は最大43%<50%、下落は無効、急変は7.4%<10%
    rep = run({"holdings": HOLDINGS, "rules": {"concentration": {"max_weight": 0.3}}})
    assert {a["ticker"] for a in rep["alerts"] if a["rule"] == "concentration"} == {"7203", "AAPL"}
    assert not [a for a in rep["alerts"] if a["rule"] in ("trade_count", "off_hours_trade")]


def test_base_config_is_not_mutated():
    before = json.dumps(BASE, sort_keys=True)
    run({"holdings": HOLDINGS, "rules": {"concentration": {"max_weight": 0.9}}})
    assert json.dumps(BASE, sort_keys=True) == before


@pytest.mark.parametrize(
    "payload, msg",
    [
        ([], "オブジェクト"),
        ({}, "1銘柄以上"),
        ({"holdings": []}, "1銘柄以上"),
        ({"holdings": ["x"]}, "オブジェクト"),
        ({"holdings": [{"ticker": "7203", "market": "JP", "quantity": "-1", "avg_cost": "1"}]}, "quantity"),
        ({"holdings": [{"ticker": "../x", "market": "JP", "quantity": "1", "avg_cost": "1"}]}, "ticker"),
        ({"holdings": [{"ticker": "7203", "market": "JP", "quantity": 1, "avg_cost": 1}] * 2}, "重複"),
        ({"holdings": [{"ticker": f"T{i}", "market": "US", "quantity": 1, "avg_cost": 1} for i in range(MAX_HOLDINGS + 1)]}, "まで"),
        ({"holdings": HOLDINGS, "rules": {"trade_count": {"enabled": True}}}, "未対応"),
        ({"holdings": HOLDINGS, "rules": {"concentration": {"max_weight": 5}}}, "範囲"),
        ({"holdings": HOLDINGS, "rules": {"daily_move": {"threshold": "abc"}}}, "数値"),
    ],
)
def test_bad_requests(payload, msg):
    with pytest.raises(RequestError, match=msg):
        run(payload)


def test_unknown_ticker_reports_failure_not_error():
    rep = run({"holdings": [{"ticker": "ZZZZ", "market": "US", "quantity": 1, "avg_cost": 1}]})
    assert rep["status"] == "failed"
    assert any("ZZZZ" in e for e in rep["errors"])


# --- Vercel関数そのもの (HTTP) ---

@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("KABU_PRICES_FILE", str(FIX / "prices.json"))
    spec = importlib.util.spec_from_file_location("api_check", ROOT / "api" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    httpd = HTTPServer(("127.0.0.1", 0), mod.handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/api/check"
    httpd.shutdown()


def post(url, body: bytes):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_http_ok(server):
    status, body = post(server, json.dumps({"holdings": HOLDINGS}).encode())
    assert status == 200
    # 実行日は今日なので、フィクスチャの終値は古い扱いになり得る。形だけ確認
    assert body["status"] in ("ok", "failed")
    assert "summary_text" in body


def test_http_bad_json(server):
    assert post(server, b"{not json")[0] == 400


def test_http_validation_error(server):
    status, body = post(server, json.dumps({"holdings": []}).encode())
    assert status == 400 and "1銘柄以上" in body["error"]


def test_http_get_not_allowed(server):
    try:
        urllib.request.urlopen(server, timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code == 405
