# Instagram Insights Collector MVP Architecture

- Task ID: `TASK-20260907-232607-D242`
- 改訂日: 2026-09-08
- 担当: 神谷 拓海（System Integration / Architecture）
- 依頼・最終検収: 佐伯 真琴（System Integration / Management）
- 入力: `2026-09-07_mvp-requirements.md`
- status: `work_completed`（Director review待ち。実装はSystem QA passedまでリリース不可）

## 1. 結論

MVPの実行時構成は次の3要素だけとする。

```text
EventBridge Scheduler（毎時、UTC）
        |
        v
単一 Collector Lambda ── Instagram API
        |
        v
単一 DynamoDB table（観測itemのみ）
```

必須付随物は、Scheduler実行role、Lambda実行role、Lambdaの標準CloudWatch Logsだけである。SQS/DLQ、SNS、alarms、PITR、custom metrics、SSM、GSI、`CONTROL` item、`RUN` itemはMVPに作らない。分析、AI、MCP、Vercelも対象外とする。

## 2. 処理契約

1. Schedulerは `cron(0 * * * ? *)`、UTC、Flexible Time Window OFF、retry 0回でLambdaを毎正時に起動し、`scheduled_time`を渡す。
2. Lambdaは `scheduled_time`をUTC正時へ正規化し、観測枠 `slot_start` とする。
3. 対象アカウントのmediaをcursor終端まで列挙する。`VIDEO` かつ `REELS` だけを扱う。
4. `slot_start < published_at + 30 days` のReelsだけinsights APIを呼ぶ。30日ちょうど以後は呼ばない。
5. 同じ `media_id + slot_start` が保存済みならAPI呼出しを省略する。未保存ならinsightsを取得し、条件付き`PutItem`で1件だけ保存する。
6. 一部mediaが失敗しても他mediaを続行し、最後に失敗が1件でもあれば要約を標準ログへ出してLambdaを失敗終了する。追加retry基盤は使わず、次回毎時実行で30日未満の対象を再取得する。

過去枠の値は後から復元できないため、失敗した枠のバックフィルはしない。次回実行は新しい観測枠として保存する。

## 3. DynamoDB契約

単一tableは `PROVISIONED` の初期 `5 RCU / 5 WCU`、AWS所有キーによる標準暗号化、TTLなし、PITRなし、GSIなしとする。

| 属性 | 値 |
| --- | --- |
| `PK` | `MEDIA#<media_id>` |
| `SK` | `OBS#<slot_start ISO-8601 UTC>` |
| 必須属性 | `instagram_account_id`, `media_id`, `media_type`, `media_product_type`, `published_at`, `slot_start`, `observed_at`, `elapsed_seconds`, `metrics`, `missing_metrics`, `api_version`, `status` |

`GetItem`で既存枠を確認し、保存は `attribute_not_exists(PK) AND attribute_not_exists(SK)` の条件付き`PutItem`とする。`status`は `SUCCESS` または、指標の一部が未返却の `PARTIAL`。未返却指標を0で補完しない。API失敗はitem化せず、秘密を除いたエラー分類を標準ログへ残す。

media一覧を毎時取得して30日条件をその場で判定するため、media registry、active query、`MEDIA` / `CONTROL` / `RUN` item、GSIは不要である。

## 4. 秘密・設定と最小IAM

- GitHub Environment secret `INSTAGRAM_ACCESS_TOKEN`をCloudFormationの`NoEcho` parameterへ渡し、Lambda環境変数へ設定する。Lambda環境変数はAWS標準の保存時暗号化を使い、custom KMS keyは作らない。
- GitHub Actionsではshell traceを無効にし、secretをecho、artifact、output、`samconfig`へ残さない。CloudFormationのMetadata・Outputsにもsecretを置かない。
- 非秘密設定（account ID、API version、metrics list、table名）はCloudFormation parameterまたはGitHub Environment variablesで管理する。
- token、Authorization header、query string、API raw error bodyをログへ出さない。Lambda設定を読めるIAM principalも運用上の秘密閲覧権限として制限する。
- Lambda roleは対象tableへの `dynamodb:GetItem` / `dynamodb:PutItem` と対象log groupへの標準Logs書込みだけを許可する。
- Scheduler roleは対象Lambdaへの `lambda:InvokeFunction`だけを許可する。
- LambdaはVPCへ入れず、Instagram APIへの外向き通信だけを行う。`ssm:GetParameter`と`cloudwatch:PutMetricData`は付与しない。

SSMを使わない代わりに、token更新はGitHub Environment secretを更新して同じworkflowを再実行する。環境変数方式は閲覧権限管理が必要だが、MVPでは追加secret serviceとruntime API呼出しを増やさないことを優先する。

## 5. CloudFormation / GitHub Actions境界

CloudFormationが所有するのはScheduler、Lambda、DynamoDB table、2つの最小role/policy、保持30日の標準log groupだけとする。tableには `DeletionPolicy: Retain` と `UpdateReplacePolicy: Retain`を付けるが、PITRは有効化しない。

