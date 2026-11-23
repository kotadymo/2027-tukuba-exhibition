import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import selectinload, sessionmaker
from sqlalchemy.engine import URL

from app.batch_jobs import BatchJobConflict, BatchJobManager
from app.database import (
    AlertEvent,
    AlertFrame,
    AlertRule,
    AlertRuleClass,
    AppSetting,
    Base,
    BatchJob,
    ModelRecord,
    ModelSettings,
    Video,
    HazardReason,
)
from app.education import generate_quiz_questions
from app.inference import CUDADeviceError, Detection
from app.main import _get_db, app


class _FakeDetector:
    device = "cpu"
    names = {0: "hand", 1: "tool"}

    def __init__(self, delay=0):
        self.delay = delay

    def predict(self, frame, confidence, iou_threshold, imgsz):
        if self.delay:
            time.sleep(self.delay)
        return [
            Detection(0, "hand", 0.95, [10, 10, 30, 30]),
            Detection(1, "tool", 0.92, [32, 12, 52, 48]),
        ]


class _CudaErrorDetector(_FakeDetector):
    device = "cuda:0"

    def predict(self, frame, confidence, iou_threshold, imgsz):
        raise CUDADeviceError("CUDA inference failed")


class _FakeInference:
    def __init__(self, detector):
        self.detector = detector
        self.pause_count = 0
        self.resume_count = 0

    def pause(self):
        self.pause_count += 1

    def resume(self):
        self.resume_count += 1

    def current_detector(self):
        return self.detector


class _FakeRuntime:
    def __init__(
        self,
        detector=None,
        video_status="paused",
        requested_device="auto",
        effective_device=None,
    ):
        self.inference = _FakeInference(detector or _FakeDetector())
        self.video_status = video_status
        self.requested_device = requested_device
        self.effective_device = effective_device or self.inference.detector.device
        self.fallback_calls = 0

    def source_snapshot(self):
        return {
            "source_type": "video",
            "video_status": self.video_status,
            "connect_requested": False,
            "insta360_status": "disconnected",
        }

    def batch_runtime_status(self):
        return {
            "model_id": 1,
            "model_name": "demo",
            "model_loaded": self.inference.current_detector() is not None,
            "requested_device": self.requested_device,
            "effective_device": self.effective_device,
            "fallback_reason": None,
            "transition_pending": False,
        }

    def fallback_to_cpu_for_batch(self, _error_message):
        self.fallback_calls += 1
        detector = _FakeDetector()
        self.inference.detector = detector
        return detector, "CUDA推論に失敗したためCPUへ切り替えました"


class _FakeCapture:
    def __init__(self, frames, opened=True):
        self.frames = frames
        self.opened = opened
        self.index = 0
        self.released = False

    def isOpened(self):
        return self.opened

    def get(self, property_id):
        import cv2

        if property_id == cv2.CAP_PROP_FRAME_COUNT:
            return len(self.frames)
        if property_id == cv2.CAP_PROP_FPS:
            return 10
        if property_id == cv2.CAP_PROP_POS_MSEC:
            return self.index * 100
        return 0

    def read(self):
        if self.index >= len(self.frames):
            return False, None
        frame = self.frames[self.index]
        self.index += 1
        return True, frame

    def release(self):
        self.released = True


class BatchJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video_dir = self.root / "videos"
        self.video_dir.mkdir()
        self.engine = create_engine(
            URL.create("sqlite", database=str(self.root / "batch.db")),
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )
        self.path_patch = patch("app.batch_jobs.VIDEOS_DIR", self.video_dir)
        self.path_patch.start()
        with self.session_factory.begin() as db:
            model = ModelRecord(
                id=1,
                name="demo",
                original_filename="demo.pt",
                stored_filename="demo.pt",
                file_size=10,
            )
            db.add(model)
            db.add(ModelSettings(id=1, active_model_id=1))
            db.add(AppSetting(key="jpeg_quality", value_json="80"))

    def tearDown(self):
        self.path_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _add_videos(self, *names):
        with self.session_factory.begin() as db:
            rows = [
                Video(
                    name=name,
                    original_filename=f"{name}.mp4",
                    stored_filename=f"{name}.mp4",
                    file_size=100,
                    fps=10,
                    duration_ms=1000,
                )
                for name in names
            ]
            db.add_all(rows)
            db.flush()
            return [row.id for row in rows]

    @staticmethod
    def _rules():
        return [
            {
                "id": 1,
                "name": "hand + tool",
                "class_names": ["hand", "tool"],
                "enabled": True,
                "detection_duration_ms": 0,
                "release_duration_ms": 1000,
                "cooldown_ms": 0,
                "education_target_class_name": "hand",
                "hazard_reason_id": 12,
                "correct_reason": "手袋が回転部に巻き込まれるため",
                "wrong_reason": "作業台の色が明るいため",
                "explanation": "回転部に巻き込まれる危険があります。",
            }
        ]

    def _wait_for_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.session_factory() as db:
                job = db.scalar(
                    select(BatchJob)
                    .options(selectinload(BatchJob.videos))
                    .where(BatchJob.id == job_id)
                )
                if job.status not in {"pending", "running"}:
                    return job
            time.sleep(0.01)
        self.fail(f"ジョブ {job_id} が完了しませんでした")

    def test_batch_alerts_are_saved_and_available_to_quiz(self):
        video_id = self._add_videos("hazard")
        frames = [np.zeros((64, 64, 3), dtype=np.uint8) for _ in range(3)]
        captures = []

        def capture_factory(_path):
            capture = _FakeCapture(frames)
            captures.append(capture)
            return capture

        runtime = _FakeRuntime()
        manager = BatchJobManager(runtime, self.session_factory, capture_factory)
        with self.session_factory() as db:
            response = asyncio.run(manager.submit(db, video_id, self._rules()))
        job = self._wait_for_job(response["id"])

        self.assertEqual(job.status, "completed")
        self.assertEqual(job.processed_frames, 3)
        self.assertEqual(job.total_alerts, 1)
        self.assertEqual(json.loads(job.settings_json)["effective_device"], "cpu")
        self.assertEqual(runtime.inference.pause_count, 1)
        self.assertEqual(runtime.inference.resume_count, 1)
        self.assertTrue(captures[0].released)

        with self.session_factory() as db:
            alert = db.scalar(
                select(AlertEvent).where(AlertEvent.batch_job_id == job.id)
            )
            self.assertIsNotNone(alert)
            self.assertEqual(alert.video_id, video_id[0])
            self.assertEqual(alert.media_time_ms, 100)
            self.assertEqual(alert.hazard_reason_id, 12)
            frame = db.get(AlertFrame, alert.alert_frame_id)
            self.assertTrue(frame.image_jpeg)
            self.assertEqual(len(frame.detections), 2)
            quiz = generate_quiz_questions(db, 3)
            self.assertEqual(quiz["available_question_count"], 1)
            self.assertEqual(quiz["questions"][0]["hazard_reason_id"], 12)

    def test_video_error_does_not_prevent_later_video_processing(self):
        video_ids = self._add_videos("broken", "working")
        processed_paths = []
        frames = [np.zeros((64, 64, 3), dtype=np.uint8)]

        def capture_factory(path):
            name = Path(path).name
            processed_paths.append(name)
            return _FakeCapture(frames, opened=name != "broken.mp4")

        manager = BatchJobManager(_FakeRuntime(), self.session_factory, capture_factory)
        with self.session_factory() as db:
            response = asyncio.run(manager.submit(db, video_ids, []))
        job = self._wait_for_job(response["id"])

        self.assertEqual(job.status, "completed_with_errors")
        self.assertEqual(processed_paths, ["broken.mp4", "working.mp4"])
        self.assertEqual([video.status for video in job.videos], ["failed", "completed"])

    def test_auto_device_falls_back_to_cpu_after_cuda_inference_error(self):
        video_id = self._add_videos("auto-fallback")
        runtime = _FakeRuntime(_CudaErrorDetector())
        manager = BatchJobManager(
            runtime,
            self.session_factory,
            lambda _path: _FakeCapture(
                [np.zeros((32, 32, 3), dtype=np.uint8)]
            ),
        )
        with self.session_factory() as db:
            response = asyncio.run(manager.submit(db, video_id, []))

        job = self._wait_for_job(response["id"])
        snapshot = json.loads(job.settings_json)
        self.assertEqual(job.status, "completed")
        self.assertEqual(runtime.fallback_calls, 1)
        self.assertEqual(snapshot["requested_device"], "auto")
        self.assertEqual(snapshot["effective_device"], "cpu")
        self.assertIn("CPUへ切り替え", snapshot["fallback_reason"])

    def test_explicit_cuda_device_does_not_fall_back_to_cpu(self):
        video_id = self._add_videos("cuda-failure")
        runtime = _FakeRuntime(
            _CudaErrorDetector(),
            requested_device="cuda",
            effective_device="cuda:0",
        )
        manager = BatchJobManager(
            runtime,
            self.session_factory,
            lambda _path: _FakeCapture(
                [np.zeros((32, 32, 3), dtype=np.uint8)]
            ),
        )
        with self.session_factory() as db:
            response = asyncio.run(manager.submit(db, video_id, []))

        job = self._wait_for_job(response["id"])
        self.assertEqual(job.status, "failed")
        self.assertEqual(runtime.fallback_calls, 0)

    def test_cancel_stops_after_current_frame_and_live_operation_is_blocked(self):
        video_id = self._add_videos("slow")
        frames = [np.zeros((32, 32, 3), dtype=np.uint8) for _ in range(30)]
        manager = BatchJobManager(
            _FakeRuntime(_FakeDetector(delay=0.03)),
            self.session_factory,
            lambda _path: _FakeCapture(frames),
        )

        async def submit_and_cancel():
            with self.session_factory() as db:
                response = await manager.submit(db, video_id, [])
            with self.assertRaises(BatchJobConflict):
                async with manager.normal_operation():
                    pass
            self.assertTrue(manager.request_cancel(response["id"]))
            return response["id"]

        job_id = asyncio.run(submit_and_cancel())
        job = self._wait_for_job(job_id)

        self.assertEqual(job.status, "cancelled")
        self.assertLess(job.processed_frames, len(frames))

    def test_live_playback_prevents_job_creation(self):
        video_id = self._add_videos("playing")
        manager = BatchJobManager(
            _FakeRuntime(video_status="playing"),
            self.session_factory,
            lambda _path: _FakeCapture([]),
        )
        with self.session_factory() as db:
            with self.assertRaises(BatchJobConflict):
                asyncio.run(manager.submit(db, video_id, []))
            self.assertEqual(db.scalar(select(BatchJob.id)), None)

    def test_batch_job_rest_api_returns_progress_and_job_alerts(self):
        video_id = self._add_videos("api-hazard")
        frames = [np.zeros((64, 64, 3), dtype=np.uint8) for _ in range(3)]
        reason = HazardReason(
            id=12,
            name="hand hazard",
            correct_reason="手が回転部に巻き込まれるため",
            wrong_reason="工具の色が明るいため",
            explanation="回転部に巻き込まれる危険があります。",
        )
        rule = AlertRule(
            id=1,
            name="hand + tool",
            enabled=True,
            detection_duration_ms=0,
            release_duration_ms=1000,
            cooldown_ms=0,
            education_target_class_name="hand",
            hazard_reason_id=12,
            classes=[
                AlertRuleClass(class_name="hand"),
                AlertRuleClass(class_name="tool"),
            ],
        )
        with self.session_factory.begin() as db:
            db.add_all([reason, rule])

        previous_dependency = app.dependency_overrides.get(_get_db)
        previous_runtime = getattr(app.state, "runtime", None)
        previous_manager = getattr(app.state, "batch_jobs", None)
        runtime = _FakeRuntime()
        manager = BatchJobManager(
            runtime,
            self.session_factory,
            lambda _path: _FakeCapture(frames),
        )

        def override_get_db():
            with self.session_factory() as db:
                yield db

        app.dependency_overrides[_get_db] = override_get_db
        app.state.runtime = runtime
        app.state.batch_jobs = manager
        client = TestClient(app)
        try:
            created = client.post("/api/batch-jobs", json={"video_ids": video_id})
            self.assertEqual(created.status_code, 202)
            job_id = created.json()["id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                detail = client.get(f"/api/batch-jobs/{job_id}").json()
                if detail["status"] not in {"pending", "running"}:
                    break
                time.sleep(0.01)
            self.assertEqual(detail["status"], "completed")
            self.assertEqual(detail["videos"][0]["processed_frames"], 3)
            alerts = client.get(f"/api/batch-jobs/{job_id}/alerts")
            self.assertEqual(alerts.status_code, 200)
            self.assertEqual(len(alerts.json()["items"]), 1)
            self.assertEqual(alerts.json()["items"][0]["batch_job_id"], job_id)
            self.assertEqual(
                alerts.json()["items"][0]["correct_reason"],
                reason.correct_reason,
            )
        finally:
            manager.stop()
            client.close()
            if previous_dependency is None:
                app.dependency_overrides.pop(_get_db, None)
            else:
                app.dependency_overrides[_get_db] = previous_dependency
            if previous_runtime is None:
                del app.state.runtime
            else:
                app.state.runtime = previous_runtime
            if previous_manager is None:
                del app.state.batch_jobs
            else:
                app.state.batch_jobs = previous_manager


if __name__ == "__main__":
    unittest.main()
