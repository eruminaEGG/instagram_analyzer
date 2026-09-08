# Instagram Insights Collector 独立Gitリポジトリ分離QA証跡

## メタデータ

- 作成日: 2026-09-08
- 検証日時: 2026-09-08 19:11 JST
- QA担当: 藤井 七海（System Integration / QA）
- Company Task: `TASK-20260908-190529-5744`
- 対象変更Task: `TASK-20260908-190054-D5C2`
- 対象: `workspaces/system_integration/projects/instagram-insights-collector/`
- 目的: 独立Gitリポジトリ境界、収録範囲、workflow、秘密値、Backend/SAMの独立検証
- 総合判定: **passed**

実装ファイル、親・子repoのindex、remote、commit履歴には変更を加えていない。QAレポートと、子`.gitignore`で除外されるテスト/build生成物のみを作成した。

## 対象版の識別

| ファイル | SHA-256 |
| --- | --- |
| 親repo `.gitignore` | `886fb8a527e0e8a292d29800e65526e3a3f36d01882bafeac773f25132db809c` |
| 子repo `.gitignore` | `9806a9d4aaefc8f533777e8fda6b7dd8b5bf4ea7b5049d693041fd5fcba1964f` |
| 子repo workflow | `94679c51f1b920403e73d2c4ea0a0738ea0c04b963d298addbd311c4af7f67de` |
| `infra/template.yaml` | `5213583b7ff53c150daae1afb542b4ff364131fd7a4dc5653bbd6626706a883b` |
| `backend/tests/test_collector.py` | `a1ac3b9f9d769ae8e5b7bd19a15ffeab846dac84d9a362b086d933f576b6e645` |

## Acceptance Criteria結果

| 確認項目 | 実測 | 判定 |
| --- | --- | --- |
| 親`.gitignore`の対象限定除外 | 差分は説明コメントと`/workspaces/system_integration/projects/instagram-insights-collector/`のroot相対1パターンのみ | passed |
| 親git statusから対象消失 | 通常の`git status --porcelain=v1 --untracked-files=all`に対象パス0件。`git check-ignore -v`は親`.gitignore:50`を返した | passed |
| 親indexから対象分離 | `git ls-files 'workspaces/system_integration/projects/instagram-insights-collector/**'`は0件 | passed |
| 親workflow消失 | 親`.github/workflows/instagram-insights-collector.yml`はfilesystem上になく、親indexにも0件 | passed |
| 子repo root | `git rev-parse --show-toplevel`が対象projectディレクトリ自身を返した | passed |
| 子branch | unborn branch `main` | passed |
| 子remote | `git remote` 0件、local remote設定0件 | passed |
| 子commit | `git rev-list --all --count`は0、Git object countも0 | passed |
| 会社ファイル非混入 | `departments`、`knowledge`、別`workspaces`ディレクトリ0件。人物設定0件。外部symlink 0件、入れ子repo 0件 | passed |
| Task非混入 | `task_id:` frontmatter 0件、`*task*`名ファイル0件。会社operations/tasksの正本なし | passed |
| 子workflowのroot相対path | 子`.github/workflows/instagram-insights-collector.yml`に配置。`PROJECT_DIR: .`から`backend/`、`infra/`を参照し、旧親repo path・`../`・絶対pathなし | passed |
| 秘密値非混入 | 高確度token/key/private-key regex 0件、機密名ファイル0件、token parameter defaultなし、workflowはGitHub secret参照のみ | passed |
| Backend unit test | 12/12 passed | passed |
| `sam validate --lint` | valid、exit code 0 | passed |
| `sam build` | Build Succeeded、exit code 0 | passed |

## 1. 親repo分離

親repoの`.gitignore`差分:

```diff
+# Managed as an independent Git repository.
+/workspaces/system_integration/projects/instagram-insights-collector/
```

このパターンは先頭・末尾とも`/`で固定された対象project専用パターンであり、`workspaces/`全体や他projectを除外しない。

確認結果:

```text
parent normal status target entries: 0
parent tracked project entries: 0
parent workflow filesystem entry: absent
parent workflow tracked entry: absent
git check-ignore source: .gitignore:50
```

親repoには対象外の既存未commit変更があるが、今回のQAでは変更・stageしていない。

## 2. 子repo境界

確認結果:

