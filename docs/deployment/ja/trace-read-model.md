---
title: 原生 Trace と Session クエリの運用・復旧
type: reference
audience: [A4]
runs: no
verified_on: 2026-10-25
sources:
  - "SigNoz: pkg/ingest/projection_maintenance.go"
  - "SigNoz: pkg/modules/aivision/trace_window.go"
  - "ai-vision/aivision-py-be/apps/core/trace_query/signoz/managed_projection.py"
---

# 原生 Trace と Session クエリの運用・復旧

対象は自動 projection 保守を含む AI Vision / SigNoz の対応ソースビルドです。既存リリースのイメージに含まれるとは限りません。外部 Langfuse は従来の経路を使用します。raw Span が正本で、派生データは再構築できます。

## 2 つの緊急制御

| プロセス | 設定 | 既定値 | 動作 |
|---|---|---|---|
| AI Vision BE | `AIVISION_QUERY_MODE` | `auto` | 検証済み索引ページと完全な hybrid/raw スナップショットを自動選択。`raw` は Trace・canonical Session・候補値をまとめて raw 経路へ切り替えます。 |
| SigNoz | `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED` | `true` | `false` は共有の保守停止状態を永続化します。単に `true` に戻して再起動しても解除されず、明示的な復旧が必要です。 |

変更後は該当プロセスを再起動します。Docker、ソース構築、Kubernetes テンプレートは両設定を渡します。Session サマリーの一括取得、有限長プレビュー、Token 最適化は標準動作です。旧 `legacy` / `shadow` / `prefer` と SQL 戦略別スイッチは廃止しました。

設定の正本は [`deploy/`](../../../deploy/README.md) です。CLI vars と製品 Compose の `.env` では `BACKEND_QUERY_MODE` と `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` を優先し、表のアプリ環境変数へ対応付けます。同じ2制御の別名であり、追加スイッチではありません。正本を変更した後、`aivision-cli/` の `make assets` で埋め込みと Compose の生成物を更新し、元の設定・namespace・導入入口を再利用してください。

## 自動処理

| 項目 | 動作 |
|---|---|
| スキーマ | バージョン付き・直列化された移行で control/source/projection/coverage 表と STORED Token 列を管理します。 |
| Token 列 | `session_trace_tokens_v1` は `DECIMAL(65,0)`、`session_billable_v1` は `TINYINT`。`row_json` の書き込みごとに DB が計算します。任意の巨大整数の精度保証ではありません。 |
| 初回移行 | 信頼を確認できない過去の派生状態を再構築し、raw Span を保持します。削除処理は 256 行以下の短いトランザクションで commit し、raw 書き込みを挟めます。中断は OFF を保持して残りから再開します。 |
| 明示復旧の期限 | サーバー 4 分、クライアント 245 秒。既存の `timeout: 240` ヘッダーを自動送信し、主 API ミドルウェアにも同じ期限を適用。通常クエリの期限は変更しません。 |
| 復旧コマンド全体 | 上記は復旧 HTTP リクエスト 1 回の期限です。管理コマンドは有界の投影処理を 1 巡実行するため、全体はより長くなる場合があります。その後も監視対象ワーカーが追いつくまで処理を続けます。 |
| 通常再起動 | 現行バージョンでは coverage と履歴カーソルを保持します。 |
| 移行失敗 | 派生読み取りを不適格にして再試行。raw の表と接続が正常なら raw 取り込み・復旧検索は維持します。通常 API の期限と DB 全体設定は変更しません。 |
| ワーカー | 標準 BE エントリポイントが Gunicorn API と `maintain_trace_read_model` を監視。Space・Agent・有効データソースを自動発見し、履歴登録と更新を継続します。 |
| 設定変更 | 新規ルート、資格情報、価格コンテキストを再発見。revision CAS の競合は再試行します。 |
| 一括処理 | 有限サイズの snapshot/publication バッチ。各 Trace の revision 検証は維持します。 |

標準エントリポイントを使わないソース実行では、同じ BE 環境で `python manage.py maintain_trace_read_model` を別途監視します。`--once` は発見 1 回、`--limit` はルートごとの処理上限です。履歴登録完了は全投影の準備完了と同義ではありません。既存の `project_trace_read_model` は診断用に残ります。

## 検索と上限

