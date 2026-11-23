# 2027 つくば展示（v0.7.0）

FastAPI と React（Vite）/ MUI で動作する、YOLO物体検出・安全教育展示システムです。Insta360 GO Ultra のライブ映像または Backend に登録した MP4 動画を入力し、CUDA GPUまたはCPUで推論します。同一フレームに複数の対象クラスがそろった場合に画面とブラウザ音声で通知し、発報画像と全検出情報を SQLite に保存します。危険理由マスターを共有するルールから危険箇所タップ・危険理由2択のクイズを生成します。

## 必要環境

- Python 3.10 以上
- Node.js 20.19 以上の 20 系、または 22.12 以上
- Ultralytics / PyTorch 環境（CPU推論。NVIDIA GPUを使う場合はCUDA対応PyTorchと対応ドライバー）
- Insta360 GO Ultra のライブ入力には、展示 PC から FFmpeg を実行できる環境とカメラの Wi-Fi 接続
- 動画入力のみを使う場合は FFmpeg が利用可能な OpenCV 環境

依存パッケージは事前にインストールしてください。通常の推論・展示時にインターネット接続は必要ありません。

## 開発起動（Windows PowerShell）

バックエンドを起動します。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

別のターミナルでフロントエンドを起動します。

```powershell
cd frontend
npm ci
npm run dev
```

通常は http://localhost:5173/ を開きます。Vite の開発サーバーは `/api` と `/ws` を `127.0.0.1:8000` の Backend へ転送します。Backend API と Swagger UI は http://127.0.0.1:8000/docs で確認できます。

## 展示用ビルド

フロントエンドをビルドしてから Backend を起動すると、FastAPI が `frontend/dist/` を配信します。

