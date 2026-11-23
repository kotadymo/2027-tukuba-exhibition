from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

import cv2
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .alert_storage import persist_alert_frame_events
from .database import (
    AppSetting,
    BatchJob,
    BatchJobVideo,
    ModelRecord,
    ModelSettings,
    SessionLocal,
    VIDEOS_DIR,
    Video,
    utc_now,
)
from .inference import CUDADeviceError, Detector
from .runtime import RuleEngine


logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"pending", "running"}


class BatchJobConflict(RuntimeError):
    pass


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def serialize_batch_job(
    job: BatchJob,
    include_details: bool = True,
) -> dict[str, Any]:
    videos = sorted(job.videos, key=lambda video: video.order_index) if include_details else []
    current_video = next(
        (video.video_name_snapshot for video in videos if video.status == "running"),
        None,
    )
    elapsed_end = job.finished_at or utc_now()
    elapsed_seconds = None
    if job.started_at is not None:
        start = job.started_at
        end = elapsed_end
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        elapsed_seconds = max(0.0, (end - start).total_seconds())

    progress_percent = None
    if videos and all(video.total_frames is not None and video.total_frames > 0 for video in videos):
        total_frames = sum(video.total_frames or 0 for video in videos)
        processed_frames = sum(video.processed_frames for video in videos)
        if total_frames > 0:
            progress_percent = round(min(100.0, processed_frames * 100 / total_frames), 1)

    result = {
        "id": job.id,
        "status": job.status,
        "model_id": job.model_id,
        "model_name": job.model_name_snapshot,
        "created_at": _isoformat(job.created_at),
        "started_at": _isoformat(job.started_at),
        "finished_at": _isoformat(job.finished_at),
        "total_videos": job.total_videos,
        "completed_videos": job.completed_videos,
        "current_video_name": current_video,
        "processed_frames": job.processed_frames,
        "progress_percent": progress_percent,
        "elapsed_seconds": elapsed_seconds,
        "total_alerts": job.total_alerts,
        "error_message": job.error_message,
    }
    if include_details:
        try:
            result["settings"] = json.loads(job.settings_json)
        except json.JSONDecodeError:
            result["settings"] = {}
        result["videos"] = [
            {
                "id": video.id,
                "video_id": video.video_id,
                "name": video.video_name_snapshot,
                "status": video.status,
                "processed_frames": video.processed_frames,
                "total_frames": video.total_frames,
                "alert_count": video.alert_count,
                "error_message": video.error_message,
                "started_at": _isoformat(video.started_at),
                "finished_at": _isoformat(video.finished_at),
            }
            for video in videos
        ]
    return result