Framework ダッシュボードは Agent の Trace ページと同じ canonical Trace / Session クエリを使用します。Session はサーバー側で 10 / 20 / 50 件ずつページ分割し、詳細でも同じグループを維持します。操作トレンドは後述の型付き SQL 集計を使用できます。一般メトリクスは `statistics-v1` 集計に完全な料金の根拠がない間、既存 summary JSON のバージョン付き呼び出し情報を使用します。識別子、親子関係、時刻、型、モデル/プロバイダー、正規化済み usage/cost を保持し、ネストしたモデル呼び出しの曖昧さと未取得 usage の扱いを raw クエリに揃えます。

統計専用ウィンドウの準備済みサマリーは本文と IO プレビューを転送せず、dirty / 未対応のデータは完全 raw 復旧を維持します。内容条件は完全な内容を読む経路を維持します。復旧経路で同一のストレージ資格情報・物理スコープ・固定期間を使う並行セクションは実行中の読み取りだけを共有し、完了結果はキャッシュしません。完了後は新しく読み取り、同じ固定期間の並行更新は実行中の読み取りに参加できます。イベントは最新 200 件以下の操作キーを取得し、必要な Trace の完全な source snapshot を 100 件以下のバッチで補完します。source revision と maintenance generation の両方が選択時と一致しなければ、全件を破棄し、完全 raw ウィンドウを 1 回読み直します。世代の証明がない旧 compact ウィンドウも分割読み取りには安全な raw 復旧を使用します。単一スナップショットの統計・リストや raw 読み取りに新たな派生状態依存は追加しません。

追加情報は既存の canonical/価格 projection context を保持し、補完中も準備済みの Trace / Session 索引を利用できます。対応データプレーンはワーカーの任意条件 `required_supplement` を受け付け、不足情報を有限バッチで補完します。この JSON 補完はテーブル移行や追加スイッチを必要とせず、後述の任意の型付き統計スキーマだけを別途追加移行します。ワーカー出力の `supplementSupported=false` は旧データプレーンを示し、通常の dirty 保守は継続しますが、更新前には過去の統計情報を積極的に補完しません。情報がないダッシュボードは完全 raw snapshot を使用し、認証・通信・不正データはエラーとして扱います。`AIVISION_QUERY_MODE=raw` は compact 統計にも適用され、既存の導入別名と 2 つの緊急制御を使用します。

同じ repeatable-read トランザクションで、信頼できるサマリーと dirty / 未投影 / 境界をまたぐ raw Span を取得します。Python が同一の canonical 規則で合成するため、1 件の dirty Trace が全件の個別取得を要求しません。`raw` は投影表に依存しません。復旧はサマリーのサイズを事前確認し、転送が応答予算を超える場合は同じトランザクションで完全な raw ウィンドウへ切り替えます。上限は変えず、raw 復旧も超過すれば明示的に失敗します。

| 経路 | 上限と確認 |
|---|---|
| 完全スナップショット | 50,000 Trace、500,000 raw Span、選択元フィールド 512 MiB、単一 Span 16 MiB、compact 応答 256 MiB。重複副本のみ除去し canonical IO と属性を保持、逐行で制限を確認。超過は明示的エラー。切り詰めた成功は返しません。 |
| 索引 Session ページ | 50 Session、5,000 選択 Trace、16 MiB。超過時は完全スナップショットへ。 |
| プレビュー | 各シリアライズ IO は最大 1,024 Unicode 文字。検索・詳細・export は完全な記録内容を使用。 |
| 旧データプレーン | snapshot 能力がなければ従来の有限な互換経路。 |
| 応答 | `meta.queryMode` は `read_model`、`hybrid_snapshot`、`raw_snapshot`。Token は `meta.sessionTokenQueryStrategy`、一括取得は `meta.sessionPageSummaryStrategy=batch`。 |

タスクユーザー、環境、タグ、内容は同一 Trace に一致させ、Session 集計は選択期間の全 turn を保持します。Token ≥0 も既知の billable usage を必要とし、未取得値をゼロと扱いません。生成列が欠落・不一致なら内部 JSON_TABLE 戦略を使用します。権限・通信・不正な成功応答はエラーとして表れます。

## OceanBase の統計集計

