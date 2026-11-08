from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, selectinload

from .database import (
    AlertEvent,
    AlertFrame,
    AlertFrameDetection,
    AlertRule,
    AlertRuleClass,
    AppSetting,
    Insta360Settings,
    MAX_MODEL_SIZE_BYTES,
    MAX_VIDEO_SIZE_BYTES,
    MODELS_DIR,
    ModelClass,
    ModelRecord,
    ModelSettings,
    SessionLocal,
    SourceSettings,
    VIDEOS_DIR,
    Video,
    initialize_database,
)
from .inference import Detector, ModelValidationError
from .runtime import LiveRuntime, WebSocketHub
from .schemas import (
    Insta360SettingsPatch,
    LoopPatch,
    PlaybackAction,
    RulePayload,
    SettingsPatch,
    SourcePatch,
)


logger = logging.getLogger(__name__)
DEFAULT_APP_SETTINGS = {
    "show_boxes": True,
    "show_labels": True,
    "show_confidence": True,
    "audio_enabled": True,
    "jpeg_quality": 80,
}


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _rule_configuration(db: Session) -> list[dict[str, Any]]:
    records = db.scalars(
        select(AlertRule).options(selectinload(AlertRule.classes)).order_by(AlertRule.id)
    ).all()
    return [
        {
            "id": rule.id,
            "name": rule.name,
            "class_names": [item.class_name for item in rule.classes],
            "enabled": rule.enabled,
            "detection_duration_ms": rule.detection_duration_ms,
            "release_duration_ms": rule.release_duration_ms,
            "cooldown_ms": rule.cooldown_ms,
        }
        for rule in records
    ]


def _serialize_video(video: Video, selected_id: int | None = None) -> dict[str, Any]:
    return {
        "id": video.id,
        "name": video.name,
        "original_filename": video.original_filename,
        "file_size": video.file_size,
        "width": video.width,
        "height": video.height,
        "fps": video.fps,
        "duration_ms": video.duration_ms,
        "created_at": _isoformat_utc(video.created_at),
        "selected": selected_id == video.id,
    }


def _serialize_model(
    model: ModelRecord,
    active_model_id: int | None,
    loaded_model_id: int | None,
) -> dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "original_filename": model.original_filename,
        "file_size": model.file_size,
        "created_at": _isoformat_utc(model.created_at),
        "active": active_model_id == model.id,
        "loaded": loaded_model_id == model.id,
        "classes": [
            {"class_id": item.class_id, "class_name": item.class_name}
            for item in model.classes
        ],
    }


def _serialize_rule(rule: AlertRule, statuses: dict[int, dict[str, Any]]) -> dict[str, Any]:
    status = statuses.get(
        rule.id,
        {"state": "idle", "incompatible": False, "missing_class_names": []},
    )
    return {
        "id": rule.id,
        "name": rule.name,
        "class_names": [item.class_name for item in rule.classes],
        "enabled": rule.enabled,
        "detection_duration_ms": rule.detection_duration_ms,
        "release_duration_ms": rule.release_duration_ms,
        "cooldown_ms": rule.cooldown_ms,
        "state": status["state"],
        "incompatible": status["incompatible"],
        "missing_class_names": status["missing_class_names"],
        "created_at": _isoformat_utc(rule.created_at),
        "updated_at": _isoformat_utc(rule.updated_at),
    }