class BatchJobManager:
    def __init__(
        self,
        runtime,
        session_factory: Callable[[], Session] = SessionLocal,
        capture_factory: Callable[[str], Any] = cv2.VideoCapture,
    ):
        self._runtime = runtime
        self._session_factory = session_factory
        self._capture_factory = capture_factory
        self._operation_lock = asyncio.Lock()
        self._state_lock = threading.RLock()
        self._active_job_id: int | None = None
        self._cancel_event: threading.Event | None = None
        self._worker: threading.Thread | None = None
        self._shutdown_requested = False

    @property
    def active_job_id(self) -> int | None:
        with self._state_lock:
            return self._active_job_id

    @property
    def is_active(self) -> bool:
        return self.active_job_id is not None

    @asynccontextmanager
    async def normal_operation(self) -> AsyncIterator[None]:
        async with self._operation_lock:
            if self.is_active:
                raise BatchJobConflict("一括推論ジョブ中はこの操作を実行できません")
            yield

    async def submit(
        self,
        db: Session,
        video_ids: list[int],
        rules: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        async with self._operation_lock:
            if self.is_active:
                raise BatchJobConflict("一括推論ジョブは同時に1件だけ実行できます")
            existing_job = db.scalar(
                select(BatchJob.id).where(BatchJob.status.in_(ACTIVE_STATUSES)).limit(1)
            )
            if existing_job is not None:
                raise BatchJobConflict("実行中または実行待ちの一括推論ジョブがあります")

            source = self._runtime.source_snapshot()
            if source["source_type"] == "video" and source["video_status"] == "playing":
                raise BatchJobConflict("動画の通常再生を停止してから一括推論を開始してください")
            if source["source_type"] == "insta360" and (
                source.get("connect_requested")
                or source["insta360_status"] in {"connecting", "connected"}
            ):
                raise BatchJobConflict("Insta360 ライブ接続を切断してから一括推論を開始してください")

            runtime_status = self._runtime.batch_runtime_status()
            if runtime_status["transition_pending"]:
                raise BatchJobConflict("モデルまたは推論デバイスの切り替え中です")
            detector = self._runtime.inference.current_detector()
            if not runtime_status["model_loaded"] or detector is None:
                raise BatchJobConflict("一括推論を開始するには有効なYOLOモデルが必要です")

            settings = db.get(ModelSettings, 1)
            if settings is None or settings.active_model_id is None:
                raise BatchJobConflict("有効なYOLOモデルが登録されていません")
            if runtime_status["model_id"] != settings.active_model_id:
                raise BatchJobConflict("有効モデルの読み込み状態が更新中です")
            model = db.get(ModelRecord, settings.active_model_id)
            if model is None:
                raise BatchJobConflict("有効なYOLOモデルが見つかりません")

            videos = db.scalars(select(Video).where(Video.id.in_(video_ids))).all()
            video_by_id = {video.id: video for video in videos}
            missing_ids = [video_id for video_id in video_ids if video_id not in video_by_id]
            if missing_ids:
                raise BatchJobConflict(
                    f"登録済み動画が見つかりません: {', '.join(map(str, missing_ids))}"
                )

            jpeg_setting = db.get(AppSetting, "jpeg_quality")
            jpeg_quality = (
                int(json.loads(jpeg_setting.value_json))
                if jpeg_setting is not None
                else 80
            )
            snapshot = {
                "confidence": settings.confidence,
                "iou_threshold": settings.iou_threshold,
                "imgsz": settings.imgsz,
                "requested_device": runtime_status["requested_device"],
                "effective_device": detector.device,
                "fallback_reason": runtime_status.get("fallback_reason"),
                "jpeg_quality": jpeg_quality,
                "model_id": model.id,
                "model_name": model.name,
                "rules": json.loads(
                    json.dumps(
                        rules() if callable(rules) else rules,
                        ensure_ascii=False,
                    )
                ),
            }
            job = BatchJob(
                status="pending",
                model_id=model.id,
                model_name_snapshot=model.name,
                settings_json=json.dumps(snapshot, ensure_ascii=False),
                total_videos=len(video_ids),
                videos=[
                    BatchJobVideo(
                        order_index=index,
                        video_id=video.id,
                        video_name_snapshot=video.name,
                        video_stored_filename_snapshot=video.stored_filename,
                        fps_snapshot=video.fps,
                        status="pending",
                    )
                    for index, video in enumerate(
                        (video_by_id[video_id] for video_id in video_ids)
                    )
                ],
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            cancel_event = threading.Event()
            worker = threading.Thread(
                target=self._run_job,
                args=(job.id, snapshot, cancel_event),
                name=f"batch-inference-{job.id}",
                daemon=True,
            )
            with self._state_lock:
                self._active_job_id = job.id
                self._cancel_event = cancel_event
                self._worker = worker
                self._shutdown_requested = False
            try:
                worker.start()
            except Exception as exc:
                logger.exception("Could not start batch inference worker")
                with self._session_factory.begin() as worker_db:
                    failed_job = worker_db.get(BatchJob, job.id)
                    if failed_job is not None:
                        failed_job.status = "failed"
                        failed_job.finished_at = utc_now()
                        failed_job.error_message = f"ジョブを開始できません: {exc}"
                        for item in failed_job.videos:
                            item.status = "failed"
                            item.finished_at = utc_now()
                            item.error_message = failed_job.error_message
                self._clear_active(job.id)
                raise RuntimeError(f"一括推論ジョブを開始できません: {exc}") from exc
            return serialize_batch_job(job)

    def request_cancel(self, job_id: int) -> bool:
        with self._state_lock:
            if self._active_job_id != job_id or self._cancel_event is None:
                return False
            self._cancel_event.set()
            return True

    def stop(self) -> None:
        with self._state_lock:
            self._shutdown_requested = True
            if self._cancel_event is not None:
                self._cancel_event.set()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=10)

    def _should_stop(self, cancel_event: threading.Event) -> bool:
        with self._state_lock:
            return self._shutdown_requested or cancel_event.is_set()

    def _stop_status(self, cancel_event: threading.Event) -> str:
        with self._state_lock:
            if self._shutdown_requested:
                return "interrupted"
        return "cancelled" if cancel_event.is_set() else "completed"

    def _run_job(
        self,
        job_id: int,
        snapshot: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        inference_paused = False
        try:
            self._runtime.inference.pause()
            inference_paused = True
            detector = self._runtime.inference.current_detector()
            if detector is None:
                raise RuntimeError("一括推論用のYOLOモデルが読み込まれていません")
            if detector.device != snapshot["effective_device"]:
                raise RuntimeError("ジョブ開始後に推論デバイスが変更されました")

            with self._session_factory.begin() as db:
                job = db.get(BatchJob, job_id)
                if job is None:
                    raise RuntimeError("一括推論ジョブが見つかりません")
                job.status = "running"
                job.started_at = utc_now()

            rule_engine = RuleEngine()
            rule_engine.configure(snapshot["rules"])
            rule_engine.set_model_classes(set(detector.names.values()))
            with self._session_factory() as db:
                videos = db.scalars(
                    select(BatchJobVideo)
                    .where(BatchJobVideo.batch_job_id == job_id)
                    .order_by(BatchJobVideo.order_index)
                ).all()
                video_specs = [
                    {
                        "row_id": item.id,
                        "video_id": item.video_id,
                        "name": item.video_name_snapshot,
                        "filename": item.video_stored_filename_snapshot,
                        "fps": item.fps_snapshot,
                    }
                    for item in videos
                ]

            for video in video_specs:
                if self._should_stop(cancel_event):
                    break
                detector = self._runtime.inference.current_detector() or detector
                rule_engine.reset(clear_cooldown=True, reason="batch_video_changed")
                try:
                    self._process_video(
                        job_id,
                        video,
                        detector,
                        rule_engine,
                        snapshot,
                        cancel_event,
                    )
                except CUDADeviceError:
                    self._finish_video(
                        job_id,
                        video["row_id"],
                        "failed",
                        "CUDA 推論エラーが発生したためジョブを停止しました",
                    )
                    raise
                except Exception as exc:
                    logger.exception(
                        "Batch inference failed for video %s in job %s",
                        video["video_id"],
                        job_id,
                    )
                    self._finish_video(job_id, video["row_id"], "failed", str(exc))

            self._finalize_job(job_id, cancel_event)
        except Exception as exc:
            logger.exception("Batch inference job %s failed", job_id)
            self._finalize_job(job_id, cancel_event, fatal_error=str(exc))
        finally:
            try:
                if inference_paused:
                    self._runtime.inference.resume()
            except Exception:
                logger.exception("Could not resume live inference after batch job %s", job_id)
            finally:
                self._clear_active(job_id)

    def _process_video(
        self,
        job_id: int,
        video: dict[str, Any],
        detector: Detector,
        rule_engine: RuleEngine,
        snapshot: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        path = VIDEOS_DIR / video["filename"]
        capture = self._capture_factory(str(path))
        processed_frames = 0
        last_progress_at = time.monotonic()
        source_session_id = uuid.uuid4().hex
        try:
            with self._session_factory.begin() as db:
                row = db.get(BatchJobVideo, video["row_id"])
                if row is not None:
                    row.status = "running"
                    row.started_at = utc_now()
                    row.processed_frames = 0
                    row.error_message = None
            if not capture.isOpened():
                raise RuntimeError("動画ファイルを開けません")

            frame_count_value = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            total_frames = (
                max(1, int(round(frame_count_value)))
                if math.isfinite(frame_count_value) and frame_count_value > 0
                else None
            )
            fps_value = capture.get(cv2.CAP_PROP_FPS)
            fps = (
                float(fps_value)
                if math.isfinite(fps_value) and fps_value > 0
                else video["fps"]
            )
            with self._session_factory.begin() as db:
                row = db.get(BatchJobVideo, video["row_id"])
                if row is not None:
                    row.total_frames = total_frames

            while not self._should_stop(cancel_event):
                success, frame = capture.read()
                if not success or frame is None:
                    if total_frames is not None and processed_frames < total_frames:
                        raise RuntimeError(
                            f"動画を最後までデコードできません "
                            f"({processed_frames}/{total_frames} フレーム)"
                        )
                    break

                height, width = frame.shape[:2]
                if fps is not None and math.isfinite(fps) and fps > 0:
                    media_time_ms = int(round(processed_frames * 1000 / fps))
                else:
                    capture_position_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
                    if not math.isfinite(capture_position_ms) or capture_position_ms < 0:
                        raise RuntimeError("動画内の再生位置を取得できません")
                    media_time_ms = int(round(capture_position_ms))
                frame_time = media_time_ms / 1000
                try:
                    detections = detector.predict(
                        frame,
                        snapshot["confidence"],
                        snapshot["iou_threshold"],
                        snapshot["imgsz"],
                    )
                except CUDADeviceError as exc:
                    if snapshot["requested_device"] != "auto":
                        raise
                    try:
                        detector, fallback_reason = (
                            self._runtime.fallback_to_cpu_for_batch(str(exc))
                        )
                    except Exception as fallback_error:
                        raise CUDADeviceError(
                            "CUDA推論に失敗し、AutoモードのCPUフォールバックも"
                            f"できません: {fallback_error}"
                        ) from fallback_error
                    snapshot["effective_device"] = detector.device
                    snapshot["fallback_reason"] = fallback_reason
                    self._update_settings_snapshot(job_id, snapshot)
                    detections = detector.predict(
                        frame,
                        snapshot["confidence"],
                        snapshot["iou_threshold"],
                        snapshot["imgsz"],
                    )
                _, generated_events = rule_engine.evaluate(
                    detections,
                    frame_time,
                    "video",
                    video["video_id"],
                    media_time_ms,
                    snapshot["model_id"],
                )
                alert_events = [
                    event
                    for event in generated_events
                    if event.get("type") == "alert_started" and not event.get("test")
                ]
                processed_frames += 1
                if alert_events:
                    encoded, jpeg = cv2.imencode(
                        ".jpg",
                        frame,
                        [cv2.IMWRITE_JPEG_QUALITY, snapshot["jpeg_quality"]],
                    )
                    if not encoded:
                        raise RuntimeError("発報フレームをJPEGへ変換できません")
                    with self._session_factory.begin() as db:
                        persisted = persist_alert_frame_events(
                            db,
                            events=alert_events,
                            source_session_id=source_session_id,
                            frame_id=processed_frames,
                            source_type="video",
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            video_id=video["video_id"],
                            media_time_ms=media_time_ms,
                            model_id=snapshot["model_id"],
                            model_name=snapshot["model_name"],
                            width=width,
                            height=height,
                            image_jpeg=jpeg.tobytes(),
                            detections=detections,
                            batch_job_id=job_id,
                        )
                        job = db.get(BatchJob, job_id)
                        job_video = db.get(BatchJobVideo, video["row_id"])
                        if job is not None:
                            job.total_alerts += len(persisted)
                        if job_video is not None:
                            job_video.alert_count += len(persisted)

                now = time.monotonic()
                if processed_frames % 10 == 0 or now - last_progress_at >= 0.5:
                    self._update_progress(job_id, video["row_id"], processed_frames)
                    last_progress_at = now

            if self._should_stop(cancel_event):
                self._finish_video(
                    job_id,
                    video["row_id"],
                    self._stop_status(cancel_event),
                    None,
                )
            else:
                self._finish_video(job_id, video["row_id"], "completed", None)
        finally:
            try:
                self._update_progress(job_id, video["row_id"], processed_frames)
            finally:
                capture.release()

    def _update_progress(self, job_id: int, video_row_id: int, processed_frames: int) -> None:
        with self._session_factory.begin() as db:
            video = db.get(BatchJobVideo, video_row_id)
            job = db.get(BatchJob, job_id)
            if video is not None:
                video.processed_frames = processed_frames
            if job is not None:
                total_processed = db.scalar(
                    select(func.coalesce(func.sum(BatchJobVideo.processed_frames), 0))
                    .where(BatchJobVideo.batch_job_id == job_id)
                )
                job.processed_frames = int(total_processed or 0)

    def _update_settings_snapshot(
        self,
        job_id: int,
        snapshot: dict[str, Any],
    ) -> None:
        with self._session_factory.begin() as db:
            job = db.get(BatchJob, job_id)
            if job is not None:
                job.settings_json = json.dumps(snapshot, ensure_ascii=False)

    def _finish_video(
        self,
        job_id: int,
        video_row_id: int,
        status: str,
        error_message: str | None,
    ) -> None:
        with self._session_factory.begin() as db:
            video = db.get(BatchJobVideo, video_row_id)
            job = db.get(BatchJob, job_id)
            if video is not None:
                video.status = status
                video.error_message = error_message
                video.finished_at = utc_now()
            if job is not None:
                job.completed_videos = int(
                    db.scalar(
                        select(func.count(BatchJobVideo.id)).where(
                            BatchJobVideo.batch_job_id == job_id,
                            BatchJobVideo.status == "completed",
                        )
                    )
                    or 0
                )

    def _finalize_job(
        self,
        job_id: int,
        cancel_event: threading.Event,
        fatal_error: str | None = None,
    ) -> None:
        with self._session_factory.begin() as db:
            job = db.get(BatchJob, job_id)
            if job is None:
                return
            videos = db.scalars(
                select(BatchJobVideo).where(BatchJobVideo.batch_job_id == job_id)
            ).all()
            stop_status = self._stop_status(cancel_event)
            if fatal_error:
                for video in videos:
                    if video.status in ACTIVE_STATUSES:
                        video.status = "failed"
                        video.error_message = fatal_error
                        video.finished_at = utc_now()
            elif stop_status in {"cancelled", "interrupted"}:
                for video in videos:
                    if video.status == "pending":
                        video.status = stop_status
                        video.finished_at = utc_now()

            job.completed_videos = sum(video.status == "completed" for video in videos)
            failures = [video for video in videos if video.status == "failed"]
            successes = job.completed_videos
            if stop_status == "interrupted":
                job.status = "interrupted"
            elif stop_status == "cancelled":
                job.status = "cancelled"
            elif failures:
                job.status = "completed_with_errors" if successes else "failed"
            elif fatal_error:
                job.status = "completed_with_errors" if successes else "failed"
            else:
                job.status = "completed"
            if fatal_error:
                job.error_message = fatal_error
            elif failures:
                job.error_message = "\n".join(
                    f"{video.video_name_snapshot}: {video.error_message or '処理に失敗しました'}"
                    for video in failures
                )
            elif job.status in {"cancelled", "interrupted"} and not job.error_message:
                job.error_message = (
                    "Backend の停止により中断されました"
                    if job.status == "interrupted"
                    else "ユーザーによりキャンセルされました"
                )
            job.finished_at = utc_now()

    def _clear_active(self, job_id: int) -> None:
        with self._state_lock:
            if self._active_job_id == job_id:
                self._active_job_id = None
                self._cancel_event = None
                self._worker = None
