import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.database import (
    AlertEvent,
    SourceSettings,
    initialize_database,
)


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_camera_and_alert_history_are_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            engine = create_engine("sqlite://")
            session_local = sessionmaker(bind=engine, expire_on_commit=False)
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "CREATE TABLE camera_settings ("
                        "id INTEGER PRIMARY KEY, "
                        "source_type VARCHAR(16) NOT NULL, "
                        "rtsp_url TEXT NOT NULL, "
                        "active_video_id INTEGER, "
                        "loop_enabled BOOLEAN NOT NULL)"
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO camera_settings VALUES (1, 'video', '', 42, 0)"
                    )
                    connection.exec_driver_sql(
                        "CREATE TABLE alert_events ("
                        "id INTEGER PRIMARY KEY, "
                        "rule_id INTEGER, "
                        "model_id INTEGER, "
                        "rule_name_snapshot VARCHAR(120) NOT NULL, "
                        "detected_classes_json TEXT NOT NULL, "
                        "event_type VARCHAR(32) NOT NULL, "
                        "source_type VARCHAR(16) NOT NULL, "
                        "video_id INTEGER, "
                        "media_time_ms INTEGER, "
                        "created_at DATETIME)"
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO alert_events VALUES "
                        "(1, NULL, NULL, 'legacy rule', '[\"hand\", \"tool\"]', "
                        "'alert_started', 'video', 42, 1250, '2026-11-08 03:00:00')"
                    )

                with (
                    patch("app.database.engine", engine),
                    patch("app.database.SessionLocal", session_local),
                    patch("app.database.DATA_DIR", data_dir),
                    patch("app.database.MODELS_DIR", data_dir / "models"),
                    patch("app.database.VIDEOS_DIR", data_dir / "videos"),
                ):
                    initialize_database()

                columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("alert_events")
                }
                self.assertIn("alert_frame_id", columns)
                self.assertIn("matched_class_names_json", columns)
                with session_local() as db:
                    source = db.get(SourceSettings, 1)
                    legacy_event = db.scalar(
                        select(AlertEvent).where(AlertEvent.id == 1)
                    )
                    self.assertEqual(source.source_type, "video")
                    self.assertEqual(source.active_video_id, 42)
                    self.assertFalse(source.loop_enabled)
                    self.assertEqual(legacy_event.rule_name_snapshot, "legacy rule")
                    self.assertIsNone(legacy_event.alert_frame_id)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