def _get_or_404(db: Session, model_type, item_id: int, label: str):
    item = db.get(model_type, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{label}が見つかりません")
    return item


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _active_model_id(db: Session) -> int | None:
    settings = db.get(ModelSettings, 1)
    return settings.active_model_id if settings else None


def _app_settings(db: Session) -> dict[str, Any]:
    values = dict(DEFAULT_APP_SETTINGS)
    for setting in db.scalars(select(AppSetting)).all():
        values[setting.key] = json.loads(setting.value_json)
    return values


def _source_response(db: Session, runtime: LiveRuntime) -> dict[str, Any]:
    settings = db.get(SourceSettings, 1)
    snapshot = runtime.source_snapshot()
    source_type = settings.source_type if settings else "insta360"
    return {
        "source_type": source_type,
        "active_video_id": settings.active_video_id if settings else None,
        "loop_enabled": settings.loop_enabled if settings else True,
        "status": (
            snapshot["insta360_status"]
            if source_type == "insta360"
            else snapshot["video_status"]
        ),
        "insta360_status": snapshot["insta360_status"],
        "insta360_error": snapshot["insta360_error"],
        "last_frame_received_at": snapshot["last_frame_received_at"],
        "video_status": snapshot["video_status"],
        "video_error": snapshot["video_error"],
    }


def _insta360_response(db: Session, runtime: LiveRuntime) -> dict[str, Any]:
    settings = db.get(Insta360Settings, 1)
    snapshot = runtime.source_snapshot()
    return {
        "camera_ip": settings.camera_ip if settings else "192.168.42.1",
        "tcp_port": settings.tcp_port if settings else 6666,
        "reconnect_interval_ms": (
            settings.reconnect_interval_ms if settings else 3000
        ),
        "status": snapshot["insta360_status"],
        "error": snapshot["insta360_error"],
        "last_frame_received_at": snapshot["last_frame_received_at"],
    }


def _set_app_setting(db: Session, key: str, value: Any) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value_json=json.dumps(value, ensure_ascii=False)))
    else:
        setting.value_json = json.dumps(value, ensure_ascii=False)


def _safe_upload_name(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:255] or "upload"


async def _write_upload(
    upload: UploadFile,
    destination_dir: Path,
    suffix: str,
    max_size: int,
) -> tuple[Path, Path, int, str]:
    original_name = _safe_upload_name(upload.filename)
    if Path(original_name).suffix.lower() != suffix:
        await upload.close()
        raise HTTPException(status_code=422, detail=f"{suffix.upper()} ファイルを選択してください")
    file_id = uuid.uuid4().hex
    temporary_path = destination_dir / f"{file_id}.uploading{suffix}"
    final_path = destination_dir / f"{file_id}{suffix}"
    total_size = 0
    try:
        with temporary_path.open("xb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail=f"ファイルサイズが上限（{max_size} bytes）を超えています",
                    )
                output.write(chunk)
        if total_size == 0:
            raise HTTPException(status_code=422, detail="空のファイルは登録できません")
        return temporary_path, final_path, total_size, original_name
    except HTTPException:
        temporary_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        logger.exception("Could not save an uploaded file")
        raise HTTPException(status_code=500, detail=f"アップロードを保存できません: {exc}") from exc
    finally:
        await upload.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    hub = WebSocketHub()
    hub.set_loop(asyncio.get_running_loop())
    runtime = LiveRuntime(hub)
    app.state.hub = hub
    app.state.runtime = runtime
    app.state.started_at = datetime.now().astimezone()
    await runtime.start()
    try:
        yield
    finally:
        runtime.stop()


app = FastAPI(
    title="動体展示用 AI 物体検出・アラートシステム",
    version="0.5.1",
    lifespan=lifespan,
)

allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    index_file = frontend_dist / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {"message": "2027-tukuba-exhibition", "docs": "/docs"}


@app.get("/api/health")
def health(request: Request):
    runtime: LiveRuntime = request.app.state.runtime
    started_at = request.app.state.started_at
    return {
        "status": "ok",
        "uptime_seconds": max(0, int((datetime.now().astimezone() - started_at).total_seconds())),
        "source_type": runtime.source_snapshot()["source_type"],
        "model_id": runtime.active_model_id,
        "model_loaded": runtime.model_loaded,
        "model_error": runtime.model_error,
    }


@app.get("/api/source")
def get_source(request: Request, db: Session = Depends(_get_db)):
    return _source_response(db, request.app.state.runtime)


