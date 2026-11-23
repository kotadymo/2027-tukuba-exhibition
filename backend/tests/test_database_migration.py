import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.database import (
    BatchJob,
    BatchJobVideo,
    Base,
    DatabaseSchemaError,
    ModelSettings,
    SourceSettings,
    initialize_database,
)


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_schema_is_rejected_without_deleting_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            models_dir = data_dir / "models"
            videos_dir = data_dir / "videos"
            models_dir.mkdir()
            videos_dir.mkdir()
            (models_dir / "keep.pt").write_bytes(b"model")
            (videos_dir / "keep.mp4").write_bytes(b"video")
            engine = create_engine("sqlite://")
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
                    patch("app.database.DATA_DIR", data_dir),
                    patch("app.database.MODELS_DIR", models_dir),
                    patch("app.database.VIDEOS_DIR", videos_dir),
                ):
                    with self.assertRaisesRegex(DatabaseSchemaError, "明示的に"):
                        initialize_database()

                columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("alert_events")
                }
                self.assertNotIn("alert_frame_id", columns)
                self.assertNotIn("hazard_reason_id", columns)
                self.assertNotIn("source_settings", inspect(engine).get_table_names())
                with engine.connect() as connection:
                    legacy_event = connection.exec_driver_sql(
                        "SELECT rule_name_snapshot FROM alert_events WHERE id = 1"
                    ).scalar_one()
                self.assertEqual(legacy_event, "legacy rule")
                self.assertEqual((models_dir / "keep.pt").read_bytes(), b"model")
                self.assertEqual((videos_dir / "keep.mp4").read_bytes(), b"video")
            finally:
                engine.dispose()

    def test_new_database_gets_v070_schema_and_auto_device_default(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            engine = create_engine("sqlite://")
            session_local = sessionmaker(bind=engine, expire_on_commit=False)
            try:
                with (
                    patch("app.database.engine", engine),
                    patch("app.database.SessionLocal", session_local),
                    patch("app.database.DATA_DIR", data_dir),
                    patch("app.database.MODELS_DIR", data_dir / "models"),
                    patch("app.database.VIDEOS_DIR", data_dir / "videos"),
                ):
                    initialize_database()

                self.assertIn("hazard_reasons", inspect(engine).get_table_names())
                with session_local() as db:
                    self.assertEqual(db.get(ModelSettings, 1).requested_device, "auto")
                    self.assertEqual(db.get(SourceSettings, 1).source_type, "insta360")
            finally:
                engine.dispose()

    def test_batch_schema_is_added_without_losing_alerts_and_running_jobs_are_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            engine = create_engine("sqlite://")
            session_local = sessionmaker(bind=engine, expire_on_commit=False)
            try:
                Base.metadata.create_all(engine)
                with engine.begin() as connection:
                    connection.exec_driver_sql("DROP TABLE alert_events")
                    connection.exec_driver_sql(
                        "CREATE TABLE alert_events ("
                        "id INTEGER PRIMARY KEY, "
                        "hazard_reason_id INTEGER, "
                        "quiz_wrong_reason_snapshot TEXT, "
                        "rule_name_snapshot VARCHAR(120) NOT NULL, "
                        "detected_classes_json TEXT NOT NULL, "
                        "event_type VARCHAR(32) NOT NULL, "
                        "source_type VARCHAR(16) NOT NULL, "
                        "created_at DATETIME)"
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO alert_events "
                        "(id, hazard_reason_id, quiz_wrong_reason_snapshot, "
                        "rule_name_snapshot, detected_classes_json, event_type, "
                        "source_type, created_at) "
                        "VALUES (1, 4, 'wrong', 'kept alert', '[]', 'alert_started', 'video', "
                        "'2027-01-01 00:00:00')"
                    )
                    connection.exec_driver_sql("DROP TABLE batch_job_videos")
                    connection.exec_driver_sql("DROP TABLE batch_jobs")

                with (
                    patch("app.database.engine", engine),
                    patch("app.database.SessionLocal", session_local),
                    patch("app.database.DATA_DIR", data_dir),
                    patch("app.database.MODELS_DIR", data_dir / "models"),
                    patch("app.database.VIDEOS_DIR", data_dir / "videos"),
                ):
                    initialize_database()

                    with session_local.begin() as db:
                        job = BatchJob(
                            status="running",
                            model_name_snapshot="demo",
                            settings_json="{}",
                            total_videos=1,
                        )
                        job.videos.append(
                            BatchJobVideo(
                                order_index=0,
                                video_name_snapshot="demo",
                                video_stored_filename_snapshot="demo.mp4",
                                status="running",
                            )
                        )
                        db.add(job)
                    initialize_database()

                self.assertIn("batch_jobs", inspect(engine).get_table_names())
                self.assertIn("batch_job_videos", inspect(engine).get_table_names())
                columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("alert_events")
                }
                self.assertIn("batch_job_id", columns)
                with session_local() as db:
                    self.assertEqual(
                        db.execute(
                            text(
                                "SELECT rule_name_snapshot FROM alert_events "
                                "WHERE id = 1"
                            )
                        ).scalar_one(),
                        "kept alert",
                    )
                    recovered_job = db.scalar(select(BatchJob))
                    self.assertEqual(recovered_job.status, "interrupted")
                    self.assertEqual(recovered_job.videos[0].status, "interrupted")
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
