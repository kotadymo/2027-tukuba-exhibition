# 2027 つくば展示

FastAPI と React（Vite）で動作する、CPU 推論の物体検出・アラート展示システムです。Insta360 GO Ultra のライブ映像または Backend に登録した MP4 動画を入力し、同一フレームに複数の対象クラスがそろった場合に画面とブラウザ音声で通知します。発報した元画像と、そのフレームの全検出情報は SQLite に保存します。

## 必要環境

- Python 3.10 以上
- Node.js 20.19 以上の 20 系、または 22.12 以上
- CPU 推論に対応する Ultralytics / PyTorch 環境
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

1. 「モデル管理」で信頼できる物体検出用 `.pt` を登録し、有効化します。登録時に CPU でモデルを読み込み、detect タスク、クラス一覧、ダミー推論を検証します。
2. ライブ入力では展示 PC を GO Ultra の Wi-Fi に接続し、ダッシュボードで「Insta360 GO Ultra」を選んで接続します。動画入力では MP4 をアップロードします。動画はアップロード直後に選択され、再生ボタンを押すまで推論を開始しません。
3. 2 種類以上のクラスから AND 条件のアラートルールを作成します。成立時間、解除時間、クールダウンはルールごとに設定できます。
4. 展示開始時にダッシュボードで「音声を有効化」を押します。音声の ON/OFF、テスト通知、発報履歴も画面から操作できます。履歴の詳細では保存画像と全検出ボックスを確認し、元 JPEG をダウンロードできます。

Frontend は元動画を直接再生しません。Backend がデコード・推論したフレームと、そのフレームの検出情報を WebSocket で一緒に送信するため、映像と Bounding Box が同期します。

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

モデルの `.pt` ファイルは任意コード実行の危険を伴う場合があります。提供元を確認した信頼できるファイルだけを登録してください。GPU 推論、複数カメラ、録画、動画の任意シークは初期スコープ外です。

アラート画像は初期設定 JPEG Quality 80 で SQLite の BLOB に保存されます。品質は設定画面から変更できます。履歴は自動削除されないため、不要になった場合は履歴画面で全件削除してください。

## 検証

```powershell
cd backend
python -m unittest discover -s tests
cd ..\frontend
npm run lint
npm run build
```

展示前には使用する `.pt` と GO Ultra を実機で確認し、CPU 負荷と推論 FPS を調整してください。カメラ接続、FFmpeg デコード、30 分以上の連続稼働は展示 PC と実機で別途検証してください。
