# kabu-assistant

決めた投資ルールを平日毎日点検し、触れた時だけ知らせる個人用の仕組み。売買の指示も予測もしない。

## 動き

1. GitHub Actions が平日 16:40 JST に終値を取得 (`scripts/run_daily.py`)
2. 評価額、損益、銘柄ごとの比率を計算し、`config/rules.yaml` のルールを判定
3. 結果を `reports/daily/YYYY-MM-DD.json` と `reports/latest.json` にコミット
4. Claude のスケジュールタスクが `reports/latest.json` を読み、新規のルール抵触だけ通知

## Web画面 (Vercel)

`public/index.html` と `api/check.py`。保有を入れて「点検する」を押すと、実際の終値で評価とルール点検をする。保有はブラウザ内だけに保存。

ローカル確認 (価格はテスト用の値):

```bash
KABU_PRICES_FILE=tests/fixtures/prices.json python3 -c "from http.server import HTTPServer; import importlib.util as u; s=u.spec_from_file_location('c','api/check.py'); m=u.module_from_spec(s); s.loader.exec_module(m); HTTPServer(('127.0.0.1',8765),m.handler).serve_forever()"
```

## ルール

| ルール | キー | 初期状態 |
| --- | --- | --- |
| 1銘柄の上限比率 | `concentration` | 有効 (記入例 20%) |
| 取得単価からの下落 | `drawdown_from_cost` | 有効 (記入例 -15%) |
| 1日の急変 | `daily_move` | 有効 (記入例 ±7%) |
| 月の売買回数 | `trade_count` | 無効 (記入例 4回) |
| 立会時間外の約定 | `off_hours_trade` | 無効 |

数値は記入例。推奨値ではない。

## 使い始め

1. `data/holdings.csv` に保有を記入 (書式は `tests/fixtures/holdings.csv`)
2. `config/rules.yaml` の数値を決める
3. Actions タブで `daily-check` を手動実行し、`reports/latest.json` の合計評価額を証券画面と照合

```bash
python scripts/run_daily.py --dry-run --expect-total 123456
```

## 注意

- 株価は Yahoo Finance の非公式エンドポイントから取得。利用規約は未確認、停止の可能性あり。止まったら J-Quants に切り替える (`scripts/kabu/prices.py` に提供元を追加)
- 米国株を `cost_currency: USD` で持つ場合、取得額は現在の為替で円換算する。為替差損益は損益率に入らない
- スプレッド率は `config/rules.yaml` の `settings.spread`。公式ページで確認して書き換える
- 投資助言ではない
