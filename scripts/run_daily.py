#!/usr/bin/env python3
"""日次点検の入口。終値を取得し、評価・ルール判定して reports/daily/ にJSONを書く。

使い方:
  python scripts/run_daily.py                      # 本番 (価格を取得して保存)
  python scripts/run_daily.py --dry-run            # 保存せず要約を表示
  python scripts/run_daily.py --prices-file x.json # 価格をファイルから読む (オフライン確認)
  python scripts/run_daily.py --dry-run --expect-total 123456
      # 合計評価額をPayPay証券の画面と照合 (差が1%超なら終了コード1)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kabu.ledger import LedgerError, load_holdings, load_trades  # noqa: E402
from kabu.prices import FileProvider, YahooChartProvider  # noqa: E402
from kabu.report import JST, build_report, summary_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _previous_report(report_dir: Path, run_date: date) -> dict | None:
    """run_date より前で、判定に成功した直近のレポート。"""
    for path in sorted(report_dir.glob("????-??-??.json"), reverse=True):
        if path.stem >= run_date.isoformat():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "ok":
            return data
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--date", type=date.fromisoformat, default=None, help="実行日 (既定: 今日 JST)")
    ap.add_argument("--prices-file", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--expect-total", type=float, default=None, help="証券画面の合計評価額 (円)")
    args = ap.parse_args(argv)

    root: Path = args.root
    run_date = args.date or datetime.now(JST).date()
    report_dir = root / "reports" / "daily"

    try:
        holdings = load_holdings(root / "data" / "holdings.csv")
        trades = load_trades(root / "data" / "trades.csv")
    except LedgerError as e:
        print(f"台帳エラー: {e}", file=sys.stderr)
        return 2
    config = yaml.safe_load((root / "config" / "rules.yaml").read_text(encoding="utf-8")) or {}

    provider = FileProvider(args.prices_file) if args.prices_file else YahooChartProvider()
    report = build_report(
        holdings, trades, config, provider, run_date, _previous_report(report_dir, run_date)
    )
    report["summary_text"] = summary_text(report)
    print(report["summary_text"])

    if not args.dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (report_dir / f"{run_date.isoformat()}.json").write_text(body, encoding="utf-8")
        (root / "reports" / "latest.json").write_text(body, encoding="utf-8")

    if args.expect_total is not None:
        if report["status"] != "ok":
            print("照合不可: 評価に失敗", file=sys.stderr)
            return 1
        actual = report["totals"]["value_jpy"]
        diff = actual / args.expect_total - 1
        print(f"照合: 台帳 {actual:,.0f}円 / 画面 {args.expect_total:,.0f}円 / 差 {diff * 100:+.2f}%")
        if abs(diff) > 0.01:
            print("差が1%超。台帳を反映しない", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
