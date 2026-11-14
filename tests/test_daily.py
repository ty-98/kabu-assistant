import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from kabu.ledger import LedgerError, load_holdings, load_trades
from kabu.prices import FileProvider, PriceError, parse_yahoo_chart
from kabu.report import build_report, summary_text

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
RUN_DATE = date(2026, 11, 13)


def config(**enabled):
    cfg = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
    for name, on in enabled.items():
        cfg["rules"][name]["enabled"] = on
    return cfg


def report(cfg=None, previous=None, prices=None, run_date=RUN_DATE):
    provider = FileProvider(FIX / "prices.json")
    if prices is not None:
        provider.data = prices
    return build_report(
        load_holdings(FIX / "holdings.csv"),
        load_trades(FIX / "trades.csv"),
        cfg or config(),
        provider,
        run_date,
        previous,
    )


def by_rule(rep, rule):
    return [a for a in rep["alerts"] if a["rule"] == rule]


# --- 評価 ---

def test_valuation_totals_and_weights():
    rep = report()
    assert rep["status"] == "ok"
    # 7203: 10*2500=25000 / 9984: 2*7600=15200 / AAPL: 0.5*240*150=18000
    assert rep["totals"]["value_jpy"] == pytest.approx(58200)
    # 取得: 30000 + 18000 + 15000
    assert rep["totals"]["cost_jpy"] == pytest.approx(63000)
    assert rep["totals"]["pnl_jpy"] == pytest.approx(-4800)
    pos = {p["ticker"]: p for p in rep["positions"]}
    assert pos["7203"]["weight"] == pytest.approx(25000 / 58200)
    assert pos["AAPL"]["pnl_pct"] == pytest.approx(18000 / 15000 - 1)
    assert sum(p["weight"] for p in rep["positions"]) == pytest.approx(1.0)
    assert rep["fx"]["USDJPY"] == 150.0


def test_usd_cost_currency_converted_at_current_rate(tmp_path):
    p = tmp_path / "holdings.csv"
    p.write_text(
        "ticker,name,market,quantity,avg_cost,cost_currency,purchase_date\n"
        "AAPL,Sample,US,1,200,USD,2026-10-01\n",
        encoding="utf-8",
    )
    rep = build_report(
        load_holdings(p), [], config(), FileProvider(FIX / "prices.json"), RUN_DATE
    )
    assert rep["totals"]["cost_jpy"] == pytest.approx(200 * 150)
    assert rep["totals"]["pnl_pct"] == pytest.approx(240 / 200 - 1)


# --- ルール ---

def test_concentration_alert_reports_weight_and_excess():
    alerts = by_rule(report(), "concentration")
    assert {a["ticker"] for a in alerts} == {"7203", "9984", "AAPL"}  # 全て20%超
    a = next(a for a in alerts if a["ticker"] == "7203")
    assert a["value"] == pytest.approx(25000 / 58200)
    assert a["details"]["excess_jpy"] == pytest.approx(25000 - 58200 * 0.20)


def test_drawdown_alert_only_at_or_below_threshold():
    alerts = by_rule(report(), "drawdown_from_cost")
    # 7203: -16.7% (抵触) / 9984: -15.6% (抵触) / AAPL: +20% (対象外)
    assert {a["ticker"] for a in alerts} == {"7203", "9984"}


def test_drawdown_boundary_is_inclusive():
    prices = json.loads((FIX / "prices.json").read_text())
    prices["7203.T"]["close"] = 2550.0  # ちょうど -15%
    prices["7203.T"]["prev_close"] = 2550.0
    assert "7203" in {a["ticker"] for a in by_rule(report(prices=prices), "drawdown_from_cost")}
    prices["7203.T"]["close"] = 2551.0
    assert "7203" not in {a["ticker"] for a in by_rule(report(prices=prices), "drawdown_from_cost")}


def test_daily_move_alert():
    alerts = by_rule(report(), "daily_move")
    assert [a["ticker"] for a in alerts] == ["7203"]  # -7.4%。他は±2%未満
    assert alerts[0]["value"] == pytest.approx(2500 / 2700 - 1)
    assert "2026-11-13" in alerts[0]["id"]


def test_disabled_rules_do_not_fire():
    rep = report()
    assert not by_rule(rep, "trade_count")
    assert not by_rule(rep, "off_hours_trade")
    rep = report(config(concentration=False, drawdown_from_cost=False, daily_move=False))
    assert rep["alerts"] == []


def test_trade_count_counts_only_current_month():
    alerts = by_rule(report(config(trade_count=True)), "trade_count")
    assert len(alerts) == 1
    a = alerts[0]
    assert a["value"] == 5  # 11月5件。10月の1件は数えない
    # 5800*0.005 + 8800*0.010 + 3500*0.005 + 2600*0.005 + 7500*0.005
    assert a["details"]["spread_paid_jpy"] == pytest.approx(29 + 88 + 17.5 + 13 + 37.5)


def test_trade_count_at_limit_is_not_alert():
    cfg = config(trade_count=True)
    cfg["rules"]["trade_count"]["max_per_month"] = 5
    assert not by_rule(report(cfg), "trade_count")