@app.patch("/api/source")
def patch_source(
    payload: SourcePatch, request: Request, db: Session = Depends(_get_db)
):
    settings = db.get(SourceSettings, 1)
    if settings is None:
        settings = SourceSettings(
            id=1,
            source_type=payload.source_type,
            active_video_id=None,
            loop_enabled=True,
        )
        db.add(settings)
        changed = True
    else:
        changed = settings.source_type != payload.source_type
        settings.source_type = payload.source_type
    db.commit()

    if changed:
        runtime: LiveRuntime = request.app.state.runtime
        if payload.source_type == "insta360":
            runtime.capture.select_source("insta360")
        else:
            video = (
                db.get(Video, settings.active_video_id)
                if settings.active_video_id is not None
                else None
            )
            if video is None:
                settings.active_video_id = None
                db.commit()
                runtime.capture.select_empty_video(settings.loop_enabled)
            else:
                runtime.capture.activate_video(
                    video.id,
                    VIDEOS_DIR / video.stored_filename,
                    settings.loop_enabled,
                    video.fps,
                    video.duration_ms,
                )
    return _source_response(db, request.app.state.runtime)


@app.get("/api/insta360")
def get_insta360(request: Request, db: Session = Depends(_get_db)):
    return _insta360_response(db, request.app.state.runtime)


@app.patch("/api/insta360")
def patch_insta360(
    payload: Insta360SettingsPatch,
    request: Request,
    db: Session = Depends(_get_db),
):
    settings = db.get(Insta360Settings, 1)
    if settings is None:
        settings = Insta360Settings(
            id=1,
            camera_ip="192.168.42.1",
            tcp_port=6666,
            reconnect_interval_ms=3000,
        )
        db.add(settings)
    values = payload.model_dump(exclude_unset=True)
    if any(value is None for value in values.values()):
        raise HTTPException(status_code=422, detail="Insta360 設定値に null は指定できません")
    for key, value in values.items():
        setattr(settings, key, value)
    db.commit()
    request.app.state.runtime.capture.configure_insta360(
        settings.camera_ip,
        settings.tcp_port,
        settings.reconnect_interval_ms,
    )
    return _insta360_response(db, request.app.state.runtime)


@app.post("/api/insta360/connect")
def connect_insta360(request: Request, db: Session = Depends(_get_db)):
    source = db.get(SourceSettings, 1)
    if source is None or source.source_type != "insta360":
        raise HTTPException(status_code=409, detail="Insta360 入力が選択されていません")
    request.app.state.runtime.capture.connect_insta360()
    return _insta360_response(db, request.app.state.runtime)


@app.post("/api/insta360/disconnect")
def disconnect_insta360(request: Request, db: Session = Depends(_get_db)):
    source = db.get(SourceSettings, 1)
    if source is None or source.source_type != "insta360":
        raise HTTPException(status_code=409, detail="Insta360 入力が選択されていません")
    request.app.state.runtime.capture.disconnect_insta360()
    return _insta360_response(db, request.app.state.runtime)


@app.get("/api/videos")
def list_videos(db: Session = Depends(_get_db)):
    settings = db.get(SourceSettings, 1)
    selected_id = settings.active_video_id if settings else None
    records = db.scalars(select(Video).order_by(Video.created_at.desc(), Video.id.desc())).all()
    return [_serialize_video(video, selected_id) for video in records]


@app.get("/api/videos/playback")
def get_video_playback(request: Request, db: Session = Depends(_get_db)):
    runtime: LiveRuntime = request.app.state.runtime
    snapshot = runtime.source_snapshot()
    settings = db.get(SourceSettings, 1)
    video_id = settings.active_video_id if settings else None
    video = db.get(Video, video_id) if video_id is not None else None
    return {
        "video": _serialize_video(video, video_id) if video is not None else None,
        "source_type": settings.source_type if settings else "insta360",
        "status": snapshot["video_status"],
        "position_ms": snapshot["position_ms"],
        "duration_ms": video.duration_ms if video is not None else None,
        "loop": settings.loop_enabled if settings else True,
        "error": snapshot["video_error"],
    }


