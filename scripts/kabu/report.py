"""日次の評価とレポート (JSON) の組み立て。

価格を1つでも取得できない、または古い場合は status=failed とし、ルール判定をしない。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from .ledger import Holding, Trade
from .prices import FX_SYMBOL, PriceError, PriceProvider, Quote
from .rules import Position, evaluate

JST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 1


def _fetch_quotes(
    holdings: list[Holding], provider: PriceProvider, run_date: date, stale_days: int
) -> tuple[dict[str, Quote], list[str]]:
    symbols = [h.symbol for h in holdings]
    needs_fx = any(h.price_currency == "USD" or h.cost_currency == "USD" for h in holdings)
    if needs_fx:
        symbols.append(FX_SYMBOL)

    quotes: dict[str, Quote] = {}
    errors: list[str] = []
    for symbol in symbols:
        try:
            q = provider.get_quote(symbol)
        except PriceError as e:
            errors.append(str(e))
            continue
        age = (run_date - q.price_date).days
        if age > stale_days:
            errors.append(
                f"{symbol}: 終値が古い ({q.price_date.isoformat()}、{age}日前)。判定に使わない"
            )
            continue
        quotes[symbol] = q
    return quotes, errors


def _positions(holdings: list[Holding], quotes: dict[str, Quote]) -> list[Position]:
    fx = quotes[FX_SYMBOL].close if FX_SYMBOL in quotes else None
    raw = []
    for h in holdings:
        q = quotes[h.symbol]
        price_fx = fx if h.price_currency == "USD" else 1.0
        cost_fx = fx if h.cost_currency == "USD" else 1.0
        value = h.quantity * q.close * price_fx
        cost = h.quantity * h.avg_cost * cost_fx
        raw.append((h, q, value, cost))

    total = sum(v for _, _, v, _ in raw)
    return [
        Position(
            ticker=h.ticker,
            name=h.name,
            market=h.market,
            quantity=h.quantity,
            close=q.close,
            prev_close=q.prev_close,
            price_date=q.price_date,
            day_change_pct=q.change_pct,
            value_jpy=value,
            cost_jpy=cost,
            pnl_jpy=value - cost,
            pnl_pct=value / cost - 1,
            weight=value / total,
        )
        for h, q, value, cost in raw
    ]


def build_report(
    holdings: list[Holding],
    trades: list[Trade],
    config: dict,
    provider: PriceProvider,
    run_date: date,
    previous: dict | None = None,
    now: datetime | None = None,
) -> dict:
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "date": run_date.isoformat(),
        "generated_at": (now or datetime.now(JST)).isoformat(timespec="seconds"),
        "price_source": provider.name,
        "status": "ok",
        "errors": [],
        "totals": None,
        "fx": None,
        "positions": [],
        "alerts": [],
    }

    if not holdings:
        report["status"] = "empty"
        report["errors"] = ["保有台帳 (data/holdings.csv) が空"]
        return report

    stale_days = int((config.get("settings") or {}).get("stale_price_days", 5))
    quotes, errors = _fetch_quotes(holdings, provider, run_date, stale_days)
    if errors:
        report["status"] = "failed"
        report["errors"] = errors
        return report

    positions = _positions(holdings, quotes)
    total_value = sum(p.value_jpy for p in positions)
    total_cost = sum(p.cost_jpy for p in positions)
    prev_value = sum(p.value_jpy / (1 + p.day_change_pct) for p in positions)

    report["totals"] = {
        "value_jpy": total_value,
        "cost_jpy": total_cost,
        "pnl_jpy": total_value - total_cost,
        "pnl_pct": total_value / total_cost - 1,
        "day_change_jpy": total_value - prev_value,
        "day_change_pct": total_value / prev_value - 1,
    }
    if FX_SYMBOL in quotes:
        fxq = quotes[FX_SYMBOL]
        report["fx"] = {"USDJPY": fxq.close, "price_date": fxq.price_date.isoformat()}
    report["positions"] = [p.to_dict() for p in sorted(positions, key=lambda p: -p.value_jpy)]

    # 前回レポートに無かったアラートだけ is_new=true。即時通知の対象はこれ
    prev_ids = {a["id"] for a in (previous or {}).get("alerts", [])}
    report["alerts"] = [
        {**asdict(a), "is_new": a.id not in prev_ids}
        for a in evaluate(positions, total_value, trades, run_date, config)
    ]
    return report


def summary_text(report: dict) -> str:
    """通知文の土台。数字はすべてここ (コード) で作る。"""
    lines = [f"【{report['date']} 日次点検】"]
    if report["status"] != "ok":
        label = "保有台帳が空" if report["status"] == "empty" else "データ取得に失敗。本日は判定していない"
        lines.append(label)
        lines += [f"- {e}" for e in report["errors"]]
        return "\n".join(lines)

    t = report["totals"]
    lines.append(
        f"評価額 {t['value_jpy']:,.0f}円 (前日比 {t['day_change_jpy']:+,.0f}円 / "
        f"{t['day_change_pct'] * 100:+.1f}%)、"
        f"含み損益 {t['pnl_jpy']:+,.0f}円 ({t['pnl_pct'] * 100:+.1f}%)"
    )
    new = [a for a in report["alerts"] if a["is_new"]]
    cont = [a for a in report["alerts"] if not a["is_new"]]
    if not report["alerts"]:
        lines.append("ルール抵触なし")
    if new:
        lines.append(f"新規のルール抵触 {len(new)}件:")
        lines += [f"- {a['message']}" for a in new]
    if cont:
        lines.append(f"継続中 {len(cont)}件:")
        lines += [f"- {a['message']}" for a in cont]
    return "\n".join(lines)
