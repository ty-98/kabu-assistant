"""ルール判定。入力は評価済みの保有と売買履歴、出力はアラートの一覧。

売買の指示は出さない。出すのは「どのルールに触れたか」と数値だけ。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from .ledger import Trade


@dataclass(frozen=True)
class Position:
    ticker: str
    name: str
    market: str
    quantity: float
    close: float
    prev_close: float
    price_date: date
    day_change_pct: float
    value_jpy: float
    cost_jpy: float
    pnl_jpy: float
    pnl_pct: float
    weight: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price_date"] = self.price_date.isoformat()
        return d


@dataclass(frozen=True)
class Alert:
    id: str  # 同じ事象なら日をまたいでも同じID。新規かどうかの判定に使う
    rule: str
    ticker: str | None
    value: float
    threshold: float
    message: str
    details: dict


def _pct(x: float, signed: bool = False) -> str:
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def _yen(x: float) -> str:
    return f"{x:,.0f}円"


def _enabled(rules: dict, name: str) -> dict | None:
    cfg = rules.get(name) or {}
    return cfg if cfg.get("enabled") else None


def check_concentration(positions: list[Position], total: float, rules: dict) -> list[Alert]:
    cfg = _enabled(rules, "concentration")
    if not cfg:
        return []
    limit = float(cfg["max_weight"])
    alerts = []
    for p in positions:
        if p.weight > limit:
            excess = p.value_jpy - total * limit
            alerts.append(
                Alert(
                    id=f"concentration:{p.ticker}",
                    rule="concentration",
                    ticker=p.ticker,
                    value=p.weight,
                    threshold=limit,
                    message=(
                        f"{p.name} ({p.ticker}) の比率が {_pct(p.weight)}。"
                        f"上限 {_pct(limit)} を {_yen(excess)} 超過"
                    ),
                    details={"value_jpy": p.value_jpy, "excess_jpy": excess},
                )
            )
    return alerts


def check_drawdown(positions: list[Position], rules: dict) -> list[Alert]:
    cfg = _enabled(rules, "drawdown_from_cost")
    if not cfg:
        return []
    threshold = float(cfg["threshold"])
    alerts = []
    for p in positions:
        if p.pnl_pct <= threshold:
            alerts.append(
                Alert(
                    id=f"drawdown_from_cost:{p.ticker}",
                    rule="drawdown_from_cost",
                    ticker=p.ticker,
                    value=p.pnl_pct,
                    threshold=threshold,
                    message=(
                        f"{p.name} ({p.ticker}) が取得単価から {_pct(p.pnl_pct, True)} "
                        f"(含み損益 {p.pnl_jpy:+,.0f}円)。基準 {_pct(threshold, True)}。"
                        "買った時の理由が崩れていないか確認"
                    ),
                    details={"pnl_jpy": p.pnl_jpy, "cost_jpy": p.cost_jpy},
                )
            )
    return alerts


def check_daily_move(positions: list[Position], rules: dict) -> list[Alert]:
    cfg = _enabled(rules, "daily_move")
    if not cfg:
        return []
    threshold = float(cfg["threshold"])
    alerts = []
    for p in positions:
        if abs(p.day_change_pct) >= threshold:
            alerts.append(
                Alert(
                    id=f"daily_move:{p.ticker}:{p.price_date.isoformat()}",
                    rule="daily_move",
                    ticker=p.ticker,
                    value=p.day_change_pct,
                    threshold=threshold,
                    message=(
                        f"{p.name} ({p.ticker}) が前日比 {_pct(p.day_change_pct, True)} "
                        f"({p.price_date.isoformat()} 終値)。基準 ±{_pct(threshold)}。"
                        "その日の開示とニュースを確認"
                    ),
                    details={"close": p.close, "prev_close": p.prev_close},
                )
            )
    return alerts


def _month_trades(trades: list[Trade], run_date: date) -> list[Trade]:
    return [t for t in trades if (t.date.year, t.date.month) == (run_date.year, run_date.month)]


def _spread_rate(spread: dict, market: str, session: str) -> float:
    return float(spread[market][session])


def check_trade_count(trades: list[Trade], run_date: date, rules: dict, spread: dict) -> list[Alert]:
    cfg = _enabled(rules, "trade_count")
    if not cfg:
        return []
    limit = int(cfg["max_per_month"])
    month = _month_trades(trades, run_date)
    if len(month) <= limit:
        return []
    paid = sum(t.amount_jpy * _spread_rate(spread, t.market, t.session) for t in month)
    ym = f"{run_date.year}-{run_date.month:02d}"
    return [
        Alert(
            id=f"trade_count:{ym}:{len(month)}",
            rule="trade_count",
            ticker=None,
            value=float(len(month)),
            threshold=float(limit),
            message=(
                f"{ym} の売買が {len(month)} 回。上限 {limit} 回を超過。"
                f"今月支払ったスプレッドの概算 {_yen(paid)}"
            ),
            details={"month": ym, "spread_paid_jpy": paid},
        )
    ]


def check_off_hours(trades: list[Trade], run_date: date, rules: dict, spread: dict) -> list[Alert]:
    cfg = _enabled(rules, "off_hours_trade")
    if not cfg:
        return []
    alerts = []
    for t in _month_trades(trades, run_date):
        if t.session != "off_hours":
            continue
        regular = _spread_rate(spread, t.market, "regular")
        off = _spread_rate(spread, t.market, "off_hours")
        extra = t.amount_jpy * (off - regular)
        alerts.append(
            Alert(
                id=f"off_hours_trade:{t.date.isoformat()}:{t.time}:{t.ticker}:{t.side}",
                rule="off_hours_trade",
                ticker=t.ticker,
                value=off,
                threshold=regular,
                message=(
                    f"{t.date.isoformat()} {t.time} の {t.ticker} は立会時間外の約定。"
                    f"時間内との差額の概算 {_yen(extra)} "
                    f"(スプレッド {_pct(off)} / 時間内 {_pct(regular)})"
                ),
                details={"amount_jpy": t.amount_jpy, "extra_cost_jpy": extra},
            )
        )
    return alerts


def evaluate(
    positions: list[Position],
    total_value_jpy: float,
    trades: list[Trade],
    run_date: date,
    config: dict,
) -> list[Alert]:
    rules = config.get("rules") or {}
    spread = (config.get("settings") or {}).get("spread") or {}
    return [
        *check_concentration(positions, total_value_jpy, rules),
        *check_drawdown(positions, rules),
        *check_daily_move(positions, rules),
        *check_trade_count(trades, run_date, rules, spread),
        *check_off_hours(trades, run_date, rules, spread),
    ]