@app.post("/api/videos", status_code=201)
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(_get_db),
):
    temporary_path, final_path, size, original_name = await _write_upload(
        file, VIDEOS_DIR, ".mp4", MAX_VIDEO_SIZE_BYTES
    )
    capture = cv2.VideoCapture(str(temporary_path))
    try:
        if not capture.isOpened():
            raise HTTPException(status_code=422, detail="MP4 動画を開けません")
        success, first_frame = capture.read()
        if not success or first_frame is None:
            raise HTTPException(
                status_code=422,
                detail="動画の先頭フレームをデコードできません。対応コーデックを確認してください",
            )
        height, width = first_frame.shape[:2]
        fps_value = capture.get(cv2.CAP_PROP_FPS)
        count_value = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = float(fps_value) if math.isfinite(fps_value) and fps_value > 0 else None
        frame_count = (
            float(count_value)
            if math.isfinite(count_value) and count_value > 0
            else None
        )
        duration_ms = (
            int(frame_count / fps * 1000)
            if fps is not None and frame_count is not None
            else None
        )
    except HTTPException:
        temporary_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        logger.exception("Could not decode uploaded video")
        raise HTTPException(status_code=422, detail=f"動画を検証できません: {exc}") from exc
    finally:
        capture.release()

    try:
        shutil.move(str(temporary_path), str(final_path))
        video = Video(
            name=Path(original_name).stem[:255] or original_name,
            original_filename=original_name,
            stored_filename=final_path.name,
            file_size=size,
            width=width,
            height=height,
            fps=fps,
            duration_ms=duration_ms,
        )
        db.add(video)
        db.flush()
        settings = db.get(SourceSettings, 1)
        if settings is None:
            settings = SourceSettings(
                id=1,
                source_type="video",
                active_video_id=video.id,
                loop_enabled=True,
            )
            db.add(settings)
        settings.source_type = "video"
        settings.active_video_id = video.id
        db.commit()
        db.refresh(video)
        loop_enabled = settings.loop_enabled
    except HTTPException:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        db.rollback()
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        logger.exception("Could not register uploaded video")
        raise HTTPException(status_code=500, detail=f"動画を登録できません: {exc}") from exc
    request.app.state.runtime.capture.activate_video(
        video.id,
        VIDEOS_DIR / video.stored_filename,
        loop_enabled,
        video.fps,
        video.duration_ms,
    )
    return _serialize_video(video, video.id)


@app.get("/api/videos/{video_id}")
def get_video(video_id: int, db: Session = Depends(_get_db)):
    video = _get_or_404(db, Video, video_id, "動画")
    settings = db.get(SourceSettings, 1)
    selected_id = settings.active_video_id if settings else None
    return _serialize_video(video, selected_id)


@app.post("/api/videos/{video_id}/activate")
def activate_video(video_id: int, request: Request, db: Session = Depends(_get_db)):
    video = _get_or_404(db, Video, video_id, "動画")
    settings = db.get(SourceSettings, 1)
    if settings is None:
        settings = SourceSettings(
            id=1,
            source_type="video",
            active_video_id=video.id,
            loop_enabled=True,
        )
        db.add(settings)
    settings.source_type = "video"
    settings.active_video_id = video.id
    db.commit()
    request.app.state.runtime.capture.activate_video(
        video.id,
        VIDEOS_DIR / video.stored_filename,
        settings.loop_enabled,
        video.fps,
        video.duration_ms,
    )
    return get_video_playback(request, db)


@app.post("/api/videos/playback")
def control_video_playback(
    payload: PlaybackAction,
    request: Request,
    db: Session = Depends(_get_db),
):
    settings = db.get(SourceSettings, 1)
    if settings is None or settings.source_type != "video" or settings.active_video_id is None:
        raise HTTPException(status_code=409, detail="再生する動画を選択してください")
    try:
        request.app.state.runtime.capture.playback(payload.action)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return get_video_playback(request, db)