現在の `statistics-v1` は金額と件数だけを持ち、`costCoverage` の価格ソース、不明理由、サブスクリプションの区分、公開価格相当額を復元できません。一般メトリクスは応答を検証した後、完全なコンパクト統計ウィンドウへ戻ります。`framework.events.timeseries` は検証済みの型付き操作件数を引き続き使い、最近の操作ページはソースリビジョンに結び付けて内容を取得します。コンパクト情報は canonical な料金根拠を保持し、raw 復旧と同じリクエスト内の Space 価格スナップショットと投影コンテキストを使います。以前の型付きメトリクスの計測値は記録されたソースだけの結果であり、この価格互換統合の性能を示しません。

対応データプレーンは独立した型付き Trace / 操作テーブルで、通常の完全期間集計を SQL 実行します。新規表は明示的な行ストレージを既定とし、既存の hybrid / column レイアウトを自動変換しません。欠損と明示的な空文字のグループキーが共存する場合、canonical の並び順を保つ既存ウィンドウを使用します。件数、usage の累計、モデル/プロバイダー別グループ、時間バケットと、従来の正確な遅延計算に必要な上限付き数値配列を返します。この経路は本文・プレビュー・各 observation の JSON を転送しません。内容条件や未対応のフィルターは検証済みの完全ウィンドウを維持します。

任意スキーマは独立した追加移行リースを使い、基本投影の準備状態・世代・canonical/価格コンテキストを変えません。投影文書の STORED receipt は旧 publisher を含む各書き込みで再計算され、変更後の古い統計を利用できなくします。既存ワーカーが不足分を段階的に補完し、source revision と保守フェンスの下で Trace 全体の寄与を原子的に置換します。未対応の次元・数値や投影の転送上限を超える統計では通常サマリーと不適格 receipt を保持するため、同一文書を無限に再投影しません。自動経路は CE と Enterprise の OceanBase 4.4 以降を対象とし、旧版は既存の compact 復旧を維持します。今回の実 SQL / ストレージ検証は CE 4.4.2.1 で行います。完全性と Trace 集計の結合クエリ、有界の遅延読み取り、5 種類の usage / 操作統計を一括生成する `GROUPING SETS` の計 3 クエリを使用します。

dirty・古い revision・不足・期間境界をまたぐ Trace は従来の統計ウィンドウ/raw 復旧へ移ります。未取得 Token、ネストした課金の曖昧さ、既知小計、coverage、Decimal cost の意味を保持します。復旧は任意統計表も同じ有界チャンクとフェンスで清掃し、表のない旧環境も許容します。追加スイッチはなく、`AIVISION_QUERY_MODE=raw` と既存の保守 OFF を使用します。ストレージ比較とローカル検証は本番容量の保証と区別します。

4 フレームワークと 10k Session の同一データ結果、行/hybrid/column 比較、追加の投影書き込みコストと並行リストの競合は [OceanBase 集計の検証報告](../../testing/ob-ap-dashboard-aggregation-20261026.md)を参照してください。準備済みローカル環境の API 観測であり、本番容量やブラウザー描画の保証ではありません。

### seekdb の軽量な互換経路

チーム共有と増加する観測データには引き続き OceanBase CE を既定として推奨し、seekdb は個人利用やローカル開発向けの選択肢とします。識別可能な seekdb 1.x（1.4 以降）は、型付き行ストレージ表、段階的な保守、最新操作ページと有限な内容補完を再利用します。表は OceanBase の明示的な列グループ句を付けず通常の行ストレージ DDL で作成します。集計は明示的なグループ軸、`CASE`、`GROUP BY` を使い、`GROUPING SETS` 構文を必要としません。同じ canonical 契約と完全性・source revision・maintenance generation の検証を保持します。

クエリ形態は内部で選択し、ユーザー設定を追加しません。旧版・識別不能なエンジンや任意情報が利用できない場合は完全ウィンドウ復旧を維持します。[seekdb 互換検証報告](../../testing/seekdb-dashboard-compatibility-20261026.md)に実際の版・データ・コスト・制約を記録し、OceanBase の測定結果や既定の個人向けリソースでの容量保証と区別します。

### アップグレード時のデータベース変更