GitHub ActionsはOIDCで既存の最小deploy roleを引き受け、test、`sam build`、template検証、change set確認、deploy、Lambda DryRunを行う。長期AWS keyは保存しない。Lambda zipはworkflowで生成し、`sam deploy --resolve-s3`の管理artifact領域を使う。プロジェクト専用artifact bucketはstackへ追加せず、artifactはruntime構成に数えない。

CloudFormation inline codeは採用しない。複数moduleとtest可能性を失うためである。別のcode pipelineやimage registryもMVPでは追加しない。

## 6. 障害境界と復旧

- 配送失敗・実行失敗: 到達した実行はLambda標準ログと失敗結果で確認する。配送失敗eventは保持せず、次回毎時実行へ委ねる。DLQ、alarm、通知は作らない。
- 認証失効: Lambdaを失敗させ、token値を含めず分類をログへ残す。secret更新後にworkflowを再実行する。
- 部分失敗: 成功mediaは保存済みのまま維持し、失敗mediaと件数をログへ残して起動を失敗扱いにする。
- code/config不具合: Schedulerをdisableし、直前の承認済みcommitを再deployしてからenableする。
- table誤変更: Schedulerを止め、Retainされたtableを保全する。PITRがないため、破壊的schema変更とtable置換をMVPでは禁止する。

## 7. 既存成果物からの移行

既存Backend/Platform成果物は旧設計向けの静的成果物で、Platform記録上AWSへ未デプロイである。したがってlive data migrationはない。

- 再利用候補: Instagram clientのcursor走査、Reels filter、30日境界、UTC slot、secret redaction、fixture/unit tests。
- Backendで除去・置換: SSM取得、MEDIA registry、GSI query/advance/complete、RUN item、ERROR observation前提、custom metric送信。repositoryは観測itemの`GetItem` / 条件付き`PutItem`だけへ縮小する。
- Platformで除去・置換: SQS/DLQ、SNS、alarms、PITR、SSM、GSI、custom metrics権限と設定。template/workflowを本書の構成へ縮小する。

想定外に旧stackが存在した場合は自動更新せず、Schedulerを停止し、tableとstack状態を保全して佐伯へ返す。旧tableを破壊的に変換しない。

## 8. 無料成立条件

1か月の実行回数は概ね720〜744回。月間負荷は次で評価する。

- Instagram API calls: `実行回数 × (media一覧ページ数 + 30日未満Reels数)`
- Lambda: `実行回数 × 平均実行秒 × memory GB`
- DynamoDB: 対象枠ごとに最大1 read + 1 write、保存量は全観測itemサイズの累計
- Logs: 各run要約と失敗詳細の取込・保持量
- deployment artifact: SAM管理領域の保存・request量

対象AWS account/Regionの他用途を含む実使用量が、その時点の無料枠またはcredits内である場合だけ無料となる。無料を保証しない。media履歴ページ数、30日未満Reels数、Lambda時間、itemサイズ、累積table容量、ログ量、GitHub Actions minutes、artifact保存量の増加で課金され得る。Platform担当がdeploy前に対象account/Regionの現行条件と見積りを確認する。

## 9. 代替案

| 案 | 判断 | 理由 |
| --- | --- | --- |
| GSIでactive mediaを管理 | MVP不採用 | media一覧の毎時列挙で代替でき、状態itemとindexを増やす |
| SQS/DLQとalarm通知 | MVP不採用 | 標準ログと次回実行で開始し、実測した欠損リスクから再評価する |
| SSM/Secrets Manager | MVP不採用 | GitHub secret→NoEcho→Lambda環境変数で開始し、runtime componentを増やさない |
| PITR | MVP不採用 | MVPは破壊的変更禁止とRetainで運用し、復旧要件が上がった時に再評価する |

## 10. 後続担当境界

- Backend: 上記の再利用候補を残して単一Lambdaを縮小し、`media_id + UTC slot`の冪等保存、30日停止、secret redactionをunit testする。AWS resourceは変更しない。
- Platform: CloudFormation/SAM、最小IAM、OIDC workflow、GitHub secret反映、標準log groupを本書へ合わせる。Backendの処理ロジックは変更しない。
- QA: Backend/Platform統合後に毎時起動、Reels限定、同一枠二重実行、30日境界、部分失敗、認証失効、secret非露出、deploy/rollbackを確認する。passed記録前はリリース不可。

## 11. 自己確認

- [x] 実行時構成はScheduler、単一Lambda、単一DynamoDB table。
- [x] 指定されたMVP対象外要素を構成・IAM・処理契約から除外。
- [x] `media_id + UTC観測枠`、30日停止、標準ログと次回実行の障害契約を定義。
- [x] GitHub Actions、CloudFormation、code artifact、secretの最小境界を定義。
- [x] 無料成立条件、対象外、移行・rollback、後続担当境界を定義。

設計改訂の自己確認はpassed。実装と実AWS/Instagram API検証は後続Task、最終受入は佐伯 真琴の担当とする。
