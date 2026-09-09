# Instagram Insights Collector

Instagram APIから投稿後30日以内のReelインサイトを1時間ごとに取得し、DynamoDBへ保存する最小構成です。デプロイはServerless Frameworkを使用します。

## 構成

- AWS Lambda: Instagram APIの呼び出しとDynamoDBへの保存
- Amazon DynamoDB: 時系列インサイトの保存
- Amazon EventBridge Scheduler: 毎時0分（UTC）にLambdaを起動
- CloudWatch Logs / IAM: Lambda実行に必要な最小設定

SQS、アラート、API Gatewayは使用しません。

## ローカルデプロイ

Node.js、npm、AWS CLIをインストールし、デプロイ権限を持つAWSアクセスキーとInstagram APIの値を環境変数へ設定します。

```bash
export AWS_ACCESS_KEY_ID='your-aws-access-key-id'
export AWS_SECRET_ACCESS_KEY='your-aws-secret-access-key'
export AWS_REGION='ap-northeast-1'
export INSTAGRAM_ACCOUNT_ID='your-instagram-account-id'
export INSTAGRAM_ACCESS_TOKEN='your-instagram-access-token'
```

一時認証情報を使う場合だけ、追加で`AWS_SESSION_TOKEN`を設定してください。値は`.env`などのリポジトリ内ファイルへ保存しないでください。

依存関係をインストールします。

```bash
npm ci
```

AWSへ変更を加えず、デプロイパッケージだけを生成する場合:

```bash
npx serverless package --stage production
```

生成物の`.serverless/`にはLambdaへ渡す環境変数が展開されます。検証後は削除し、Gitへ追加しないでください。

実際にデプロイする場合:

```bash
./deploy.sh -s production
```

`deploy.sh`は認証情報を引数で受け取らず、Serverlessのcredential設定へ永続保存もしません。現在のシェルに設定された`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、任意の`AWS_SESSION_TOKEN`をAWS SDKの標準認証として使用します。

## GitHub Actionsからのデプロイ

`main`へのpush、またはGitHub Actions画面からの手動実行で、Serverless Frameworkを使って`production`へデプロイします。GitHubリポジトリのSettingsでEnvironment `production`を作成し、次の値を登録してください。

| Name | Type | Purpose |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | Environment secret | デプロイ用IAMユーザーのアクセスキーID |
| `AWS_SECRET_ACCESS_KEY` | Environment secret | デプロイ用IAMユーザーのシークレットアクセスキー |
| `INSTAGRAM_ACCESS_TOKEN` | Environment secret | Instagram APIアクセストークン |
| `AWS_REGION` | Environment variable | デプロイ先リージョン（`ap-northeast-1`） |
| `INSTAGRAM_ACCOUNT_ID` | Environment variable | Instagram Account ID |

workflowはOIDCを使用せず、`AWS_DEPLOY_ROLE_ARN`も参照しません。Secretsの値をリポジトリ、workflow、ログへ記録しないでください。

## 既存SAMスタックからの移行

初回デプロイ前に既存スタックの状態を確認してください。

```bash
aws cloudformation describe-stacks \
  --stack-name instagram-insights-production \
  --region ap-northeast-1 \
  --query 'Stacks[0].StackStatus' \
  --output text
```

`CREATE_COMPLETE`または`UPDATE_COMPLETE`なら、同じスタックをServerlessで更新できます。既存DynamoDBの論理IDと物理名は維持し、Lambdaだけ衝突しない`instagram-insights-production-collector-v2`を先に作成してから、既存Schedulerの参照先を切り替えます。

`ROLLBACK_COMPLETE`の場合、`deploy.sh`は停止します。DynamoDBには既存データが残っている可能性があるため、スタックを自動削除せず、テーブルの所属・データを確認してからCloudFormationの復旧またはリソースインポート方針を決めてください。

## テスト

```bash
PYTHONPATH=backend/src python3 -m unittest discover -s backend/tests -v
```
