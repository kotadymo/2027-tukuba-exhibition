# 2027-tukuba-exhibition

FastAPI のバックエンドと React（Vite）のフロントエンドを管理するリポジトリです。

## バックエンド

Python 3.10 以上が必要です。リポジトリのルートから実行します。

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

API は http://127.0.0.1:8000/ 、API ドキュメントは http://127.0.0.1:8000/docs で確認できます。

## フロントエンド

Node.js 20.19 以上の 20 系、または 22.12 以上が必要です。別のターミナルでリポジトリのルートから実行します。

```sh
cd frontend
npm ci
npm run dev
```

表示された URL（通常は http://localhost:5173/ ）にアクセスします。
`npm run lint` で lint、`npm run build` で本番用ビルドを実行できます。