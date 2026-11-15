"""保有台帳 (holdings.csv) と売買履歴 (trades.csv) の読み込みと検証。"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MARKETS = ("JP", "US")
CURRENCIES = ("JPY", "USD")
SIDES = ("buy", "sell")
SESSIONS = ("regular", "off_hours")
TICKER_RE = re.compile(r"[A-Za-z0-9.\-]{1,12}")


class LedgerError(ValueError):
    """CSVの内容が不正。"""


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str
    market: str  # JP / US
    quantity: float
    avg_cost: float  # 1株あたり取得単価 (cost_currency建て)
    cost_currency: str  # JPY / USD
    purchase_date: date | None

    @property
    def symbol(self) -> str:
        """価格取得用のシンボル。国内株は東証 (.T) を付ける。"""
        return f"{self.ticker}.T" if self.market == "JP" else self.ticker

    @property
    def price_currency(self) -> str:
        return "JPY" if self.market == "JP" else "USD"


@dataclass(frozen=True)
class Trade:
    date: date
    time: str
    ticker: str
    market: str
    side: str  # buy / sell
    quantity: float
    price: float
    amount_jpy: float
    session: str  # regular / off_hours
    reason: str
    expectation: str


def _rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    if not path.exists():
        raise LedgerError(f"{path} が存在しない")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        out = []
        for i, row in enumerate(reader, start=2):
            cleaned = {k: (v or "").strip() for k, v in row.items() if k}
            if not any(cleaned.values()):
                continue
            out.append((i, cleaned))
        return out


def _num(value: str, field: str, where: str, *, positive: bool = True) -> float:
    try:
        n = float(value.replace(",", ""))
    except ValueError as e:
        raise LedgerError(f"{where}: {field} が数値でない: {value!r}") from e
    if positive and n <= 0:
        raise LedgerError(f"{where}: {field} は正の数にする: {value!r}")
    return n


def _choice(value: str, allowed: tuple[str, ...], field: str, where: str) -> str:
    if value not in allowed:
        raise LedgerError(f"{where}: {field} は {'/'.join(allowed)} のいずれか: {value!r}")
    return value


def _date(value: str, field: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise LedgerError(f"{where}: {field} は YYYY-MM-DD 形式: {value!r}") from e


def load_holdings(path: Path) -> list[Holding]:
    return parse_holdings(_rows(path), path.name)


def parse_holdings(rows: list[tuple[int, dict[str, str]]], source: str) -> list[Holding]:
    """(行番号, {列名: 値}) の並びから保有を作る。CSVとWeb入力で共通の検証。"""
    holdings: list[Holding] = []
    seen: set[str] = set()
    for line, raw in rows:
        r = {k: str(v if v is not None else "").strip() for k, v in raw.items()}
        where = f"{source}:{line}"
        ticker = r.get("ticker", "")
        if not ticker:
            raise LedgerError(f"{where}: ticker が空")
        if not TICKER_RE.fullmatch(ticker):
            raise LedgerError(f"{where}: ticker は英数字・ピリオド・ハイフン12文字以内: {ticker!r}")
        ticker = ticker.upper()
        if ticker in seen:
            raise LedgerError(f"{where}: ticker が重複: {ticker} (1銘柄1行にまとめる)")
        seen.add(ticker)
        market = _choice(r.get("market", "").upper(), MARKETS, "market", where)
        default_ccy = "JPY"
        holdings.append(
            Holding(
                ticker=ticker,
                name=r.get("name", "") or ticker,
                market=market,
                quantity=_num(r.get("quantity", ""), "quantity", where),
                avg_cost=_num(r.get("avg_cost", ""), "avg_cost", where),
                cost_currency=_choice(
                    (r.get("cost_currency", "") or default_ccy).upper(),
                    CURRENCIES,
                    "cost_currency",
                    where,
                ),
                purchase_date=_date(r["purchase_date"], "purchase_date", where)
                if r.get("purchase_date")
                else None,
            )
        )
    return holdings


def load_trades(path: Path) -> list[Trade]:
    trades: list[Trade] = []
    for line, r in _rows(path):
        where = f"{path.name}:{line}"
        if not r.get("ticker"):
            raise LedgerError(f"{where}: ticker が空")
        trades.append(
            Trade(
                date=_date(r.get("date", ""), "date", where),
                time=r.get("time", ""),
                ticker=r["ticker"],
                market=_choice(r.get("market", "").upper(), MARKETS, "market", where),
                side=_choice(r.get("side", "").lower(), SIDES, "side", where),
                quantity=_num(r.get("quantity", ""), "quantity", where),
                price=_num(r.get("price", ""), "price", where),
                amount_jpy=_num(r.get("amount_jpy", ""), "amount_jpy", where),
                session=_choice(
                    (r.get("session", "") or "regular").lower(), SESSIONS, "session", where
                ),
                reason=r.get("reason", ""),
                expectation=r.get("expectation", ""),
            )
        )
    return trades
