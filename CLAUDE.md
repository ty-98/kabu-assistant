# kabu-assistant

個人用の株式投資アシスタント。決めたルールを毎日自動で点検し、判断材料と記録を残す。
要件の全文: docs/requirements.md

## 守ること

- 売買の指示を出さない。出すのは「どのルールに触れたか」「確認すべき材料」まで。判断と発注は本人
- 予測機能を作らない
- 数字 (損益、比率、金額) はコードの計算結果だけを使う。AIが暗算しない。通知文の数字は `reports/` のJSONから写す
- 証券口座のID・パスワードを保存しない。自動ログインを作らない
- リポジトリは非公開
- 価格取得に失敗した日は「失敗した」と通知する。古い数字で判定しない
- `config/rules.yaml` の数値は本人が決める。値動きを見てから基準を変える提案をしない
- フェーズの完了条件を満たすまで次のフェーズの機能を足さない (現在: フェーズ1)

## 構成

- `data/holdings.csv`: 保有台帳。1銘柄1行。`market` は JP/US、`avg_cost` は1株あたり、`cost_currency` は JPY/USD
- `data/trades.csv`: 売買履歴と理由。`session` は regular/off_hours
- `config/rules.yaml`: ルールと閾値
- `scripts/kabu/`: 台帳読み込み (ledger)、価格取得 (prices)、ルール判定 (rules)、レポート (report)
- `scripts/run_daily.py`: 日次点検の入口
- `reports/daily/YYYY-MM-DD.json`、`reports/latest.json`: 判定結果。GitHub Actionsが平日16:40 JSTに書く
- `knowledge/*.md`: 知識カード (フェーズ2)

## コマンド

```bash
pip install -r requirements-dev.txt
pytest -q
python scripts/run_daily.py --dry-run --prices-file tests/fixtures/prices.json
```

Claudeのクラウド環境からは株価サイトに接続できない。価格取得の実動作はGitHub Actionsで確認する。

## 手順: 保有の更新 (スクショを受け取った時)

1. スクショから銘柄、数量、取得単価、合計評価額を読み取り、読み取った値を本人に見せる
2. `data/holdings.csv` を更新。売買なら `data/trades.csv` に1行追加し、理由と想定を2〜3問で聞いて記入
3. `reports/latest.json` の終値で合計評価額を計算し、画面の合計と照合。差が1%超なら反映せず本人に確認
4. コミットしてプッシュ

## 手順: 日次通知 (スケジュールタスク)

1. `reports/latest.json` を読む。`date` が今日 (JST) でなければ「本日の点検結果が無い」と通知して終了
2. `status` が `failed` なら `summary_text` をそのまま通知
3. `status` が `ok` で `is_new: true` のアラートがあれば、`summary_text` を通知。数字は書き換えない
4. 新規アラートが無ければ通知しない
