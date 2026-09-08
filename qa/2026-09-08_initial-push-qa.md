# Instagram Insights Collector 初回push独立QA証跡

## メタデータ

- 作成日: 2026-09-08
- 検証日時: 2026-09-08 20:13 JST
- QA担当: 藤井 七海（System Integration / QA）
- Company Task: `TASK-20260908-200849-1CFB`
- 先行Task: `TASK-20260908-200305-C6F4`
- 対象repo: `/Users/kenshi/Documents/GitHub/chibinoppo/workspaces/system_integration/projects/instagram-insights-collector`
- 指定remote: `https://github.com/eruminaEGG/instagram_analyzer.git`
- 指定commit: `3ea8295`
- 総合判定: **passed**

対象repoの実装、Git index、commit、branch、tag、remote、追跡設定には変更を加えていない。QA成果物として本ファイルのみを追加した。

## Acceptance Criteria結果

| 確認項目 | 実測 | 判定 |
| --- | --- | --- |
| `origin` URL | fetch/pushとも指定URLと完全一致。remote名は`origin`の1件のみ | passed |
| local `main` / local `origin/main` | ともに`3ea8295c1d83f99d609a2d5c00c20962362f0b69` | passed |
| GitHub上の`origin/main` | `git ls-remote --exit-code origin refs/heads/main`が同一完全hashを返した | passed |
| 追跡状態 | `main`のupstreamは`origin/main`、ahead/behindは`0/0` | passed |
| 作業ツリー | 本QAレポート作成前の`git status --porcelain=v1`は出力0件 | passed |
| force push不使用の記録 | 先行Taskは通常pushを記録し、force系optionを禁止。remote追跡reflogは初回の`update by push` 1件 | passed（記録確認） |
| 秘密情報 | 高確度credential/private-key pattern 0件、機密名ファイル0件、literal secretなし | passed |
| 生成物 | cache、build、仮想環境、`.aws-sam`等の収録0件 | passed |
| 会社全体ファイル | 会社共通・人物・他workspace・Company Task正本0件 | passed |

## 1. remote、commit、追跡、clean状態

repo rootは指定対象自身だった。確認結果:

```text
remote count: 1 (origin)
origin fetch URL: https://github.com/eruminaEGG/instagram_analyzer.git
origin push URL:  https://github.com/eruminaEGG/instagram_analyzer.git
HEAD:             3ea8295c1d83f99d609a2d5c00c20962362f0b69
main:             3ea8295c1d83f99d609a2d5c00c20962362f0b69
origin/main:      3ea8295c1d83f99d609a2d5c00c20962362f0b69
upstream:         origin/main
ahead / behind:   0 / 0
commit count:     1
status before QA report: clean
```

branch設定も次のとおり一致した。

```text
branch.main.remote = origin
branch.main.merge  = refs/heads/main
```

`git status --short --branch`は`## main...origin/main`のみ、`git status --porcelain=v1`は空だった。本レポート作成後に発生する未追跡差分はQA成果物そのものなので、初回push直後のclean判定とは分けて扱う。

## 2. GitHub上の到達性

読み取り専用のremote照会を行った。

```text
$ git ls-remote --exit-code origin refs/heads/main
3ea8295c1d83f99d609a2d5c00c20962362f0b69 refs/heads/main
exit code: 0
```

これにより、検証時点で指定GitHub remoteへ到達でき、GitHub側`refs/heads/main`が指定commitを指すことを確認した。Web検索インデックスおよびページキャッシュからは新規repoページを取得できなかったため、ブラウザ表示や可視性設定までは判定対象外とする。Git remote transportによるbranch到達性はpassed。

## 3. force push不使用の記録確認

先行Task `TASK-20260908-200305-C6F4`には次が記録されている。

- Scope: `main`を通常pushする
- Constraints: `--force`および`--force-with-lease`は禁止
- Result: 初回commit `3ea8295`を通常pushし、force pushは使用していない
- Verification: push前のGitHub側にHEAD、branch、tagが存在しなかった

local remote-tracking reflogは`2026-09-08T20:06:26+09:00`の`update by push` 1件だけで、対象は`3ea8295`だった。現在のlive remoteも同じhashである。

Git metadataから実行時の全CLI argvを事後復元することはできないため、「force optionの不使用」は先行Taskの操作記録に対するQAである。ただし、push前にremote refなし、単一root commit、push後のlocal/remote一致という状態証拠とも矛盾しない。要求された「不使用の記録」の確認としてpassedとする。

## 4. commit収録範囲

`3ea8295`はmessage `Initial commit`のroot commitで、23ファイル、2,147行追加だった。tree entryは全件regular file（mode `100644`）で、symlinkおよびsubmodule/gitlinkは0件だった。

top-levelは次に限定されている。

```text
.github/
.gitignore
backend/
infra/
operations/
qa/
architecture.md
2026-09-07_architecture-pre-minimal-redesign.md
2026-09-07_backend-implementation-verification.md
2026-09-07_mvp-requirements.md
2026-09-08_backend-minimal-implementation-verification.md
```

`departments/`、`knowledge/`、`workspaces/`、`members/`、`.company/`、人物`profile.md`、`AGENTS.md`、`task_id:` frontmatterを持つCompany Task正本は0件だった。`operations/2026-09-08_platform-minimal-implementation.md`は当project固有の実装証跡であり、会社全体のoperations/tasks正本ではない。既存設計・QA文書中のTask ID参照は追跡情報で、Company Taskファイルの混入とは判定しない。

## 5. 秘密情報・生成物

commit treeを対象に、AWS access key、GitHub token、Instagram/Meta token、Slack token、private-key headerの高確度patternをファイル名のみ返す方式で検査し、該当0件だった。さらに次を確認した。

- `.env*`、credentials、PEM/P12/PFX、SSH private key等の機密名ファイル0件
- `InstagramAccessToken`はSAM parameterの`NoEcho: true`でdefaultなし
- workflowは`${{ secrets.INSTAGRAM_ACCESS_TOKEN }}`というGitHub secret参照のみで、literal値なし
- remote URLにuser info、token、query parameterなし
- ローカルに`gitleaks` executableは未導入

workflowのvalidate jobには`gitleaks/gitleaks-action@v2`が含まれるが、本QAではローカルaction実行結果を根拠にしていない。高確度静的検査と構成確認の結果はpassed。

生成物patternとして`.aws-sam/`、`__pycache__/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`node_modules/`、coverage、`dist/`、`build/`、仮想環境、`.pyc/.pyo`、`.DS_Store`を照合し、commit収録0件だった。これらは子repoの`.gitignore`でも除外されている。

## 最終判定

指定されたorigin、local/live remote commit一致、追跡状態、初回push直後のclean状態、force push不使用の記録、commit収録範囲、秘密情報・生成物の非混入を確認した。**QA passed**とし、佐伯 真琴へ提出する。

本QAではGit fetch、commit、push、force push、remote変更、index変更、実装修正を行っていない。
