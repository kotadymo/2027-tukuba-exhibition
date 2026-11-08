# 動体展示用 AI 物体検出・アラートシステム 仕様書

- **仕様バージョン:** 0.5.0
- **更新日:** 2026-11-08
- **対象リポジトリ:** [kotadymo/2027-tukuba-exhibition](https://github.com/kotadymo/2027-tukuba-exhibition)
- **状態:** 実装用仕様（Insta360 GO Ultra の接続部分は実機検証が必要）

## 1. 目的と確定事項

展示用 Web アプリケーションとして、**Insta360 GO Ultra のライブ映像**または**アップロードした動画ファイル**を FastAPI で受信・処理し、CPU 上のカスタム YOLO 物体検出モデルで検出する。

React の画面に映像と Bounding Box を表示し、設定された複数の物体クラスが同一フレーム内に存在したとき警告を表示・発音する。**アラートが発報した元フレーム画像と、そのフレームの検出情報を SQLite に記録**し、履歴から再表示できるようにする。

### 1.1 採用する構成

| 項目 | 仕様 |
| --- | --- |
| Backend | Python / FastAPI |
| Frontend | React / Vite（既存リポジトリの JavaScript / JSX 構成を維持） |
| DB | SQLite / SQLAlchemy |
| ライブ映像入力 | Insta360 GO Ultra（Wi-Fi、非公式 Python ツールキットを利用する方針） |
| 動画ファイル入力 | Frontend からアップロードする MP4（H.264 推奨） |
| 入力ソース | `insta360` と `video` の **2 種類を排他的に切り替え** |
| モデル | Ultralytics 互換の物体検出用カスタム `.pt`、Frontend から登録／切り替え |
| 推論 | 展示 PC の **CPU のみ**、1 モデル・1 映像ソース |
| ルール | 同一推論フレーム内に選択した全クラスが存在する（AND） |
| 警告 | React 画面上の警告＋ブラウザでの PC 警告音 |
| アラート保存 | **SQLite の JPEG BLOB＋検出データ＋発報メタデータ** |
| 通信 | REST API（設定・履歴）／WebSocket（映像＋検出＋イベント） |
| 実行 | Backend と Frontend を同じ展示 PC で起動、通常利用はローカルで完結 |

### 1.2 廃止する機能・前提

- **Raspberry Pi／reCamera カメラへの接続は廃止。**
- カメラ入力における RTSP 接続設定、RTSP 接続 API、RTSP 再接続処理、カメラアダプターは**初期実装対象外**。
- Raspberry Pi はカメラ中継機としても**必須としない**。展示 PC を GO Ultra の Wi-Fi に直接接続する構成を基本とする。
- 既存の**動画アップロード・再生モードは維持**する。

デモ専用のため、ログイン／権限管理、API 認証、アクセス制限、監査ログ、モデルのハッシュ検証等は実装しない。モデルは展示担当者が出所を把握しているファイルを使用する。`.pt` のロードには任意コード実行の危険があり得るため、信頼できるモデルのみを使用する運用とする。

## 2. 全体アーキテクチャ

~~~text
[Insta360 GO Ultra]          [アップロード MP4]
  │ カメラの Wi-Fi                │ REST アップロード
  │ 独自 TCP + H.265             ▼
  ▼                         data/videos/
  [非公式 SDK アダプター]         │
  [FFmpeg H.265 デコード]       [動画デコード]
              └──────────┬─────────┘
                         ▼
                  SourceService
             （入力モード切り替え）
                         │ 最新フレーム
                         ▼
                InferenceService
               カスタム YOLO / CPU
                         │ 同一フレームの検出結果
                         ▼
                    RuleEngine
                AND / 継続 / 解除
                         │ アラート発報イベント
                         ▼
                   AlertService
                JPEG + 検出情報を
                   SQLite に保存
                         │
     ┌───────────────────┴──────────────────┐
     ▼                                      ▼
  WebSocket                              REST API
  画像＋ボックス＋警告                   設定／履歴／画像
     └───────────────────┬──────────────────┘
                         ▼
                    React Frontend
            ライブ表示／動画再生／ルール／
            モデル管理／アラート履歴
~~~

YOLO、ルール判定、履歴保存は入力ソースに依存しない共通サービスとする。入力ソース固有の接続・デコード処理は独立モジュールに隔離する。

## 3. Insta360 GO Ultra ライブ入力

### 3.1 非公式ライブラリの候補と前提

利用候補は次の公開リポジトリとする。

- **[Daiki-Iijima/insta360-go-ultra-sdk](https://github.com/Daiki-Iijima/insta360-go-ultra-sdk)**
- 参考資料: [README](https://github.com/Daiki-Iijima/insta360-go-ultra-sdk/blob/main/README.md) / [通信仕様](https://github.com/Daiki-Iijima/insta360-go-ultra-sdk/blob/main/docs/protocol.md)

同リポジトリの説明では、GO Ultra の Wi-Fi アクセスポイントに接続し、**TCP 6666 番ポートの独自プロトコル**で映像を取得する。映像は **H.265 / HEVC Annex B** 形式で、Python スクリプトが動画バイト列を標準出力へ流せる。これは **RTSP ではない**。

次は**候補 SDK に基づく設計値**であり、実機動作の保証ではない。

| 項目 | 初期方針 |
| --- | --- |
| カメラ側ネットワーク | GO Ultra が提供する Wi-Fi に展示 PC が接続 |
| カメラ IP | `192.168.42.1`（SDK 記載の例、実機で確認） |
| 制御・映像 | TCP `6666` / 独自 Protocol Buffers |
| 映像形式 | H.265 / HEVC |
| デコード | FFmpeg を介して動画フレームを取得 |
| 実装方法 | 非公式 SDK の映像受信処理を `Insta360Source` にラップする |

**使用する非公式リポジトリの最終指定、利用許諾、GO Ultra 実機ファームウェア／Windows 対応は要確認。** SDK の README にある動作確認は macOS／Raspberry Pi OS と特定ファームウェアであり、Windows での動作を確認できたわけではない。互換性がない場合は同じ抽象インターフェースを満たす別アダプターへ差し替え可能にする。

### 3.2 接続フロー

1. 展示 PC で GO Ultra の Wi-Fi に接続する（OS 側の接続操作）。
2. Frontend の入力ソースを「Insta360 GO Ultra」に切り替える。
3. 「接続」ボタンで Backend から非公式 SDK アダプターを開始する。
4. Backend がカメラの制御接続を確立し、ライブプレビューを開始する。
5. 受信した H.265 ストリームを FFmpeg でデコードしてフレーム化する。
6. SourceService が最新フレームを YOLO 推論へ渡す。
7. Frontend に JPEG フレームと Bounding Box を送信する。

カメラ Wi-Fi への切り替えは**基本的に OS 側で行い、Frontend からの Wi-Fi SSID 設定や自動参加は初期版では提供しない**。カメラ Wi-Fi 接続中は PC のインターネット接続がなくなる場合があるが、Frontend と Backend は `localhost` で動作できるようにする。

### 3.3 接続状態と操作

Frontend に以下を表示する。

- 接続状態: `disconnected` / `connecting` / `connected` / `error`。
- 動画プレビューの表示、推論 FPS、最終フレーム受信時刻。
- 「接続」「切断」ボタン。
- エラー内容（Wi-Fi 未接続、接続失敗、ストリーム停止、デコード失敗等）。

切断時は一定間隔（初期値 3 秒）で再接続を試行する。ユーザーが明示的に「切断」した場合は自動再接続しない。切断を検知した際は最新検出状態・ルール判定状態をクリアし、前の映像を正常動作中のように表示しない。

### 3.4 フレーム処理

- ライブ入力と推論は非同期に動作させる。
- 推論待ちキューは最大 1 フレーム、CPU が追いつかないときは古いフレームを捨てる。
- **推論したフレームと、そのフレームで検出した Bounding Box を同期して送る。** 初期版では表示 FPS は推論 FPS に準ずる。
- カメラのタイムスタンプを取得・復元できない場合、Backend の受信時刻／単調時計を使う。タイムスタンプの出典は保持し、異なる時計系を混ぜない。
- エラーや接続再試行は展示 UI に反映する。
- 初期版ではカメラ内録画・撮影コマンド・カメラ内ファイル一覧・バッテリー制御等は使用しない。

## 4. アップロード動画入力（維持）

### 4.1 登録・管理

- Frontend から MP4 動画をアップロードし、Backend の `data/videos/` に保存する。
- 初期対応は MP4（H.264 推奨）。デコード不能な動画はエラー表示。
- ファイルサイズ上限の初期値は 1 GiB。
- 登録時に OpenCV / FFmpeg で解像度、FPS、長さ等を検査する。
- 動画一覧から選択・削除できる。再生中の動画は削除できない。
- アップロード直後は選択状態とし、**再生開始はユーザー操作**とする。

### 4.2 再生操作

- 再生／一時停止／停止／先頭から再生。
- ループ再生は初期値 ON。OFF のときは末尾で停止。
- 再生状態、動画名、動画内時刻・総時間を表示。
- 元動画音声、シーク、再生速度変更は初期スコープ外（アラート音声のみ再生）。

### 4.3 推論との同期

- Backend が動画をデコードし、動画のフレームを**共通の InferenceService / RuleEngine** へ渡す。
- 元動画を Frontend だけで再生してボックスを重ねる構成は採用しない。
- 動画内時刻を基準に原則 1 倍速相当を目指す。CPU が間に合わなければフレームを間引き、遅延蓄積を防ぐ。
- 一時停止時は最後のフレーム／ボックスを残し、新規アラートを発報しない。
- 再開・停止・先頭から再生・ループ先頭・入力切り替え時に、ルールの継続判定状態とクールダウンをリセットする。
- すでにアラートとして**SQLite に保存したフレームは停止・ループ後も残す**。

## 5. モデル登録・CPU 推論

### 5.1 モデル管理

Frontend から Ultralytics 互換のカスタム物体検出 `.pt` ファイルをアップロードする。

- `data/models/` に保存、初期サイズ上限 512 MiB。
- Ultralytics でロードし、物体検出（detect）タスク、クラス一覧、CPU ダミー推論を確認。
- 複数モデル登録・一覧・詳細表示・未使用モデル削除に対応。
- 使用するモデルは常に 1 つ。アップロードと有効化は別操作。
- モデル切り替え時は推論を一時停止し、成功時にモデル・クラス一覧を切り替える。失敗時は旧モデルを維持する。
- 再起動時は最後に有効だったモデルを復元する。
- モデルに存在しないクラスを用いたルールは `incompatible` として判定しない（ルールは保存したまま）。

### 5.2 CPU 推論の設定

| 項目 | 初期値 |
| --- | --- |
| デバイス | `cpu` 固定 |
| Confidence | `0.40` |
| NMS IoU Threshold | `0.70` |
| `imgsz` | `416` |
| 目標推論 FPS | `5`（保証値ではない） |
| バッチサイズ | `1` |

推論結果には `frame_id`、ソース種別、推論時刻、`class_id`、`class_name`、`confidence`、`bbox=[x1,y1,x2,y2]` を含める。Bounding Box は**表示・保存する元フレーム JPEG のピクセル座標**とする。動画入力なら `video_id` と `media_time_ms` を付加する。

CPU 性能やカスタムモデルの規模によって実測 FPS は変わる。展示 PC と使用する実モデルで調整する。

## 6. アラートルールと判定

### 6.1 登録

- 1 ルールにつき **異なるクラス名 2 種類以上**を選択する。
- 同一推論フレームにすべてのクラス名が存在したら AND 条件成立。
- ルール間は独立（OR）。同フレームで複数ルールが成立してもよい。
- ルール名、対象クラス、有効／無効、成立継続時間、解除時間、クールダウンを設定可能。
- **接触・距離・位置関係・人物との対応付け・個数条件は判定しない**。
- クラス名はモデルの ID ではなく文字列で保存し、モデル切り替え時に互換性を確認する。

例: `hand + drill`、`hand + glove`、`person + helmet + glove`。

### 6.2 状態遷移

各ルールを `idle` / `pending` / `active` として管理する。

1. 推論フレームから Confidence を満たしたクラス集合を作る。
2. ルールの全クラスがそろったら成立候補。
3. **連続 2 推論フレーム以上**かつ**成立開始から 0.5 秒以上**継続したら **1 回**発報。
4. `active` 中は同じルールを毎フレーム発報しない。
5. 条件が **2.0 秒連続で不成立**なら解除。
6. 最終発報時から **10 秒**のクールダウン中は再発報しない。
7. ライブ切断、推論停止、動画停止・一時停止・再開・ループ、モデル／入力切り替え時は状態をクリア。
8. フレームが 2 秒以上届かない場合は成立継続とみなさない。

上記時間は初期値で、ルールごとに変更できる。ライブ入力の判定時間は受信フレームの単調時計、動画入力は動画内時刻を使う。アラートの**発報日時**はどちらの場合も Backend の実時刻で記録する。

### 6.3 発報時のアクション

- React に警告バナー、ルール名、発報時刻を表示。
- 対象クラスの Bounding Box を強調表示。
- ブラウザで警告音を再生（音声有効化ボタン・ON/OFF・テスト発報に対応）。
- **発報を引き起こした推論フレームとその全検出結果を SQLite に記録**。
- `active` の継続中には重複記録しない。

## 7. アラートフレーム・検出情報の SQLite 保存（新規要件）

### 7.1 保存対象・タイミング

**保存対象はアラートが新たに発報したフレームのみ**。全映像フレームや毎回の推論結果は保存しない。

アラート判定が `pending → active` になった**当該推論フレーム**について、1 回の保存トランザクション内で以下を記録する。

- フレーム画像（Bounding Box 等を描画する前の**元画像**を JPEG 圧縮し、SQLite BLOB に格納）。
- 画像幅・高さ、フレーム ID、取得／発報日時、入力モード、使用モデル。
- **そのフレームで YOLO が検出したすべての物体**のクラス ID・クラス名・Confidence・Bounding Box。ルールに関係しない物体も含める。
- 発報したルール ID・ルール名スナップショット・発報日時。
- アップロード動画の場合は `video_id` と `media_time_ms`（再生位置）。
- ライブの場合は入力元が GO Ultra であること。

**同一フレームで複数ルールが発報した場合、フレーム画像と検出物体は 1 セットだけ保存**し、複数の `alert_events` から同じ `alert_frames.id` を参照する。入力セッション ID とフレーム ID を組み合わせて重複を避ける。

初期画質は **JPEG Quality 80**。画質は後から変更可能とする。画像 BLOB、検出レコード、アラートイベントを同一 DB トランザクションで確定し、一部だけが保存されないようにする。

### 7.2 履歴画面

- 新しい順に日時、ルール名、入力種別、モデル名を表示する。
- イベント詳細画面で**保存した元 JPEG を読み出し、保存した検出ボックス・クラス名・Confidence を描画**する。
- 発報対象のクラス／ルールに該当するボックスを区別して強調する。
- 複数ルールが同じフレームで発報した場合は、各イベントから共通フレームを参照する。
- 履歴画像はライブ配信とは別の REST API で取得できる。
- 保存後に YOLO モデルやルールが変更・削除されても、記録時の検出情報と発報ルール名を表示できる。
- アラート発報画像をダウンロードできるようにする（元 JPEG）。

### 7.3 保存容量・削除

- 動画全体や常時録画は保存しないため、イベント数に応じて SQLite 容量が増える。
- 初期版は**自動削除を行わない**。履歴画面に「履歴を全件削除」操作を設ける（確認操作後にイベント・検出・フレームをまとめて削除）。
- 削除時は他イベントから参照されている共通フレームを誤って削除しない。
- 保存失敗時も映像表示・推論は継続し、画面とログに保存エラーを表示する。

## 8. Frontend 画面仕様

1920×1080 の展示 PC 用モニターを基準とする。全画面表示に対応する。

### 8.1 ダッシュボード

- **入力ソース切替:** 「Insta360 GO Ultra（ライブ）」／「アップロード動画」。
- **ライブ操作:** 接続／切断、接続状態。
- **動画操作:** MP4 アップロード、動画一覧・選択・削除、再生／一時停止／停止／先頭から再生、ループ ON/OFF、再生位置。
- **映像表示:** 推論対象フレームの JPEG、Bounding Box、クラス名、Confidence、ルール成立対象の強調。
- **ルール:** 一覧、追加、編集、削除、有効／無効、モデル不一致。
- **警告:** 発報中ルールの大きな表示、警告音 ON/OFF、有効化、テスト警告。
- **状態:** 接続状態、使用モデル、実測推論 FPS／処理時間。
- **履歴:** 直近のイベント表示、履歴一覧・詳細への移動。

初期版では React の Canvas を使い、画像とボックスを同じ画像座標系で描画する。画面サイズを変更しても縦横比を保ち、ボックス座標を適切にスケールする。

### 8.2 モデル管理

- カスタム `.pt` アップロード、検証結果表示。
- モデル一覧、クラス一覧、アクティブモデル切り替え、未使用モデル削除。

### 8.3 設定

- GO Ultra 接続先 IP／ポート（初期値は SDK の記載値を参考）。
- Confidence、IoU、`imgsz`、推論 FPS。
- Bounding Box／クラス名／Confidence 表示 ON/OFF。
- 警告音 ON/OFF。
- JPEG 保存品質（初期値 80）。

### 8.4 アラート履歴

- イベント一覧、イベント詳細、画像と検出ボックスの再描画。
- JPEG ダウンロード。
- 確認付きの履歴全件削除。

## 9. REST API と WebSocket

### 9.1 REST API

すべての REST パスは `/api` を使用する。

| Method | Endpoint | 機能 |
| --- | --- | --- |
| GET | `/api/health` | Backend 状態 |
| GET | `/api/source` | 入力ソース種別・状態 |
| PATCH | `/api/source` | `insta360` / `video` 切り替え |
| GET | `/api/insta360` | GO Ultra 接続設定・状態 |
| PATCH | `/api/insta360` | IP・ポート等の接続設定 |
| POST | `/api/insta360/connect` | GO Ultra 接続・プレビュー開始 |
| POST | `/api/insta360/disconnect` | 明示的切断 |
| GET | `/api/videos` | 登録済み動画一覧 |
| POST | `/api/videos` | MP4 アップロード（multipart） |
| GET | `/api/videos/{id}` | 動画メタデータ |
| POST | `/api/videos/{id}/activate` | 動画入力へ切り替え／選択 |
| DELETE | `/api/videos/{id}` | 未使用動画の削除 |
| GET | `/api/videos/playback` | 動画再生状態・位置 |
| POST | `/api/videos/playback` | 再生操作 `play` / `pause` / `restart` / `stop` |
| PATCH | `/api/videos/playback` | ループ設定 |
| GET | `/api/models` | モデル一覧 |
| POST | `/api/models` | `.pt` アップロード（multipart） |
| GET | `/api/models/{id}` | モデル詳細・クラス |
| POST | `/api/models/{id}/activate` | 使用モデル切り替え |
| DELETE | `/api/models/{id}` | 未使用モデル削除 |
| GET | `/api/models/active` | 有効モデル |
| GET | `/api/classes` | 有効モデルのクラス一覧 |
| GET | `/api/rules` | ルール一覧 |
| POST | `/api/rules` | ルール登録 |
| PUT | `/api/rules/{id}` | ルール編集 |
| DELETE | `/api/rules/{id}` | ルール削除 |
| GET | `/api/alerts` | アラート履歴（新しい順、limit/offset） |
| GET | `/api/alerts/{id}` | イベント詳細＋保存済み検出情報＋画像の参照先 |
| GET | `/api/alerts/{id}/frame` | 当該イベントの元 JPEG（image/jpeg） |
| DELETE | `/api/alerts` | 全イベント／未参照フレーム・検出情報の削除 |
| POST | `/api/alerts/test` | テスト警告（保存せず、区別して通知） |
| GET | `/api/settings` | 共通設定取得 |
| PATCH | `/api/settings` | 共通設定変更 |

REST は JSON を返し、画像取得のみ `image/jpeg` とする。存在しない ID は `404`、使用中リソース削除は `409`、入力不正は `422`。モデル・動画のファイルを送る API は `multipart/form-data` とする。

### 9.2 WebSocket: `/ws/live`

**バイナリのフレームメッセージ**は、`uint32_le(header_length)` + `UTF-8 JSON header` + `JPEG bytes` とする。

ヘッダーには以下を含める。

- `type="frame"`、`frame_id`、`source_session_id`、`timestamp`、`source_type`。
- `width`、`height`、`detections`（クラス ID／名、Confidence、ピクセル座標 bbox）。
- `matched_rule_ids`（現フレームで条件が成立しているルール）。
- 動画入力時のみ `video_id`、`media_time_ms`。

**テキスト JSON イベント:** `alert_started` / `alert_cleared` / `insta360_status` / `video_status` / `source_changed` / `model_changed` / `alert_save_error` / `error`。

`alert_started` は `alert_id`、`rule_id`、`frame_id`、`source_session_id`、`timestamp` を持つ。履歴画像の取得は REST とし、**WebSocket 上で画像を再送保存する方式にはしない**。WebSocket 切断時は自動再接続し、REST で状態と最新履歴を再取得する。

### 9.3 フレームメッセージの例（ヘッダーのみ）

~~~json
{
  "type": "frame",
  "frame_id": 1234,
  "source_session_id": "session-20261108-01",
  "timestamp": "2026-11-08T16:21:00+09:00",
  "source_type": "insta360",
  "width": 640,
  "height": 360,
  "detections": [
    {
      "class_id": 0,
      "class_name": "hand",
      "confidence": 0.94,
      "bbox": [42, 51, 178, 224]
    }
  ],
  "matched_rule_ids": [1]
}
~~~

## 10. SQLite スキーマ

SQLite のファイルは `data/app.db` とする。モデル・アップロード動画はファイルシステムに保存し、**アラート発報画像は SQLite 本体に保存**する。

| テーブル | 主なカラム | 用途 |
| --- | --- | --- |
| `models` | `id, name, original_filename, stored_filename, file_size, created_at` | モデル管理 |
| `model_classes` | `id, model_id, class_id, class_name` | モデルのクラス |
| `model_settings` | `id, active_model_id, confidence, iou_threshold, imgsz, target_fps` | 推論設定 |
| `source_settings` | `id, source_type, active_video_id, loop_enabled` | 入力ソース選択 |
| `insta360_settings` | `id, camera_ip, tcp_port, reconnect_interval_ms` | GO Ultra 接続設定 |
| `videos` | `id, name, original_filename, stored_filename, file_size, width, height, fps, duration_ms, created_at` | アップロード動画 |
| `alert_rules` | `id, name, enabled, detection_duration_ms, release_duration_ms, cooldown_ms, created_at, updated_at` | ルール |
| `alert_rule_classes` | `id, rule_id, class_name` | ルール対象クラス |
| **`alert_frames`** | **`id, source_session_id, frame_id, model_id, source_type, video_id, media_time_ms, captured_at, width, height, image_jpeg BLOB`** | **発報フレームそのもの** |
| **`alert_frame_detections`** | **`id, alert_frame_id, class_id, class_name, confidence, x1, y1, x2, y2`** | **発報フレーム内の全検出物体** |
| `alert_events` | `id, alert_frame_id, rule_id, model_id, rule_name_snapshot, matched_class_names_json, event_type, created_at` | 発報ルールと保存フレームの対応 |
| `app_settings` | `key, value_json` | 音声・画像保存品質等 |

### 10.1 保存・参照に関する規則

- `alert_frames` に **(source_session_id, frame_id)** の一意制約を設け、同一フレームの重複 BLOB 保存を防止する。
- `alert_frame_detections.alert_frame_id` は `alert_frames.id` を参照する。
- `alert_events.alert_frame_id` は `alert_frames.id` を参照する（テスト警告はイベント保存対象外）。
- 同一フレームで複数ルールが発報した場合は 1 件の `alert_frames` と複数 `alert_events` を作る。
- クラス名・座標・Confidence・ルール名は**発報時のスナップショット値**を残す。
- モデル／動画が削除されてもアラート履歴は保持できるよう、参照 ID が欠損しても履歴表示が壊れない設計とする。
- `image_jpeg` は元フレームの JPEG バイト列そのものとする。元の動画全体は DB に保存しない。
- 書き込みを 1 トランザクションで処理し、SQLite は WAL モードを推奨する。
- 履歴削除では関連イベント、フレーム、検出をまとめて削除する。

## 11. 非機能要件

### 11.1 性能

- CPU 推論の初期目標は **5 FPS**、`imgsz=416`、`batch=1`（実測により調整）。
- H.265 デコードと YOLO 推論を連続処理できるよう、受信・デコード・推論・配信を分離する。
- 処理が追いつかないときは古いフレームを間引き、表示とボックスの**同期を優先**する。
- スナップショット JPEG 圧縮と SQLite 保存がライブ表示を長時間ブロックしないよう、保存をバックグラウンドワーカーに委譲してよい（ただし保存完了前に結果を確定扱いしない）。
- JPEG BLOB のため SQLite ファイルが増大する。展示前に容量を確認する。

### 11.2 安定性

- GO Ultra 切断時の再接続、動画末尾／破損ファイルの処理、WebSocket 自動再接続。
- モデル切り替え失敗時の旧モデル維持。
- DB 保存失敗は明示し、推論を継続する。
- 設定・モデル・動画一覧・ルール・履歴は Backend 再起動後も維持する。
- 実機で **30 分以上**の連続動作試験を行う。

### 11.3 初期版の対象外

Raspberry Pi／reCamera／RTSP 入力、複数カメラ、複数モデル同時推論、GPU 推論、物体追跡、物体同士の接触／距離／位置関係、ROI、常時録画、アラート前後の動画クリップ保存、動画再生位置シーク、元動画の音声再生、カメラ内録画操作、外部ブザー、クラウド通知、ログイン／認証は対象外。

## 12. リポジトリ構成案

既存の `backend/main.py`、`backend/requirements.txt`、`frontend/src/App.jsx`、`frontend/package.json` を維持し、機能ごとに追加する。

~~~text
2027-tukuba-exhibition/
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── app/
│       ├── api/
│       ├── sources/
│       │   ├── insta360_source.py  # 非公式 SDK アダプター
│       │   ├── video_source.py     # アップロード動画の再生
│       │   └── source_service.py   # 入力切り替え
│       ├── detection/
│       ├── models/
│       ├── rules/
│       ├── alerts/
│       └── database/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── App.jsx
│       ├── components/
│       ├── pages/
│       ├── hooks/
│       └── services/
├── data/
│   ├── models/
│   ├── videos/
│   └── app.db
├── docs/
│   └── specification.md
└── README.md
~~~

`data/` 配下のモデル、アップロード動画、SQLite DB は実行時データとして Git 管理対象外にする（実装時に `.gitignore` を確認）。

## 13. 開発フェーズ

### Phase 1: 共通 Backend と動画デモ

1. SQLite スキーマ、カスタム `.pt` モデル登録、CPU YOLO 推論。
2. MP4 アップロード／再生、最新フレームへの推論。
3. 同一フレームの Bounding Box を WebSocket 配信。
4. ルール判定、アラート発報、**画像 BLOB と全検出情報の保存**。

### Phase 2: Frontend とアラート履歴

1. React ダッシュボード、動画操作、モデル管理、ルール CRUD。
2. アラート警告バナー・ブラウザ音声。
3. 履歴一覧・詳細・保存画像へのボックス再描画・ダウンロード。
4. 入力切り替え UI と通信状態表示。

### Phase 3: Insta360 GO Ultra ライブ入力

1. 指定の非公式ライブラリを確定し、実機と展示 PC の対応状況を検証。
2. GO Ultra の Wi-Fi 接続・独自 TCP ストリーム受信。
3. FFmpeg による H.265 デコードと SourceService への接続。
4. ライブ映像への YOLO／アラート／SQLite フレーム保存を統合。
5. 切断復旧、CPU FPS、保存速度、**30 分以上**の連続展示試験。

## 14. 完了条件（受け入れ試験）

- [ ] Frontend から MP4 をアップロード・再生し、YOLO Bounding Box が同期表示される。
- [ ] 動画の再生／一時停止／停止／先頭から再生／ループが機能する。
- [ ] Frontend からカスタム YOLO `.pt` を登録・切り替えられ、CPU のみで推論する。
- [ ] ルールを追加・編集・無効化でき、同一フレーム AND 条件でアラート発報する。
- [ ] 警告バナーと PC の警告音が動作し、継続中の重複発報がない。
- [ ] **アラート発報フレームの JPEG が SQLite BLOB に格納される。**
- [ ] **そのフレームの全検出クラス・Confidence・Bounding Box が DB に記録される。**
- [ ] **同一フレームの複数発報で画像が重複保存されない。**
- [ ] **履歴画面から画像＋検出ボックスを正確に再表示できる。**
- [ ] 動画モードの履歴に再生位置が残る。
- [ ] Insta360 GO Ultra に接続し、H.265 をデコードしてライブ映像を取得できる。
- [ ] ライブ入力でも動画入力と同じモデル／ルール／履歴保存機能を使える。
- [ ] 入力切り替えや接続切断で古いボックス・判定が残らない。
- [ ] 設定・履歴が再起動後も復元される。
- [ ] 展示 PC 上で 30 分以上の連続動作ができる。

---

## 15. 実装前に確認する項目

1. **非公式ライブラリの指定:** 本書は `Daiki-Iijima/insta360-go-ultra-sdk` を採用候補として記載した。別ライブラリを想定している場合は GitHub URL を指定して差し替える。
2. **実機動作:** Insta360 GO Ultra のファームウェア、展示 PC の OS（特に Windows）、Wi-Fi 接続、FFmpeg デコード可否。実機試験で確定する。
3. **CPU 性能:** モデルファイルの容量、PC の CPU、H.265 デコード＋推論の FPS／遅延。
4. **保存容量:** JPEG 画像の発報頻度と画質に応じて SQLite の容量を計測する。

上記は実機確認を要する項目であり、**動画デモとアラート保存機能の実装を先行する際のブロッカーにはしない**。