@app.patch("/api/videos/playback")
def set_video_loop(payload: LoopPatch, request: Request, db: Session = Depends(_get_db)):
    settings = db.get(SourceSettings, 1)
    if settings is None:
        settings = SourceSettings(
            id=1,
            source_type="video",
            active_video_id=None,
            loop_enabled=payload.loop,
        )
        db.add(settings)
    settings.loop_enabled = payload.loop
    db.commit()
    request.app.state.runtime.capture.set_loop(payload.loop)
    return get_video_playback(request, db)


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: int, request: Request, db: Session = Depends(_get_db)):
    video = _get_or_404(db, Video, video_id, "動画")
    settings = db.get(SourceSettings, 1)
    runtime: LiveRuntime = request.app.state.runtime
    snapshot = runtime.source_snapshot()
    if (
        settings is not None
        and settings.source_type == "video"
        and settings.active_video_id == video_id
        and snapshot["video_status"] == "playing"
    ):
        raise HTTPException(status_code=409, detail="再生中の動画は削除できません")

    if settings is not None and settings.active_video_id == video_id:
        settings.active_video_id = None
        if settings.source_type == "video":
            try:
                runtime.capture.clear_video(video_id, settings.loop_enabled)
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    stored_path = VIDEOS_DIR / video.stored_filename
    if stored_path.exists():
        try:
            stored_path.unlink()
        except OSError as exc:
            logger.exception("Could not remove video file")
            raise HTTPException(status_code=500, detail=f"動画ファイルを削除できません: {exc}") from exc
    db.delete(video)
    db.commit()
    return {"deleted": True, "id": video_id}


@app.get("/api/models")
def list_models(request: Request, db: Session = Depends(_get_db)):
    settings = db.get(ModelSettings, 1)
    active_id = settings.active_model_id if settings else None
    runtime: LiveRuntime = request.app.state.runtime
    records = db.scalars(
        select(ModelRecord)
        .options(selectinload(ModelRecord.classes))
        .order_by(ModelRecord.created_at.desc(), ModelRecord.id.desc())
    ).all()
    return [
        _serialize_model(model, active_id, runtime.active_model_id if runtime.model_loaded else None)
        for model in records
    ]


@app.post("/api/models", status_code=201)
async def upload_model(file: UploadFile = File(...), db: Session = Depends(_get_db)):
    temporary_path, final_path, size, original_name = await _write_upload(
        file, MODELS_DIR, ".pt", MAX_MODEL_SIZE_BYTES
    )
    try:
        detector = await run_in_threadpool(Detector.load, temporary_path)
    except ModelValidationError as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        logger.exception("Could not validate uploaded model")
        raise HTTPException(status_code=422, detail=f"モデルを検証できません: {exc}") from exc

    try:
        shutil.move(str(temporary_path), str(final_path))
        model = ModelRecord(
            name=Path(original_name).stem[:255] or original_name,
            original_filename=original_name,
            stored_filename=final_path.name,
            file_size=size,
            classes=[
                ModelClass(class_id=class_id, class_name=class_name)
                for class_id, class_name in sorted(detector.names.items())
            ],
        )
        db.add(model)
        db.commit()
        db.refresh(model)
    except Exception as exc:
        db.rollback()
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        logger.exception("Could not register uploaded model")
        raise HTTPException(status_code=500, detail=f"モデルを登録できません: {exc}") from exc
    return _serialize_model(model, _active_model_id(db), None)


@app.get("/api/models/active")
def get_active_model(request: Request, db: Session = Depends(_get_db)):
    settings = db.get(ModelSettings, 1)
    if settings is None or settings.active_model_id is None:
        return None
    model = db.scalars(
        select(ModelRecord)
        .options(selectinload(ModelRecord.classes))
        .where(ModelRecord.id == settings.active_model_id)
    ).first()
    if model is None:
        return None
    runtime: LiveRuntime = request.app.state.runtime
    result = _serialize_model(
        model,
        settings.active_model_id,
        runtime.active_model_id if runtime.model_loaded else None,
    )
    result["load_error"] = runtime.model_error
    return result