対応する SigNoz データバックエンドと AI Vision Python バックエンドを更新します。OceanBase 4.4+ と識別可能な seekdb 1.x（1.4 以降）では起動時に `<prefix>_trace_statistics`、`<prefix>_call_statistics`（時間索引を含む）を自動作成し、`<prefix>_trace_projections` に `statistics_receipt_v1` STORED 生成列を追加します。既存 DB アカウントには `CREATE`、`ALTER`、通常の読み書きと構造メタデータへのアクセス権限が必要です。通常の更新で手動 SQL や raw Span 表の書き換えは不要です。旧版・識別不能なエンジンは既存経路を維持し、この任意構造を作成しません。

通常ワーカーが過去の寄与を有限バッチで補完します。不足・不適格な情報は完全ウィンドウ復旧へ移り、既存 Trace / Session ページの基本準備状態を保持します。データのある表への STORED 列追加は IO・容量を消費し、DDL ロック待ちが起こり得るため、更新時間を確保してください。12 Trace の初期化検証は本番履歴の移行時間を保証しません。任意初期化の失敗は記録して再試行します。構造欠落・不一致は復旧経路を使えますが、メタデータ権限・接続・SQL の実エラーは表面化します。機能復旧は既存の raw 制御を使い、追加列や表の削除は不要です。

## 復旧

| 順序 | 操作 |
|---|---|
| 1 | BE を `BACKEND_QUERY_MODE=raw` で再起動。同じフィルターを維持し、完全 raw snapshot を使用します。遅くなる場合があります。 |
| 2 | 派生書き込みや構造に問題があれば、SigNoz を `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=false` で再起動。共有停止は他の既定 ON インスタンスにも適用されます。 |
| 3 | 原因修復後、すべての writer の `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` を `true` に戻します。復旧には SigNoz admin 資格情報と既存の内部 ingest key の両方が必要です。 |
| 4 | 運用端末へ元の非公開設定から `SIGNOZ_INGEST_INTERNAL_KEY` を export し、`docker exec --workdir /app --env SIGNOZ_INGEST_INTERNAL_KEY aivision-be /app/.venv/bin/python manage.py maintain_trace_read_model --resume-maintenance --once` を実行。`aiv status` または元の `aiv status --target compose --compose-file ... --compose-env-file ...` で実際の backend コンテナ名を確認し、key の値を引数・ログに入れません。通常ワーカーには key 不要です。 |
| 5 | 復旧は孤立した派生行を含めて再構築し、世代を更新。raw は保持します。同じ検索と件数・ゼロ閾値を確認後、同じ `BACKEND_QUERY_MODE` を `auto` に戻します。 |

SQL import、外部 retention、restore、表置換、直接 raw 更新の前に保守を停止し、終了後に明示復旧します。外部変更の自動検出は保証しません。機能復旧のために生成列を削除する必要はありません。CE / seekdb 間の変更は別デプロイと明示移行が必要です。

### Kubernetes の復旧プロセス

全 writer の `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` を `true` に戻し、元の context・namespace・vars ファイルで固定 CLI を再適用します。既存 Secret の承認された手順で単一行 key を取得し、運用端末だけへ export してください。下例の context と namespace を置き換え、key は一回限りの exec プロセスへ標準入力で渡します。値を引数・Pod 環境・履歴出力・ログへ書かず、通常プロセスや初期化 Job を再起動しません。

```sh
test -n "${SIGNOZ_INGEST_INTERNAL_KEY:-}" &&
printf '%s\n' "$SIGNOZ_INGEST_INTERNAL_KEY" |
  kubectl --context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
    exec -i deployment/aivision-be -c be -- /bin/sh -c \
    'IFS= read -r SIGNOZ_INGEST_INTERNAL_KEY || exit 1; export SIGNOZ_INGEST_INTERNAL_KEY; cd /app && exec /app/.venv/bin/python manage.py maintain_trace_read_model --resume-maintenance --once'
```

互換性のため Deployment / container 名は `aivision-be` / `be` のままです。key は復旧プロセスだけに入り、完全な結果を確認後、同じ `BACKEND_QUERY_MODE` を `auto` に戻して元の CLI を再適用します。実クラスター検証を実施したという意味ではありません。

検証は[同一データの報告](../../testing/standard-query-defaults-20261025.md)を参照。ローカル準備済み環境、実 DB、外部テスト transport と、完全認証・公開イメージ・Kubernetes 実行・本番適用は区別します。詳しい手順は[英語版](../en/trace-read-model.md)にもあります。