```powershell
cd frontend
npm ci
npm run build
cd ..\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## 操作

1. 「モデル管理」で信頼できる物体検出用 `.pt` を登録し、有効化します。登録時に CPU でモデルを読み込み、detect タスク、クラス一覧、ダミー推論を検証します。有効化時は設定中のデバイスへ再ロードしてダミー推論を行います。
2. ライブ入力では展示 PC を GO Ultra の Wi-Fi に接続し、ダッシュボードで「Insta360 GO Ultra」を選んで接続します。動画入力では MP4 をアップロードします。動画はアップロード直後に選択され、再生ボタンを押すまで推論を開始しません。
3. 「危険理由マスター」で正解理由・誤答理由・解説を登録します。正解理由と誤答理由は異なる文章にしてください。2種類以上のクラスから AND 条件のアラートルールを作成し、教材用の正解対象クラスと危険理由マスターを割り当てます。同じマスターを複数ルールから共有できます。
4. 展示開始時にダッシュボードで「音声を有効化」を押します。音声の ON/OFF、テスト通知、発報履歴も画面から操作できます。履歴の詳細では保存画像と全検出ボックスを確認し、元 JPEG をダウンロードできます。
5. 「問題に挑戦」を開くと、異なる危険理由IDから最大3問を作成します。同じ理由IDはルールが異なっても1セットに1問だけで、理由が3種類未満なら利用できる理由数だけ出題します。正解の危険箇所を画像上でタップするとマスターの正解・誤答の2択が表示され、回答後は正しい理由と解説を確認できます。

Frontend は元動画を直接再生しません。Backend がデコード・推論したフレームと、そのフレームの検出情報を WebSocket で一緒に送信するため、映像と Bounding Box が同期します。

教材の切り出し画像は SQLite に追加保存せず、発報時の元 JPEG と保存済み切り出し座標から要求時に生成します。クイズの進行・選択は画面内だけで管理し、学習者の回答・得点・進捗は保存しません。

設定画面の推論モードは `Auto`（初期値）、`NVIDIA GPU (CUDA)`、`CPU` から選べます。Auto はCUDAを優先し、CUDAの初期化または推論に失敗した場合は一度だけCPUへ切り替えます。CUDA固定時はGPUが使えない場合にエラーとなり、CPUへ黙って切り替えません。要求モード、実効デバイス、CUDA/GPU情報、フォールバック理由、推論FPS・処理時間は設定画面に表示されます。

### NVIDIA GPU 用 PyTorch

CPUだけを使う場合は追加作業は不要です。CUDAを使う場合はNVIDIAドライバーを用意し、Backend の仮想環境で、使用OS・Python・GPUドライバーに対応するCUDA wheelをインストールしてください。`requirements.txt` をインストールした後に、[PyTorch Start Locally](https://pytorch.org/get-started/locally/)で **Windows / Pip / Python / CUDA** を選び、表示されたコマンドを実行します。コマンドのCUDA wheel版は環境やPyTorchのリリースに合わせて選択してください。

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu132
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

上記の `cu132` はコマンド例です。公式インストールセレクターに別のCUDA wheelが表示される場合は、そのコマンドを優先してください。導入後にアプリを再起動し、設定画面でCUDA利用可否とGPU名を確認してください。

ライブ入力は [採用 SDK の README](https://github.com/Daiki-Iijima/insta360-go-ultra-sdk) と[公開プロトコル](https://github.com/Daiki-Iijima/insta360-go-ultra-sdk/blob/main/docs/protocol.md)に基づく TCP/H.265 アダプターで取得し、FFmpeg でデコードします。SDK のソースコードは同梱していません。GO Ultra のファームウェア互換性と Windows 実機での連続動作は、展示前に確認してください。

FFmpeg の実行ファイルは `PATH` に追加するか、`FFMPEG_PATH` に指定してください。GO Ultra の Wi-Fi への接続は OS 側で行います。明示的に切断した後は自動再接続しません。

## 保存先と設定

初期設定では、実行時データをリポジトリ直下の `data/` に保存します。SQLite は `data/app.db`、モデルは `data/models/`、動画は `data/videos/` に保存されます。これらのデータは Git 管理対象外です。

必要に応じて次の環境変数を設定できます。

| 環境変数 | 初期値 | 用途 |
| --- | --- | --- |
| `APP_DATA_DIR` | リポジトリ直下の `data/` | DB・モデル・動画の保存先 |
| `DATABASE_URL` | `APP_DATA_DIR/app.db` | SQLAlchemy 接続 URL |
| `MAX_MODEL_SIZE_BYTES` | 536870912 | `.pt` の最大サイズ（512 MiB） |
| `MAX_VIDEO_SIZE_BYTES` | 1073741824 | MP4 の最大サイズ（1 GiB） |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Backend が許可するブラウザー origin |
| `VITE_API_BASE_URL` | 空（同一 origin） | Frontend から Backend への接続先 |
| `FFMPEG_PATH` | `PATH` から検索 | FFmpeg 実行ファイルのパス |

モデルの `.pt` ファイルは任意コード実行の危険を伴う場合があります。提供元を確認した信頼できるファイルだけを登録してください。複数カメラ、録画、動画の任意シークは対象外です。

アラート画像は初期設定 JPEG Quality 80 で SQLite の BLOB に保存されます。品質は設定画面から変更できます。履歴は自動削除されないため、不要になった場合は履歴画面で全件削除してください。

### v0.7.0 の SQLite 初期化

v0.7.0 は危険理由マスター、デバイス設定、発報スナップショット用にSQLiteスキーマが変更されています。旧バージョンのDBを自動変換・削除しません。旧スキーマで起動した場合はBackendがエラーを表示します。初期化が必要な場合だけ、Backendを停止し、保存先をバックアップしてからSQLiteファイルを手動で退避してください。

リポジトリ直下の既定保存先での例です。`APP_DATA_DIR` または `DATABASE_URL` を変更している場合は、実際に設定したSQLiteファイルを対象にしてください。

```powershell
# Backend を停止してから、リポジトリ直下で実行
$stamp = Get-Date -Format yyyyMMddHHmmss
Copy-Item .\data\app.db ".\data\app.db.$stamp.bak"
if (Test-Path .\data\app.db-wal) { Copy-Item .\data\app.db-wal ".\data\app.db-wal.$stamp.bak" }
if (Test-Path .\data\app.db-shm) { Copy-Item .\data\app.db-shm ".\data\app.db-shm.$stamp.bak" }
Move-Item .\data\app.db ".\data\app.db.$stamp.old"
if (Test-Path .\data\app.db-wal) { Move-Item .\data\app.db-wal ".\data\app.db-wal.$stamp.old" }
if (Test-Path .\data\app.db-shm) { Move-Item .\data\app.db-shm ".\data\app.db-shm.$stamp.old" }
```

次回起動時に新しいスキーマを作成します。モデル・動画の実ファイルは削除されませんが、DBの登録情報は新しくなるため、画面からモデルと動画を再登録してください。通常の起動ではDBを削除しません。

## 検証

```powershell
cd backend
python -m unittest discover -s tests
cd ..\frontend
npm run lint
npm run build
```

展示前には使用する `.pt` と GO Ultra を実機で確認し、CPU 負荷と推論 FPS を調整してください。カメラ接続、FFmpeg デコード、30 分以上の連続稼働は展示 PC と実機で別途検証してください。