def test_off_hours_trade_extra_cost():
    alerts = by_rule(report(config(off_hours_trade=True)), "off_hours_trade")
    assert len(alerts) == 1
    assert alerts[0]["ticker"] == "9984"
    assert alerts[0]["details"]["extra_cost_jpy"] == pytest.approx(8800 * 0.005)


# --- 新規/継続 ---

def test_is_new_flag_against_previous_report():
    first = report()
    assert all(a["is_new"] for a in first["alerts"])
    second = report(previous=first)
    assert not any(a["is_new"] for a in second["alerts"])  # 同じ日の再実行は全て継続

    # 翌日も同じ値動き率 → 急変だけ新規 (IDに日付を含む)。比率と下落は継続
    prices = json.loads((FIX / "prices.json").read_text())
    for s in ("7203.T", "9984.T", "USDJPY=X"):
        prices[s]["price_date"] = "2026-11-16"
    third = report(previous=first, prices=prices, run_date=date(2026, 11, 16))
    assert [a["rule"] for a in third["alerts"] if a["is_new"]] == ["daily_move"]


# --- 失敗時は判定しない ---

def test_missing_price_fails_without_alerts():
    prices = json.loads((FIX / "prices.json").read_text())
    del prices["9984.T"]
    rep = report(prices=prices)
    assert rep["status"] == "failed"
    assert rep["alerts"] == [] and rep["positions"] == [] and rep["totals"] is None
    assert any("9984.T" in e for e in rep["errors"])
    assert "判定していない" in summary_text(rep)


def test_missing_fx_fails_when_us_stock_held():
    prices = json.loads((FIX / "prices.json").read_text())
    del prices["USDJPY=X"]
    assert report(prices=prices)["status"] == "failed"


def test_stale_price_fails():
    rep = report(run_date=date(2026, 11, 20))  # AAPL の終値が8日前
    assert rep["status"] == "failed"
    assert any("古い" in e for e in rep["errors"])


def test_empty_holdings():
    rep = build_report([], [], config(), FileProvider(FIX / "prices.json"), RUN_DATE)
    assert rep["status"] == "empty"


# --- 台帳の検証 ---

@pytest.mark.parametrize(
    "row, msg",
    [
        ("7203,x,JP,abc,3000,JPY,2026-09-01", "quantity"),
        ("7203,x,JP,0,3000,JPY,2026-09-01", "quantity"),
        ("7203,x,XX,1,3000,JPY,2026-09-01", "market"),
        ("7203,x,JP,1,3000,EUR,2026-09-01", "cost_currency"),
        ("7203,x,JP,1,3000,JPY,2026/09/01", "purchase_date"),
        (",x,JP,1,3000,JPY,2026-09-01", "ticker"),
    ],
)
def test_holdings_validation(tmp_path, row, msg):
    p = tmp_path / "holdings.csv"
    p.write_text(
        "ticker,name,market,quantity,avg_cost,cost_currency,purchase_date\n" + row + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerError, match=msg):
        load_holdings(p)


def test_duplicate_ticker_rejected(tmp_path):
    p = tmp_path / "holdings.csv"
    p.write_text(
        "ticker,name,market,quantity,avg_cost,cost_currency,purchase_date\n"
        "7203,x,JP,1,3000,JPY,\n7203,x,JP,2,3100,JPY,\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerError, match="重複"):
        load_holdings(p)


def test_comma_in_numbers_and_bom(tmp_path):
    p = tmp_path / "holdings.csv"
    p.write_text(
        "﻿ticker,name,market,quantity,avg_cost,cost_currency,purchase_date\n"
        '7203,x,JP,1,"3,000",,\n',
        encoding="utf-8",
    )
    h = load_holdings(p)[0]
    assert h.avg_cost == 3000 and h.cost_currency == "JPY" and h.purchase_date is None


# --- Yahoo応答の解析 ---

def yahoo(closes, ts=(1794528000, 1794614400, 1794700800), offset=32400):
    return {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": offset},
                    "timestamp": list(ts),
                    "indicators": {"quote": [{"close": list(closes)}]},
                }
            ],
            "error": None,
        }
    }


def test_parse_yahoo_chart_takes_last_two_closes():
    q = parse_yahoo_chart(yahoo([2600.0, 2700.0, 2500.0]), "7203.T")
    assert (q.close, q.prev_close) == (2500.0, 2700.0)
    assert q.change_pct == pytest.approx(2500 / 2700 - 1)


def test_parse_yahoo_chart_skips_null_bars():
    q = parse_yahoo_chart(yahoo([2600.0, 2700.0, None]), "7203.T")
    assert (q.close, q.prev_close) == (2700.0, 2600.0)


def test_parse_yahoo_chart_uses_exchange_local_date():
    # 2026-11-13 00:00 UTC = 東京では 11-13 09:00、ニューヨークでは 11-12 19:00
    ts = (1794441600, 1794528000)
    assert parse_yahoo_chart(yahoo([1.0, 2.0], ts, 32400), "x").price_date == date(2026, 11, 13)
    assert parse_yahoo_chart(yahoo([1.0, 2.0], ts, -18000), "x").price_date == date(2026, 11, 12)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"chart": {"result": None, "error": {"code": "Not Found"}}},
        yahoo([2500.0, None, None]),
    ],
)
def test_parse_yahoo_chart_errors(payload):
    with pytest.raises(PriceError):
        parse_yahoo_chart(payload, "7203.T")
