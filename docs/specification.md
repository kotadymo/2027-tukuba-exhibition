# 動体展示用 AI 物体検出・アラートシステム 仕様書

- **仕様バージョン:** 0.3.0
- **作成日:** 2026-11-07
- **対象リポジトリ:** [kotadymo/2027-tukuba-exhibition](https://github.com/kotadymo/2027-tukuba-exhibition)
- **状態:** 初期実装向け確定仕様（性能値は実機計測後に調整）

## 1. 目的とスコープ

カメラ映像を FastAPI の Backend で受信し、アップロードされた学習済みカスタム YOLO モデル（`.pt`）で物体検出する。ユーザーが設定した**複数の物体クラスが同一フレーム内にすべて存在する**場合にアラートを発報し、React の Frontend に映像・検出ボックス・警告を表示する。

本番用システムのコア機能を展示向けに切り出すが、本仕様の優先順位は **デモとしての分かりやすさ、動作の安定性、実装の単純さ** とする。

### 1.1 確定済みの要件

| 項目 | 仕様 |
| --- | --- |
| Backend | Python / FastAPI |
| Frontend | React / Vite（**既存の JSX 構成を維持**） |
| データベース | SQLite |
| カメラ | Raspberry Pi または Seeed Studio reCamera |
| 映像入力 | RTSP を共通インターフェースとする |
| YOLO モデル | Ultralytics で読み込める、物体検出用のカスタム `.pt` |
| モデル登録 | Frontend からアップロード／一覧表示／有効化／削除 |
| 推論環境 | **CPU のみ**（CUDA/GPU は初期スコープ外） |
| アラート条件 | 同一の推論フレームに、選択されたすべてのクラスが出現（AND） |
| アラート手段 | ブラウザ内の警告表示＋展示 PC の音声 |
| 通信 | 設定は REST API、映像・検出結果・イベントは WebSocket |
| 配備 | Backend と Frontend を同じ展示 PC で実行。カメラと同じ LAN 内で接続 |
| インターネット | 推論・通常展示時は不要（依存パッケージ導入等は事前実施） |

### 1.2 デモ向けの簡素化

ログイン機能、権限管理、API 認証、アクセス制御、監査ログ、暗号化設定、モデルの SHA-256 管理等は**実装しない**。モデルファイルは展示担当者が用意したものを使用する。`.pt` の読み込みは任意コード実行の危険を伴い得るため、**出所が信頼できるファイルだけを登録する運用**とし、アップロード可否のチェックは互換性・不意のエラー防止を目的とする。

## 2. 全体構成

~~~text
Raspberry Pi Camera / reCamera
        │ RTSP（LAN）
        ▼
Backend: FastAPI
 ├─ CameraService        RTSP 接続・取得・再接続
 ├─ InferenceService     YOLO(.pt) / CPU 推論
 ├─ ModelService         .pt ファイルの保存・登録・切り替え
 ├─ RuleEngine           同一フレーム AND 判定・連続検出・解除
 ├─ AlertService         アラートイベント生成・履歴保存
 ├─ REST API             モデル／ルール／設定／履歴
 ├─ WebSocket            推論済みフレーム＋検出情報＋イベント
 └─ SQLite               設定・モデルメタデータ・ルール・履歴
        │ REST / WebSocket
        ▼
Frontend: React (JSX)
 ├─ ダッシュボード        カメラ映像＋Bounding Box＋警告
 ├─ ルール設定            クラスの組み合わせを選択
 ├─ モデル管理            .pt アップロード・切り替え
 └─ 設定                 カメラ URL・推論設定・警告音
~~~

Backend はカメラ映像を一元的に取得し、Frontend は RTSP へ直接接続しない。reCamera のオンデバイス AI 機能は使用せず、**推論は展示 PC の CPU で実行**する。

## 3. カメラ・映像取得

### 3.1 対応

- Raspberry Pi: カメラモジュールと RTSP 配信ソフトウェアを別途構成する。
- reCamera: 利用する機種・ファームウェアの RTSP 配信機能を使用する。
- Backend 側は機種名による分岐を原則設けず、共通の RTSP URL で接続する。
- 開発・テスト用にローカル動画ファイル入力にも対応する（展示時は RTSP を使用）。

### 3.2 カメラ操作

Frontend で接続 URL を登録・更新し、接続開始／切断を操作できる。接続状況を `connecting` / `connected` / `disconnected` / `error` として表示する。

カメラ切断時は自動再接続を試みる（初期値 3 秒間隔）。切断中は古い検出ボックスを消し、アラートルールの継続判定を停止する。

### 3.3 フレーム処理方針

- CameraService が継続的にフレームを受信する。
- InferenceService は**最新フレームだけ**を取り出して推論する。
- 推論が追いつかない場合、待機フレームをため込まず破棄する（キュー長は最大 1）。
- 最初の実装では**推論に使ったフレームとその検出結果を対として WebSocket 配信**する。異なる時刻の画像にボックスがずれて描かれる問題を避けるため、表示更新 FPS は推論 FPS に準じる。
- 表示 FPS を推論 FPS と独立させる高速プレビュー（WebRTC 等）は将来拡張とする。

## 4. YOLO モデル管理

### 4.1 アップロード

Frontend のモデル管理画面で `.pt` ファイルを選び、`multipart/form-data` で Backend にアップロードする。

Backend は `data/models/` にファイルを保存し、次を確認する。

1. 拡張子 `.pt`、ファイルサイズ上限（初期値 512 MiB）の確認。
2. Ultralytics でのロード可否の確認。
3. タスクが物体検出（detect）であることの確認。
4. `model.names` に相当するクラス一覧の取得。
5. CPU でのダミーフレーム推論を試行し、例外がないことを確認。

失敗時はエラーを画面に表示し、登録を完了しない。登録成功時にモデル名、元ファイル名、格納パス、ファイルサイズ、クラス一覧、登録日時を SQLite に保存する。モデルファイル本体は DB には格納しない。

> モデルの検証は展示 PC のローカル環境で実施する。モデル提供者や学習時の Ultralytics バージョンとの差異によって読み込めない場合があるため、実際に使用するモデルで事前検証する。

### 4.2 管理・有効化

- 複数モデルを登録できるが、**推論に使用するのは 1 モデルのみ**。
- 登録と有効化は別操作とする。アップロードだけでは稼働中のモデルを切り替えない。
- 登録済みモデルの一覧・クラス一覧・モデルサイズ・有効状態を表示する。
- 切り替え時は推論を一時停止し、新モデルのロードを確認してから有効化する。失敗時は直前のモデルを継続使用する。
- 切り替え成功時に検出結果のキャッシュ・アラート判定状態をリセットし、Frontend に変更を通知する。
- 使用中モデルは削除できない。未使用モデルはファイルとメタデータを削除できる。
- 再起動後は最後に有効化したモデルを復元する。まだモデルがなければ「モデル未選択」を表示する。

### 4.3 ルールとの整合性

ルールの対象は **クラス名** として永続化する（クラス ID はモデルごとに変わり得るため）。

- モデル切り替え後、全ルールについてクラス名の存在を再評価する。
- 必要クラスが存在しないルールは `incompatible` とし、判定から除外する。
- ルール設定は削除しない。モデルを戻した際に再利用できる。
- クラス名の一致は初期版では完全一致（大文字小文字も区別）とする。

## 5. 推論仕様

| 設定 | 初期値 | 備考 |
| --- | --- | --- |
| 推論デバイス | `cpu` | 固定 |
| Confidence | `0.40` | Frontend から変更可 |
| NMS IoU | `0.70` | Frontend から変更可 |
| `imgsz` | `416` | モデル互換の整数値に変更可 |
| 目標推論 FPS | `5` | 上限目標。性能保証ではない |
| Batch Size | `1` | 固定 |
| カメラ画像サイズ | カメラの配信設定に依存 | 初期評価は 1280×720 を想定 |

検出データは `frame_id`、`timestamp`、`class_id`、`class_name`、`confidence`、`bbox=[x1,y1,x2,y2]` を含む。`bbox` は**配信 JPEG 画像のピクセル座標**とする。

UI には実測推論 FPS、処理時間（ms）、モデル名、カメラの状態を表示する。

CPU 性能とモデル規模によって実際の FPS が変動するため、展示前に `imgsz` / Confidence / FPS 上限を調整する。GPU 対応、量子化、ONNX 変換、モデルの追加学習は初期スコープに含めない。

## 6. アラートルール

### 6.1 登録

- 名前と **2 種類以上の異なるクラス名**を選択してルールを登録する。
- 同じクラスを重複選択できない。
- 選択可能なクラスは現在有効なモデルから取得する。
- ルールはいくつでも登録でき、ルール間は OR（各ルールが独立に評価）とする。
- ルール単位で ON/OFF、条件成立時間、解除時間、クールダウンを設定する。

例:

| ルール名 | 必要クラス | 判定 |
| --- | --- | --- |
| 手とドリル | `hand` + `drill` | 同一フレーム内に両方ある |
| 手と手袋 | `hand` + `glove` | 同一フレーム内に両方ある |
| 人・ヘルメット・手袋 | `person` + `helmet` + `glove` | 同一フレーム内にすべてある |

**物体同士の距離、接触、人物との対応付け、位置関係は判定しない。** 同じクラスが複数必要という個数条件も初期版では対象外。

### 6.2 状態遷移

各ルールに `idle`（未成立） / `pending`（成立待ち） / `active`（発報中）を持たせる。

1. 各 YOLO 推論フレームから、Confidence 閾値を満たすクラス名集合を作る。
2. ルールの全クラス名が含まれていれば、そのフレームは条件成立とする。
3. **連続 2 推論フレーム以上**かつ**最初の成立から 0.5 秒以上**続いた時点で 1 回発報する。
4. `active` 中は同一ルールのアラートを連発しない。
5. 条件が **2.0 秒連続で不成立**になったら解除して `idle` に戻す。
6. 前回の発報から **10 秒**のクールダウン中は再発報しない（成立状態は画面表示できる）。
7. カメラ切断、推論停止、モデル切り替え時は `pending` / `active` をクリアする。
8. 推論結果が 2 秒以上届かなければ継続成立とは扱わず、画面にストリーム異常を示す。

初期値は `detection_duration_ms=500`、`release_duration_ms=2000`、`cooldown_ms=10000` とし、ルールごとに変更できる。クールダウンは最終発報時刻を基準とする。

### 6.3 アラート表示・記録

発報時に、アラート名、発報日時、対象クラスを画面上で強調表示する。アラート対象の Bounding Box も強調する。

PC からの警告音はブラウザで再生する。ブラウザの自動再生制限に対応するため、展示開始時に「音声を有効化」をクリックする。音声の ON/OFF とテスト発報ボタンを設ける。

アラート履歴は**発報イベント単位**で SQLite に保存する。各推論フレームは保存せず、動画や画像も初期版では保存しない。画面上では直近の履歴を表示できる。

## 7. Frontend 画面仕様

### 7.1 ダッシュボード（メイン画面）

1920×1080 の PC／展示モニターを主な対象とし、1 画面で以下を操作・確認できる。

- **映像エリア:** ライブ映像、Bounding Box、クラス名、Confidence、検出対象の強調。
- **アラートルール:** ルール一覧、ON/OFF、新規追加、編集、削除、モデル不一致表示。
- **警告エリア:** 発報中ルール、警告バナー、発報時刻、音声 ON/OFF、テスト発報。
- **状態エリア:** カメラ接続、Backend 接続、モデル名、実測推論 FPS、推論時間。
- **ナビゲーション:** モデル管理、カメラ／推論設定、アラート履歴。

初期版では Canvas を用いて JPEG フレームの上に検出ボックスを描く。映像の縦横比を維持し、リサイズ時は座標を映像の表示倍率に合わせて変換する。

### 7.2 モデル管理画面

- `.pt` ファイルを選択してアップロード。
- 進捗、登録成否、検証エラーを表示。
- 登録済みモデルの一覧／クラス一覧を表示。
- 使用モデルの有効化、未使用モデルの削除。

### 7.3 設定画面

- RTSP URL と接続操作。
- Confidence、NMS IoU、`imgsz`、目標 FPS。
- ボックス・ラベル・Confidence の表示切り替え。
- 警告音 ON/OFF。

設定は Backend の SQLite に保存し、画面リロード後も保持する。音声の再生許可状態はブラウザごとに再操作が必要となる場合がある。

### 7.4 エラー表示

未接続、モデル未選択、モデル不一致、推論エラー、WebSocket 切断を日本語で明示し、過去のフレームを正常稼働中のように表示し続けない。

## 8. API 仕様

すべての REST パスは `/api` プレフィックスを持つ。ID は初期実装では整数とする。

| Method | Endpoint | 用途 |
| --- | --- | --- |
| GET | `/api/health` | Backend 稼働状態 |
| GET | `/api/camera` | カメラ設定と状態 |
| PATCH | `/api/camera` | 映像入力種別・URL の更新 |
| POST | `/api/camera/connect` | カメラ接続開始 |
| POST | `/api/camera/disconnect` | カメラ切断 |
| GET | `/api/models` | モデル一覧 |
| POST | `/api/models` | `multipart/form-data` による .pt 登録 |
| GET | `/api/models/{id}` | モデル詳細とクラス |
| POST | `/api/models/{id}/activate` | 使用モデル切り替え |
| DELETE | `/api/models/{id}` | 未使用モデルの削除 |
| GET | `/api/models/active` | 使用中モデル |
| GET | `/api/classes` | 使用中モデルのクラス一覧 |
| GET | `/api/rules` | ルール一覧 |
| POST | `/api/rules` | ルール登録 |
| PUT | `/api/rules/{id}` | ルール変更 |
| DELETE | `/api/rules/{id}` | ルール削除 |
| GET | `/api/alerts` | 新しい順で履歴（`limit` / `offset`） |
| POST | `/api/alerts/test` | 音声・画面確認用テスト通知 |
| GET | `/api/settings` | 推論・表示設定取得 |
| PATCH | `/api/settings` | 推論・表示設定更新 |

応答は JSON。更新 API では入力を検証し、不正なクラスや値は `422`、存在しない ID は `404`、使用中モデル削除等は `409` を返す。アップロード失敗時は読み込み失敗理由をメッセージで返す。

### 8.1 WebSocket: `/ws/live`

初期版は**推論済みフレームと検出結果の同期**を優先して単一 WebSocket を使用する。

- **フレーム（バイナリ）:** `uint32_le(header_length)` + `UTF-8 JSON header` + `JPEG bytes`。
- JSON header には `type="frame"`、`frame_id`、`timestamp`、`width`、`height`、`detections`、`matched_rule_ids` を含む。
- **イベント（テキスト JSON）:** `type="alert_started"` / `"alert_cleared"` / `"camera_status"` / `"model_changed"` / `"error"`。
- Frontend は受け取った 1 つのフレームメッセージから JPEG と検出結果を同時に描画する。フレーム ID は受信元フレームを識別する単調増加整数とする。
- WebSocket 切断時、Frontend は自動再接続する。再接続後は REST API から現在の設定と履歴を再取得する。

**フレームヘッダー例（JSON 部分）:**

~~~json
{
  "type": "frame",
  "frame_id": 1234,
  "timestamp": "2026-11-07T18:30:00+09:00",
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

### 8.2 ルール登録例

~~~json
{
  "name": "手とドリル",
  "class_names": ["hand", "drill"],
  "enabled": true,
  "detection_duration_ms": 500,
  "release_duration_ms": 2000,
  "cooldown_ms": 10000
}
~~~

## 9. SQLite データベース

SQLite は 1 ファイル（`data/app.db`）で運用する。テーブルは下記を基本とする。DB アクセスは SQLAlchemy を使用し、デモ用途に必要な範囲でスキーマを簡潔に保つ。

| テーブル | 主なカラム |
| --- | --- |
| `models` | `id, name, original_filename, stored_filename, file_size, created_at` |
| `model_classes` | `id, model_id, class_id, class_name` |
| `model_settings` | `id, active_model_id, confidence, iou_threshold, imgsz, target_fps` |
| `camera_settings` | `id, source_type, rtsp_url, video_path` |
| `alert_rules` | `id, name, enabled, detection_duration_ms, release_duration_ms, cooldown_ms, created_at, updated_at` |
| `alert_rule_classes` | `id, rule_id, class_name` |
| `alert_events` | `id, rule_id, model_id, rule_name_snapshot, detected_classes_json, event_type, created_at` |
| `app_settings` | `key, value_json` |

- `model_settings` / `camera_settings` は単一レコード運用。
- `model_classes` のクラス ID はモデルごとの値を保持する。
- `alert_rule_classes` はクラス名で参照する。
- `alert_events` はルール／モデル削除後も表示可能なスナップショット項目を保持する。
- `app_settings` に音声 ON/OFF など共通 UI 設定を保持する。
- モデル本体、映像、検出フレームは SQLite に保存しない。
- SQLite は WAL モードを推奨する。

## 10. 非機能要件

### 10.1 CPU 性能・遅延

- 目標推論 FPS: **5 FPS**（暫定、保証値ではない）。
- 初期 `imgsz`: **416**。必要に応じ 320/640 等で調整する。
- 目標はアラート判定が遅れて累積しないこと。最新フレーム優先とする。
- モデル読み込みや切り替え処理中も FastAPI の API 応答をブロックしない。
- 推論は別ワーカー（スレッドまたはプロセス）で実行し、WebSocket 送信は非同期で扱う。
- 使用 CPU 型番・モデルサイズが未確定のため、最終 FPS と端末要件は実機確認後に調整する。

### 10.2 安定動作

- RTSP 再接続、WebSocket 再接続。
- モデル切り替えに失敗した場合、元の有効モデルを維持する。
- Backend 再起動後にカメラ URL、使用モデル、ルール、設定を復元する。
- 稼働状態／推論エラーを画面で確認可能とする。
- 展示当日の 30 分以上の連続稼働試験を実施する。

### 10.3 スコープ外

GPU/CUDA 推論、複数カメラ、複数モデル同時推論、物体追跡、物体間の距離／接触／包含判定、ROI、録画、静止画保存、モデルの再学習、クラウド連携、外部ブザー／警告灯、メール通知、ログイン／認証システムは対象外。

## 11. 既存リポジトリとの整合性

2026-11-07 時点のリポジトリは以下の最小構成である。

- `backend/main.py` に FastAPI アプリが存在。
- `backend/requirements.txt` でバックエンド依存を管理。
- `frontend/` は React + Vite、`src/App.jsx` が存在（TypeScript ではない）。
- `frontend/package.json` で Frontend 依存を管理。

**初期実装ではこの構成を維持**し、必要に応じて次のように拡張する。

~~~text
2027-tukuba-exhibition/
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── app/
│       ├── api/
│       ├── camera/
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
│   ├── models/           # 実行時の .pt ファイル
│   └── app.db            # 実行時の SQLite
├── docs/
│   └── specification.md  # 本書
└── README.md
~~~

`data/` 以下は実行時に生成し、モデルと DB は Git 管理対象外にすることを推奨する（実装時に `.gitignore` を確認・追記する）。

## 12. 開発フェーズと受け入れ基準

### Phase 1: Backend 基盤

1. SQLite のスキーマ作成。
2. `.pt` アップロード、登録、切り替え、クラス一覧 API。
3. RTSP／動画ファイル入力。
4. CPU での YOLO 推論と検出結果生成。

### Phase 2: Frontend とリアルタイム表示

1. WebSocket フレーム配信。
2. React ダッシュボードと Bounding Box。
3. モデル管理画面とカメラ／推論設定。

### Phase 3: アラートと展示確認

1. ルール CRUD と同一フレーム AND 判定。
2. 継続判定、解除、クールダウン。
3. 画面警告、ブラウザ音声、イベント履歴。
4. 実機 RTSP 接続、CPU FPS 測定、30 分連続動作試験。

### 完了判定（最小受け入れ試験）

- [ ] Raspberry Pi または reCamera の RTSP 映像が表示される。
- [ ] Frontend からカスタム `.pt` を登録でき、クラス一覧が表示される。
- [ ] 登録済みモデルを画面から切り替えられる。
- [ ] CPU のみで推論し、同じフレームに合う Bounding Box が描画される。
- [ ] 複数クラスの組み合わせルールを画面で追加・編集・削除できる。
- [ ] AND 条件成立時のみ、継続判定後にアラートが表示・発音される。
- [ ] 継続中にアラートを連発せず、解除後の再発報が機能する。
- [ ] アラート履歴が SQLite に保存される。
- [ ] モデル切り替え時、非互換ルールが明示され誤発報しない。
- [ ] カメラ／WebSocket 切断時の状態表示と再接続が動作する。
- [ ] 再起動後も設定・登録モデル・ルールが維持される。
- [ ] 展示 PC 上で 30 分以上の連続稼働を確認する。

---

**未確定事項:** 展示 PC の CPU 型番、使用する `.pt` モデルの規模、使用する Raspberry Pi/reCamera の具体的な機種と RTSP URL。これらは初期開発のブロッカーとせず、実機評価時に確定する。
