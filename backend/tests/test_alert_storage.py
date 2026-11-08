import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.alert_storage import persist_alert_frame_events
from app.database import (
    AlertEvent,
    AlertFrame,
    AlertFrameDetection,
    Base,
    Insta360Settings,
    SourceSettings,
)
from app.inference import Detection
from app.main import _get_db, app


class AlertStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _events(self):
        timestamp = "2026-11-08T12:00:00+09:00"
        return [
            {
                "type": "alert_started",
                "rule_id": 1,
                "rule_name": "hand + tool",
                "detected_classes": ["hand", "tool"],
                "timestamp": timestamp,
            },
            {
                "type": "alert_started",
                "rule_id": 2,
                "rule_name": "person + tool",
                "detected_classes": ["person", "tool"],
                "timestamp": timestamp,
            },
        ]

    def _persist(self, db, events=None):
        return persist_alert_frame_events(
            db,
            events=self._events() if events is None else events,
            source_session_id="session-1",
            frame_id=27,
            source_type="video",
            timestamp="2026-11-08T12:00:00+09:00",
            video_id=4,
            media_time_ms=1250,
            model_id=3,
            model_name="Exhibition model",
            width=640,
            height=360,
            image_jpeg=b"\xff\xd8jpeg-frame\xff\xd9",
            detections=[
                Detection(0, "hand", 0.94, [10, 20, 100, 120]),
                Detection(1, "tool", 0.87, [120, 40, 300, 240]),
                Detection(2, "background-object", 0.52, [5, 6, 8, 9]),
            ],
        )

    def test_multiple_rules_share_one_frame_and_store_all_detections(self):
        with Session(self.engine) as db:
            with db.begin():
                events = self._persist(db)

            frames = db.scalars(select(AlertFrame)).all()
            records = db.scalars(select(AlertEvent).order_by(AlertEvent.id)).all()
            detections = db.scalars(
                select(AlertFrameDetection).order_by(AlertFrameDetection.id)
            ).all()

            self.assertEqual(len(frames), 1)
            self.assertEqual(len(records), 2)
            self.assertEqual({event.alert_frame_id for event in records}, {frames[0].id})
            self.assertEqual([item.class_name for item in detections], [
                "hand",
                "tool",
                "background-object",
            ])
            self.assertEqual(frames[0].image_jpeg, b"\xff\xd8jpeg-frame\xff\xd9")
            self.assertEqual(frames[0].model_name_snapshot, "Exhibition model")
            self.assertEqual(frames[0].source_type, "video")
            self.assertEqual(frames[0].media_time_ms, 1250)
            self.assertEqual(
                frames[0].captured_at.replace(tzinfo=timezone.utc),
                datetime.fromisoformat("2026-11-08T12:00:00+09:00").astimezone(
                    timezone.utc
                ),
            )
            self.assertEqual(
                [event["frame_id"] for event in events],
                [27, 27],
            )
            self.assertEqual(
                {event["source_session_id"] for event in events},
                {"session-1"},
            )
            self.assertTrue(all(event["alert_id"] for event in events))

    def test_failed_event_write_rolls_back_frame_and_prior_events(self):
        invalid_events = [self._events()[0], {"rule_id": 2, "rule_name": "incomplete"}]
        with Session(self.engine) as db:
            with self.assertRaises(KeyError):
                with db.begin():
                    self._persist(db, invalid_events)

            self.assertEqual(
                db.scalar(select(func.count()).select_from(AlertFrame)),
                0,
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(AlertEvent)),
                0,
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(AlertFrameDetection)),
                0,
            )


class AlertApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            with self.session_factory() as db:
                yield db

        self._previous_dependency = app.dependency_overrides.get(_get_db)
        app.dependency_overrides[_get_db] = override_get_db
        self._previous_runtime = getattr(app.state, "runtime", None)
        app.state.runtime = _FakeRuntime()
        self.client = TestClient(app)
        with self.session_factory.begin() as db:
            db.add(SourceSettings(id=1))
            db.add(Insta360Settings(id=1))
            self.saved_events = persist_alert_frame_events(
                db,
                events=[
                    {
                        "type": "alert_started",
                        "rule_id": 1,
                        "rule_name": "hand + tool",
                        "detected_classes": ["hand", "tool"],
                        "timestamp": "2026-11-08T03:00:00+00:00",
                    },
                    {
                        "type": "alert_started",
                        "rule_id": 2,
                        "rule_name": "person + tool",
                        "detected_classes": ["person", "tool"],
                        "timestamp": "2026-11-08T03:00:00+00:00",
                    },
                ],
                source_session_id="session-api",
                frame_id=38,
                source_type="insta360",
                timestamp="2026-11-08T03:00:00+00:00",
                video_id=None,
                media_time_ms=None,
                model_id=3,
                model_name="API model",
                width=320,
                height=180,
                image_jpeg=b"\xff\xd8api-frame\xff\xd9",
                detections=[
                    Detection(0, "hand", 0.94, [1, 2, 30, 40]),
                    Detection(1, "tool", 0.82, [31, 42, 60, 90]),
                ],
            )

    def tearDown(self):
        self.client.close()
        if self._previous_dependency is None:
            app.dependency_overrides.pop(_get_db, None)
        else:
            app.dependency_overrides[_get_db] = self._previous_dependency
        if self._previous_runtime is None:
            del app.state.runtime
        else:
            app.state.runtime = self._previous_runtime
        self.engine.dispose()

    def test_alert_list_detail_image_and_bulk_delete(self):
        listed = self.client.get("/api/alerts?limit=50&offset=0")
        self.assertEqual(listed.status_code, 200)
        items = listed.json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual({item["frame_id"] for item in items}, {38})
        self.assertEqual(len({item["alert_frame_id"] for item in items}), 1)
        self.assertTrue(all(item["has_frame"] for item in items))
        self.assertTrue(all(item["model_name"] == "API model" for item in items))

        alert_id = self.saved_events[0]["alert_id"]
        detail = self.client.get(f"/api/alerts/{alert_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["frame"]["frame_id"], 38)
        self.assertEqual(
            [item["class_name"] for item in detail.json()["frame"]["detections"]],
            ["hand", "tool"],
        )

        image = self.client.get(f"/api/alerts/{alert_id}/frame")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertEqual(image.content, b"\xff\xd8api-frame\xff\xd9")
        self.assertEqual(self.client.get("/api/alerts/999").status_code, 404)

        self.assertEqual(self.client.delete("/api/alerts").json(), {"deleted": True})
        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AlertEvent)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(AlertFrame)), 0)
            self.assertEqual(
                db.scalar(select(func.count()).select_from(AlertFrameDetection)),
                0,
            )

    def test_source_and_insta360_settings_routes(self):
        source = self.client.get("/api/source")
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.json()["source_type"], "insta360")

        changed = self.client.patch("/api/source", json={"source_type": "video"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["source_type"], "video")
        self.assertEqual(changed.json()["status"], "ready")
        self.assertEqual(self.client.post("/api/insta360/connect").status_code, 409)

        self.client.patch("/api/source", json={"source_type": "insta360"})
        updated = self.client.patch(
            "/api/insta360",
            json={
                "camera_ip": "192.168.42.2",
                "tcp_port": 6667,
                "reconnect_interval_ms": 4000,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["camera_ip"], "192.168.42.2")
        self.assertEqual(updated.json()["tcp_port"], 6667)
        self.assertEqual(self.client.post("/api/insta360/connect").json()["status"], "connecting")
        self.assertEqual(
            self.client.post("/api/insta360/disconnect").json()["status"],
            "disconnected",
        )

        settings = self.client.get("/api/settings")
        self.assertEqual(settings.json()["jpeg_quality"], 80)
        updated_settings = self.client.patch(
            "/api/settings",
            json={"jpeg_quality": 65},
        )
        self.assertEqual(updated_settings.status_code, 200)
        self.assertEqual(updated_settings.json()["jpeg_quality"], 65)
        self.assertEqual(app.state.runtime.inference_settings, {"jpeg_quality": 65})


class _FakeRuntime:
    def __init__(self):
        self.capture = _FakeCapture()
        self.inference_settings = {}

    def source_snapshot(self):
        return self.capture.snapshot

    def set_inference_settings(self, settings):
        self.inference_settings.update(settings)


class _FakeCapture:
    def __init__(self):
        self.snapshot = {
            "source_type": "insta360",
            "insta360_status": "disconnected",
            "insta360_error": None,
            "last_frame_received_at": None,
            "video_status": "ready",
            "video_error": None,
            "position_ms": 0,
            "duration_ms": None,
        }

    def select_source(self, source_type):
        self.snapshot["source_type"] = source_type
        self.snapshot["insta360_status"] = "disconnected"

    def select_empty_video(self, _loop_enabled):
        self.snapshot["source_type"] = "video"
        self.snapshot["video_status"] = "ready"

    def configure_insta360(self, camera_ip, tcp_port, reconnect_interval_ms):
        self.config = (camera_ip, tcp_port, reconnect_interval_ms)

    def connect_insta360(self):
        self.snapshot["insta360_status"] = "connecting"

    def disconnect_insta360(self):
        self.snapshot["insta360_status"] = "disconnected"


if __name__ == "__main__":
    unittest.main()
