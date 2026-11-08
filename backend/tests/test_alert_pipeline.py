import json
import struct
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import AlertEvent, AlertFrame, AlertFrameDetection, Base
from app.inference import Detection
from app.runtime import FrameInput, InferenceWorker, RuleEngine


class AlertPipelineTests(unittest.TestCase):
    def test_inference_persists_alert_frame_and_publishes_linked_event(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        test_session_local = sessionmaker(bind=engine, expire_on_commit=False)
        hub = _RecordingHub()
        rules = RuleEngine()
        rules.configure(
            [
                {
                    "id": 1,
                    "name": "hand + tool",
                    "class_names": ["hand", "tool"],
                    "enabled": True,
                    "detection_duration_ms": 0,
                    "release_duration_ms": 1000,
                    "cooldown_ms": 10000,
                }
            ]
        )
        worker = InferenceWorker(rules, hub)
        worker.set_settings({"target_fps": 60, "jpeg_quality": 80})
        worker.set_model(_FakeDetector(), 7, "fake-model")

        try:
            with patch("app.runtime.SessionLocal", test_session_local):
                worker.start()
                worker.offer(_frame(1, 0))
                self.assertTrue(hub.wait_for_frame_count(1))
                worker.offer(_frame(2, 100))
                self.assertTrue(hub.alert_published.wait(3))
        finally:
            worker.stop()

        alert = next(event for event in hub.events if event.get("type") == "alert_started")
        self.assertEqual(alert["frame_id"], 2)
        self.assertEqual(alert["source_session_id"], "session-pipeline")
        self.assertIsNotNone(alert["alert_id"])

        frame_packet = hub.frames[-1]
        header_length = struct.unpack("<I", frame_packet[:4])[0]
        header = json.loads(frame_packet[4 : 4 + header_length])
        self.assertEqual(header["frame_id"], 2)
        self.assertEqual(header["source_session_id"], "session-pipeline")

        with test_session_local() as db:
            frames = db.scalars(select(AlertFrame)).all()
            events = db.scalars(select(AlertEvent)).all()
            detections = db.scalars(select(AlertFrameDetection)).all()
            self.assertEqual(len(frames), 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].alert_frame_id, frames[0].id)
            self.assertEqual([item.class_name for item in detections], ["hand", "tool"])
            self.assertEqual(frames[0].image_jpeg[:2], b"\xff\xd8")
            self.assertEqual(frames[0].source_session_id, "session-pipeline")
            self.assertEqual(frames[0].frame_id, 2)

    def test_storage_failure_is_reported_without_stopping_frame_stream(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        test_session_local = sessionmaker(bind=engine, expire_on_commit=False)
        hub = _RecordingHub()
        rules = RuleEngine()
        rules.configure(
            [
                {
                    "id": 1,
                    "name": "hand + tool",
                    "class_names": ["hand", "tool"],
                    "enabled": True,
                    "detection_duration_ms": 0,
                    "release_duration_ms": 1000,
                    "cooldown_ms": 10000,
                }
            ]
        )
        worker = InferenceWorker(rules, hub)
        worker.set_settings({"target_fps": 60})
        worker.set_model(_FakeDetector(), 7, "fake-model")

        try:
            with (
                patch("app.runtime.SessionLocal", test_session_local),
                patch(
                    "app.runtime.persist_alert_frame_events",
                    side_effect=RuntimeError("disk unavailable"),
                ),
            ):
                worker.start()
                worker.offer(_frame(1, 0))
                self.assertTrue(hub.wait_for_frame_count(1))
                worker.offer(_frame(2, 100))
                self.assertTrue(hub.alert_published.wait(3))
                self.assertTrue(hub.storage_error_published.wait(3))
                self.assertTrue(hub.wait_for_frame_count(2))
        finally:
            worker.stop()

        self.assertTrue(any(event.get("type") == "alert_save_error" for event in hub.events))
        with test_session_local() as db:
            self.assertEqual(db.scalars(select(AlertFrame)).all(), [])
            self.assertEqual(db.scalars(select(AlertEvent)).all(), [])


def _frame(frame_id, media_time_ms):
    return FrameInput(
        frame_id=frame_id,
        source_session_id="session-pipeline",
        image=np.zeros((32, 48, 3), dtype=np.uint8),
        source_type="video",
        timestamp=datetime.now().astimezone().isoformat(),
        captured_at=time.monotonic(),
        video_id=11,
        media_time_ms=media_time_ms,
    )


class _FakeDetector:
    names = {0: "hand", 1: "tool"}

    def predict(self, _frame, confidence, iou_threshold, imgsz):
        self.last_settings = (confidence, iou_threshold, imgsz)
        return [
            Detection(0, "hand", 0.94, [1, 2, 20, 25]),
            Detection(1, "tool", 0.82, [22, 3, 45, 28]),
        ]


class _RecordingHub:
    def __init__(self):
        self.events = []
        self.frames = []
        self._condition = threading.Condition()
        self.alert_published = threading.Event()
        self.storage_error_published = threading.Event()

    def publish_event(self, event):
        with self._condition:
            self.events.append(event)
            if event.get("type") == "alert_started":
                self.alert_published.set()
            if event.get("type") == "alert_save_error":
                self.storage_error_published.set()
            self._condition.notify_all()

    def publish_frame(self, frame):
        with self._condition:
            self.frames.append(frame)
            self._condition.notify_all()

    def wait_for_frame_count(self, count):
        deadline = time.monotonic() + 3
        with self._condition:
            while len(self.frames) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True


if __name__ == "__main__":
    unittest.main()
