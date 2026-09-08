# Instagram Insights Collector 最小Backend実装・検証記録

- Task: `TASK-20260908-092514-DB43`
- 上位参照: `TASK-20260907-232607-D242`
- 担当: System Integration / Backend Data・Integration（西村朱莉）
- 実装日: 2026-09-08
- 状態: Backend作業完了・Director一次検収待ち

## 実装差分

承認済み最小設計に合わせ、単一Lambdaの処理を観測itemだけに縮小した。

- `paging.next` をopaque URLとして終端まで追従し、`VIDEO` かつ `REELS` のみを評価する。
- Scheduler時刻をUTC時刻枠へ正規化し、投稿から厳密に30日未満だけを対象とする。ちょうど30日は除外する。
- `MEDIA#<media_id>` / `OBS#<slot_start>` の強整合`GetItem`で既存観測を確認し、既存時はInsights APIを呼ばない。
- 取得成功時だけ、`attribute_not_exists(PK) AND attribute_not_exists(SK)`付き`PutItem`で観測itemを保存する。競合時は冪等成功として扱う。
- 欠損metricはゼロ補完せず、`missing_metrics`と`PARTIAL`で保存する。
- メディア単位のAPI失敗は他メディアを継続処理し、走査後に秘密を含まない構造化標準ログを残してLambdaを失敗終了する。ERROR itemは保存しない。
- `INSTAGRAM_ACCESS_TOKEN`環境変数を用い、SSM取得、MEDIA registry、GSI query、RUN/CONTROL/ERROR item、custom CloudWatch metricsを削除した。

AWS infrastructureおよびGitHub Actions/workflowには変更を加えていない。

## Self-verification

実行コマンド:

```sh
PYTHONPATH=backend/src python3 -m unittest discover -s backend/tests -v
```

結果: **12 tests passed**。

含む確認項目:

- 実`InstagramClient`のopaque `paging.next`終端追従
- 短い`Retry-After`後の成功、長すぎる待機およびretry枯渇の`RATE_LIMITED`
- 30日境界、同一media/UTC枠の事前冪等確認、条件付きPut競合
- missing metricの`PARTIAL`保存
- 部分失敗継続、認証失敗・429の安全な分類、観測ERROR item非作成
- access tokenおよびURL queryのredaction、例外詳細をログに含めないこと

## 残存ゲート

資格情報・AWS接続情報は提供されていないため、次は未実施であり推測していない。

1. Instagram実アカウントでのページング、対象判定、metric可用性、API権限の確認。
2. 配備済みLambdaから実DynamoDBへのGetItem/条件付きPutItem権限とitem形状の確認。
3. Scheduler入力からCloudWatch標準ログまでを含む実環境の一時間枠実行確認。

これらは認証情報とPlatform管理下の配備環境が利用可能になった後に実施する必要がある。