```text
repo root: .../workspaces/system_integration/projects/instagram-insights-collector
branch: main (No commits yet)
remote count: 0
commit count: 0
tracked/index entries: 0
Git object count: 0
symlink count: 0
nested .git count: 0
```

初回commit候補のtop-levelは次に限定されていた。

```text
.github
.gitignore
architecture.md
backend
infra
operations
qa
2026-09-07_architecture-pre-minimal-redesign.md
2026-09-07_backend-implementation-verification.md
2026-09-07_mvp-requirements.md
2026-09-08_backend-minimal-implementation-verification.md
```

`operations/`は当projectのPlatform実装証跡1件で、会社のTask正本ではない。既存のQA/実装証跡には追跡性のためCompany Task IDへの参照があるが、`task_id:` frontmatterを持つTaskファイル、人物profile、AGENTS、会社共通知識、他workspaceは存在しない。

子`.gitignore`は`.aws-sam/`、`__pycache__/`、Python test/cache、仮想環境、`.env*`、`samconfig*.toml`等を除外する。再検証後も`.aws-sam/`とPython bytecode cacheはignoredで、初回commit候補に入らない。

## 3. workflow契約

Ruby Psych ASTによるYAML構文解析は成功した。workflowは子repo内の`.github/workflows/instagram-insights-collector.yml`にあり、次を確認した。

- `PROJECT_DIR: .`
- SAM template: `$PROJECT_DIR/infra/template.yaml`
- Backend source existence check: `$PROJECT_DIR/backend/src`
- build output: `$PROJECT_DIR/.aws-sam/build`
- build済みtemplate: `$PROJECT_DIR/.aws-sam/build/template.yaml`
- 旧`workspaces/system_integration/projects/instagram-insights-collector`参照なし
- 親方向`../`、ローカル絶対path、会社ディレクトリ参照なし
- deployは`main`へのpushかつprotected `production` environment経由
- AWS認証はOIDC、Instagram tokenは`${{ secrets.INSTAGRAM_ACCESS_TOKEN }}`からのみ注入

判定: **passed**。

## 4. 秘密値確認

`.git/`、build/cacheを除く子repo内容を対象に、AWS access key、GitHub token、Instagram long-lived token形式、Slack token、private key headerの高確度patternを照合し、該当0件だった。さらに`.env*`、PEM/P12/PFX、SSH private key、credentials名ファイルは0件だった。

`InstagramAccessToken`はSAMの`NoEcho: true` parameterでdefaultなし。workflowではGitHub Environment secretの式だけを参照し、`set +x`でdeploy値を表示しない。remote・commit・Git objectも0件のため、Git履歴やremote URLへの秘密値混入もない。

ローカル環境には`gitleaks` executableがなかったため、gitleaks action自体は未実行である。workflowのvalidate jobには`gitleaks/gitleaks-action@v2`が設定済みであり、初回commit後のCIでも検査される。今回のfilesystem静的検査結果はpassedとする。

## 5. Backend unit test

実行コマンド:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend/src python3 -m unittest discover -s backend/tests -v
```

結果:

```text
Ran 12 tests in 0.001s
OK
```

**12 passed / 0 failed / 0 errors**。

## 6. SAM validate / build

環境: `SAM CLI, version 1.165.0`

実行コマンド:

```text
SAM_CLI_TELEMETRY=0 sam validate --lint --template-file infra/template.yaml
SAM_CLI_TELEMETRY=0 sam build --template-file infra/template.yaml
```

結果:

```text
infra/template.yaml is a valid SAM Template
Build Succeeded
Built Artifacts: .aws-sam/build
Built Template: .aws-sam/build/template.yaml
```

buildは`infra/template.yaml`の`CodeUri: ../backend/src/`を解決し、`.aws-sam/build/CollectorFunction/collector/handler.py`を生成した。これにより独立repo rootからのinfra/backend接続も確認できた。生成物は子`.gitignore`対象である。

SAM CLIはコマンド終了時にsandbox外の`~/.aws-sam/metadata.json`へ書き込めずPermissionError警告を出したが、validate/buildはいずれもexit code 0で本処理は成功した。製品・repo分離の不具合とは判定しない。

## 最終判定

指定された独立Gitリポジトリ化のAcceptance Criteriaはすべて満たされた。**QA passed**とし、佐伯 真琴へ提出する。

本QAではGitHub repo作成、remote追加、commit、push、秘密値設定、実装修正を行っていない。