@app.get("/api/models/{model_id}")
def get_model(model_id: int, request: Request, db: Session = Depends(_get_db)):
    model = db.scalars(
        select(ModelRecord)
        .options(selectinload(ModelRecord.classes))
        .where(ModelRecord.id == model_id)
    ).first()
    if model is None:
        raise HTTPException(status_code=404, detail="モデルが見つかりません")
    settings = db.get(ModelSettings, 1)
    runtime: LiveRuntime = request.app.state.runtime
    return _serialize_model(
        model,
        settings.active_model_id if settings else None,
        runtime.active_model_id if runtime.model_loaded else None,
    )


@app.post("/api/models/{model_id}/activate")
async def activate_model(model_id: int, request: Request, db: Session = Depends(_get_db)):
    model = db.scalars(
        select(ModelRecord)
        .options(selectinload(ModelRecord.classes))
        .where(ModelRecord.id == model_id)
    ).first()
    if model is None:
        raise HTTPException(status_code=404, detail="モデルが見つかりません")
    model_path = MODELS_DIR / model.stored_filename
    try:
        detector = await run_in_threadpool(Detector.load, model_path)
    except ModelValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not load model")
        raise HTTPException(status_code=422, detail=f"モデルを読み込めません: {exc}") from exc

    settings = db.get(ModelSettings, 1)
    if settings is None:
        settings = ModelSettings(id=1)
        db.add(settings)
    settings.active_model_id = model.id
    db.commit()
    runtime: LiveRuntime = request.app.state.runtime
    runtime.set_model(detector, model.id, model.name)
    return _serialize_model(model, model.id, model.id)


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int, db: Session = Depends(_get_db)):
    model = _get_or_404(db, ModelRecord, model_id, "モデル")
    settings = db.get(ModelSettings, 1)
    if settings is not None and settings.active_model_id == model_id:
        raise HTTPException(status_code=409, detail="使用中のモデルは削除できません")
    stored_path = MODELS_DIR / model.stored_filename
    if stored_path.exists():
        try:
            stored_path.unlink()
        except OSError as exc:
            logger.exception("Could not remove model file")
            raise HTTPException(status_code=500, detail=f"モデルファイルを削除できません: {exc}") from exc
    db.delete(model)
    db.commit()
    return {"deleted": True, "id": model_id}


@app.get("/api/classes")
def get_classes(db: Session = Depends(_get_db)):
    settings = db.get(ModelSettings, 1)
    if settings is None or settings.active_model_id is None:
        return []
    return [
        {"class_id": item.class_id, "class_name": item.class_name}
        for item in db.scalars(
            select(ModelClass)
            .where(ModelClass.model_id == settings.active_model_id)
            .order_by(ModelClass.class_id)
        ).all()
    ]


@app.get("/api/rules")
def list_rules(request: Request, db: Session = Depends(_get_db)):
    runtime: LiveRuntime = request.app.state.runtime
    statuses = runtime.rules.statuses()
    records = db.scalars(
        select(AlertRule).options(selectinload(AlertRule.classes)).order_by(AlertRule.id)
    ).all()
    return [_serialize_rule(rule, statuses) for rule in records]


def _validate_rule_classes(db: Session, class_names: list[str]) -> None:
    settings = db.get(ModelSettings, 1)
    if settings is None or settings.active_model_id is None:
        raise HTTPException(status_code=409, detail="先に推論モデルを有効化してください")
    available = {
        item.class_name
        for item in db.scalars(
            select(ModelClass).where(ModelClass.model_id == settings.active_model_id)
        ).all()
    }
    unknown = sorted(set(class_names) - available)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"有効モデルに存在しないクラスです: {', '.join(unknown)}",
        )


