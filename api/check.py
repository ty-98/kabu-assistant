"""Vercel Python Function: POST /api/check

保有とルールを受け取り、実際の終値で評価・点検して返す。保有はどこにも保存しない。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from kabu.prices import FileProvider, YahooChartProvider  # noqa: E402
from kabu.report import JST  # noqa: E402
from kabu.web import RequestError, check  # noqa: E402

MAX_BODY = 32 * 1024
BASE_CONFIG = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8")) or {}


def _provider():
    # ローカル確認用。本番では未設定で、実際の終値を取得する
    path = os.environ.get("KABU_PRICES_FILE")
    return FileProvider(Path(path)) if path else YahooChartProvider(retries=2, timeout=8.0, pause=1.0)


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send(400, {"error": f"本文は1〜{MAX_BODY}バイト"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            report = check(payload, _provider(), datetime.now(JST).date(), BASE_CONFIG)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "JSONとして読めない"})
            return
        except RequestError as e:
            self._send(400, {"error": str(e)})
            return
        self._send(200, report)

    def do_GET(self) -> None:  # noqa: N802
        self._send(405, {"error": "POSTで送る"})

    def log_message(self, format, *args):  # noqa: A002
        # 保有の中身をログに残さない。パスとステータスだけ
        sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")
