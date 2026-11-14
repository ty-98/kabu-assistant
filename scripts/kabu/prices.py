"""終値の取得。提供元は差し替え可能 (試作: Yahoo Finance chart / 将来: J-Quants)。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

FX_SYMBOL = "USDJPY=X"


class PriceError(RuntimeError):
    """価格を取得できなかった、または内容が不正。"""


@dataclass(frozen=True)
class Quote:
    symbol: str
    close: float
    prev_close: float
    price_date: date

    @property
    def change_pct(self) -> float:
        return self.close / self.prev_close - 1


class PriceProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> Quote: ...


def parse_yahoo_chart(payload: dict, symbol: str) -> Quote:
    """Yahoo Finance chart API (v8) のJSONから直近2営業日の終値を取り出す。"""
    try:
        chart = payload["chart"]
        if chart.get("error"):
            raise PriceError(f"{symbol}: {chart['error']}")
        result = chart["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        offset = result.get("meta", {}).get("gmtoffset", 0)
    except (KeyError, IndexError, TypeError) as e:
        raise PriceError(f"{symbol}: 応答の形式が想定と違う") from e

    tz = timezone(timedelta(seconds=offset))
    bars = [
        (datetime.fromtimestamp(ts, tz).date(), float(c))
        for ts, c in zip(timestamps, closes)
        if c is not None
    ]
    if len(bars) < 2:
        raise PriceError(f"{symbol}: 終値が2日分そろわない")
    (_, prev_close), (price_date, close) = bars[-2], bars[-1]
    if close <= 0 or prev_close <= 0:
        raise PriceError(f"{symbol}: 終値が0以下")
    return Quote(symbol=symbol, close=close, prev_close=prev_close, price_date=price_date)


class YahooChartProvider:
    """Yahoo Finance の chart エンドポイント。非公式・無保証。利用規約は未確認。"""

    name = "yahoo_chart"
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=1d"

    def __init__(self, retries: int = 3, timeout: float = 20.0, pause: float = 2.0):
        self.retries = retries
        self.timeout = timeout
        self.pause = pause

    def get_quote(self, symbol: str) -> Quote:
        url = self.URL.format(symbol=urllib.parse.quote(symbol))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.load(resp)
                return parse_yahoo_chart(payload, symbol)
            except PriceError:
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
                if attempt < self.retries - 1:
                    time.sleep(self.pause * (attempt + 1))
        raise PriceError(f"{symbol}: 取得失敗 ({last})")


class FileProvider:
    """JSONファイルから価格を読む。テストとオフライン確認用。

    形式: {"7203.T": {"close": 2500, "prev_close": 2700, "price_date": "2026-11-13"}, ...}
    """

    name = "file"

    def __init__(self, path: Path):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))

    def get_quote(self, symbol: str) -> Quote:
        try:
            d = self.data[symbol]
            return Quote(
                symbol=symbol,
                close=float(d["close"]),
                prev_close=float(d["prev_close"]),
                price_date=date.fromisoformat(d["price_date"]),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise PriceError(f"{symbol}: 価格ファイルに有効なデータがない") from e