@app.post("/api/rules", status_code=201)
def create_rule(payload: RulePayload, request: Request, db: Session = Depends(_get_db)):
    _validate_rule_classes(db, payload.class_names)
    rule = AlertRule(
        name=payload.name,
        enabled=payload.enabled,
        detection_duration_ms=payload.detection_duration_ms,
        release_duration_ms=payload.release_duration_ms,
        cooldown_ms=payload.cooldown_ms,
        classes=[AlertRuleClass(class_name=name) for name in payload.class_names],
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    request.app.state.runtime.configure_rules(_rule_configuration(db))
    return _serialize_rule(rule, request.app.state.runtime.rules.statuses())


@app.put("/api/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: RulePayload,
    request: Request,
    db: Session = Depends(_get_db),
):
    _validate_rule_classes(db, payload.class_names)
    rule = _get_or_404(db, AlertRule, rule_id, "ルール")
    rule.name = payload.name
    rule.enabled = payload.enabled
    rule.detection_duration_ms = payload.detection_duration_ms
    rule.release_duration_ms = payload.release_duration_ms
    rule.cooldown_ms = payload.cooldown_ms
    existing_classes = {item.class_name: item for item in rule.classes}
    requested_classes = set(payload.class_names)
    for class_name, item in existing_classes.items():
        if class_name not in requested_classes:
            rule.classes.remove(item)
    for class_name in payload.class_names:
        if class_name not in existing_classes:
            rule.classes.append(AlertRuleClass(class_name=class_name))
    db.commit()
    db.refresh(rule)
    request.app.state.runtime.configure_rules(_rule_configuration(db))
    return _serialize_rule(rule, request.app.state.runtime.rules.statuses())


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, db: Session = Depends(_get_db)):
    rule = _get_or_404(db, AlertRule, rule_id, "ルール")
    db.delete(rule)
    db.commit()
    request.app.state.runtime.configure_rules(_rule_configuration(db))
    return {"deleted": True, "id": rule_id}


@app.get("/api/alerts")
def list_alerts(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(_get_db),
):
    records = db.scalars(
        select(AlertEvent)
        .options(selectinload(AlertEvent.frame))
        .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": event.id,
                "rule_id": event.rule_id,
                "model_id": event.model_id,
                "rule_name": event.rule_name_snapshot,
                "detected_classes": json.loads(event.detected_classes_json),
                "event_type": event.event_type,
                "source_type": event.source_type,
                "video_id": event.video_id,
                "media_time_ms": event.media_time_ms,
                "model_name": (
                    event.frame.model_name_snapshot if event.frame is not None else None
                ),
                "alert_frame_id": event.alert_frame_id,
                "frame_id": event.frame.frame_id if event.frame is not None else None,
                "has_frame": event.frame is not None,
                "created_at": _isoformat_utc(event.created_at),
            }
            for event in records
        ],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/alerts/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(_get_db)):
    event = db.scalar(
        select(AlertEvent)
        .options(
            selectinload(AlertEvent.frame).selectinload(AlertFrame.detections)
        )
        .where(AlertEvent.id == alert_id)
    )
    if event is None:
        raise HTTPException(status_code=404, detail="アラート履歴が見つかりません")
    frame = event.frame
    return {
        "id": event.id,
        "rule_id": event.rule_id,
        "rule_name": event.rule_name_snapshot,
        "detected_classes": json.loads(event.detected_classes_json),
        "event_type": event.event_type,
        "source_type": event.source_type,
        "video_id": event.video_id,
        "media_time_ms": event.media_time_ms,
        "model_id": event.model_id,
        "model_name": frame.model_name_snapshot if frame is not None else None,
        "created_at": _isoformat_utc(event.created_at),
        "frame": (
            {
                "id": frame.id,
                "source_session_id": frame.source_session_id,
                "frame_id": frame.frame_id,
                "captured_at": _isoformat_utc(frame.captured_at),
                "source_type": frame.source_type,
                "video_id": frame.video_id,
                "media_time_ms": frame.media_time_ms,
                "model_name": frame.model_name_snapshot,
                "width": frame.width,
                "height": frame.height,
                "detections": [
                    {
                        "class_id": detection.class_id,
                        "class_name": detection.class_name,
                        "confidence": detection.confidence,
                        "bbox": [
                            detection.x1,
                            detection.y1,
                            detection.x2,
                            detection.y2,
                        ],
                    }
                    for detection in frame.detections
                ],
                "image_url": f"/api/alerts/{event.id}/frame",
            }
            if frame is not None
            else None
        ),
    }


