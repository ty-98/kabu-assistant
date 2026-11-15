"""Web画面からの点検リクエスト。保有はリクエストごとに受け取り、保存しない。"""
from __future__ import annotations

import copy
from datetime import date

from .ledger import LedgerError, parse_holdings
from .prices import PriceProvider
from .report import build_report, summary_text

MAX_HOLDINGS = 30

# 画面から変えられるルールと、受け付ける範囲
RULE_LIMITS = {
    "concentration": ("max_weight", 0.01, 1.0),
    "drawdown_from_cost": ("threshold", -0.95, -0.01),
    "daily_move": ("threshold", 0.01, 0.5),
}


class RequestError(ValueError):
    """入力が不正。400で返す。"""


def _rules(base_config: dict, requested: object) -> dict:
    config = copy.deepcopy(base_config)
    rules = config.setdefault("rules", {})
    # 売買履歴は画面で扱わないので、売買系ルールは止める
    for name in ("trade_count", "off_hours_trade"):
        rules.setdefault(name, {})["enabled"] = False
    if requested is None:
        return config
    if not isinstance(requested, dict):
        raise RequestError("rules はオブジェクトで送る")
    for name, value in requested.items():
        if name not in RULE_LIMITS:
            raise RequestError(f"未対応のルール: {name}")
        if not isinstance(value, dict):
            raise RequestError(f"rules.{name} はオブジェクトで送る")
        key, lo, hi = RULE_LIMITS[name]
        cfg = rules.setdefault(name, {})
        if "enabled" in value:
            cfg["enabled"] = bool(value["enabled"])
        if key in value:
            try:
                n = float(value[key])
            except (TypeError, ValueError) as e:
                raise RequestError(f"rules.{name}.{key} が数値でない") from e
            if not lo <= n <= hi:
                raise RequestError(f"rules.{name}.{key} は {lo}〜{hi} の範囲")
            cfg[key] = n
    return config


def check(payload: object, provider: PriceProvider, run_date: date, base_config: dict) -> dict:
    if not isinstance(payload, dict):
        raise RequestError("JSONオブジェクトで送る")
    rows = payload.get("holdings")
    if not isinstance(rows, list) or not rows:
        raise RequestError("holdings に1銘柄以上入れる")
    if len(rows) > MAX_HOLDINGS:
        raise RequestError(f"holdings は {MAX_HOLDINGS} 銘柄まで")
    if not all(isinstance(r, dict) for r in rows):
        raise RequestError("holdings の各行はオブジェクトで送る")

    try:
        holdings = parse_holdings([(i, r) for i, r in enumerate(rows, start=1)], "入力")
    except LedgerError as e:
        raise RequestError(str(e)) from e

    config = _rules(base_config, payload.get("rules"))
    report = build_report(holdings, [], config, provider, run_date)
    report["summary_text"] = summary_text(report)
    return report