@app.get("/api/alerts/{alert_id}/frame")
def get_alert_frame(alert_id: int, db: Session = Depends(_get_db)):
    event = db.get(AlertEvent, alert_id)
    if event is None:
        raise HTTPException(status_code=404, detail="アラート履歴が見つかりません")
    if event.alert_frame_id is None:
        raise HTTPException(status_code=404, detail="保存画像がありません")
    frame = db.get(AlertFrame, event.alert_frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="保存画像が見つかりません")
    return Response(
        content=frame.image_jpeg,
        media_type="image/jpeg",
        headers={"Content-Disposition": f'inline; filename="alert-{alert_id}.jpg"'},
    )


@app.delete("/api/alerts")
def delete_alerts(db: Session = Depends(_get_db)):
    db.execute(delete(AlertEvent))
    unreferenced_frame_ids = select(AlertFrame.id).where(
        ~exists(select(AlertEvent.id).where(AlertEvent.alert_frame_id == AlertFrame.id))
    )
    db.execute(
        delete(AlertFrameDetection).where(
            AlertFrameDetection.alert_frame_id.in_(unreferenced_frame_ids)
        )
    )
    db.execute(delete(AlertFrame).where(AlertFrame.id.in_(unreferenced_frame_ids)))
    db.commit()
    return {"deleted": True}


@app.post("/api/alerts/test")
def test_alert(request: Request):
    request.app.state.runtime.test_alert()
    return {"sent": True}


@app.get("/api/settings")
def get_settings(db: Session = Depends(_get_db)):
    settings = db.get(ModelSettings, 1)
    values = _app_settings(db)
    return {
        "confidence": settings.confidence if settings else 0.4,
        "iou_threshold": settings.iou_threshold if settings else 0.7,
        "imgsz": settings.imgsz if settings else 416,
        "target_fps": settings.target_fps if settings else 5.0,
        **values,
        "max_model_size_bytes": MAX_MODEL_SIZE_BYTES,
        "max_video_size_bytes": MAX_VIDEO_SIZE_BYTES,
    }


@app.patch("/api/settings")
def patch_settings(payload: SettingsPatch, request: Request, db: Session = Depends(_get_db)):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return get_settings(db)
    if any(value is None for value in values.values()):
        raise HTTPException(status_code=422, detail="設定値に null は指定できません")
    model_values = {
        key: values[key]
        for key in ("confidence", "iou_threshold", "imgsz", "target_fps")
        if key in values
    }
    settings = db.get(ModelSettings, 1)
    if settings is None:
        settings = ModelSettings(id=1)
        db.add(settings)
    for key, value in model_values.items():
        setattr(settings, key, value)
    app_setting_keys = (
        "show_boxes",
        "show_labels",
        "show_confidence",
        "audio_enabled",
        "jpeg_quality",
    )
    for key in app_setting_keys:
        if key in values:
            _set_app_setting(db, key, values[key])
    db.commit()
    inference_values = dict(model_values)
    if "jpeg_quality" in values:
        inference_values["jpeg_quality"] = values["jpeg_quality"]
    request.app.state.runtime.set_inference_settings(inference_values)
    return get_settings(db)


@app.websocket("/ws/live")
async def live_websocket(websocket: WebSocket):
    await websocket.accept()
    hub: WebSocketHub = websocket.app.state.hub
    subscriber = hub.subscribe()
    try:
        while True:
            message = await hub.receive(subscriber)
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(subscriber)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
