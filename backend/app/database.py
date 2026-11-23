from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    URL,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", ROOT_DIR / "data")).expanduser().resolve()
MODELS_DIR = DATA_DIR / "models"
VIDEOS_DIR = DATA_DIR / "videos"
DATABASE_PATH = DATA_DIR / "app.db"
MAX_MODEL_SIZE_BYTES = int(os.environ.get("MAX_MODEL_SIZE_BYTES", 512 * 1024 * 1024))
MAX_VIDEO_SIZE_BYTES = int(os.environ.get("MAX_VIDEO_SIZE_BYTES", 1024 * 1024 * 1024))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _create_engine():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)

    return create_engine(
        URL.create("sqlite", database=str(DATABASE_PATH)),
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )


engine = _create_engine()
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class ModelRecord(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    classes: Mapped[list[ModelClass]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="ModelClass.class_id",
    )


class ModelClass(Base):
    __tablename__ = "model_classes"
    __table_args__ = (UniqueConstraint("model_id", "class_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[ModelRecord] = relationship(back_populates="classes")


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelSettings(Base):
    __tablename__ = "model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL")
    )
    requested_device: Mapped[str] = mapped_column(
        String(16), nullable=False, default="auto"
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.4)
    iou_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    imgsz: Mapped[int] = mapped_column(Integer, nullable=False, default=416)
    target_fps: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)


class CameraSettings(Base):
    __tablename__ = "camera_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="rtsp")
    rtsp_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    loop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SourceSettings(Base):
    __tablename__ = "source_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="insta360")
    active_video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    loop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Insta360Settings(Base):
    __tablename__ = "insta360_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_ip: Mapped[str] = mapped_column(String(255), nullable=False, default="192.168.42.1")
    tcp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=6666)
    reconnect_interval_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3000
    )


class HazardReason(Base):
    __tablename__ = "hazard_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    correct_reason: Mapped[str] = mapped_column(Text, nullable=False)
    wrong_reason: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    rules: Mapped[list[AlertRule]] = relationship(back_populates="hazard_reason")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detection_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    release_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    cooldown_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    education_target_class_name: Mapped[str | None] = mapped_column(String(255))
    hazard_reason_id: Mapped[int | None] = mapped_column(
        ForeignKey("hazard_reasons.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    classes: Mapped[list[AlertRuleClass]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="AlertRuleClass.id",
    )
    hazard_reason: Mapped[HazardReason | None] = relationship(
        back_populates="rules"
    )


class AlertRuleClass(Base):
    __tablename__ = "alert_rule_classes"
    __table_args__ = (UniqueConstraint("rule_id", "class_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule: Mapped[AlertRule] = relationship(back_populates="classes")


class AlertFrame(Base):
    __tablename__ = "alert_frames"
    __table_args__ = (
        UniqueConstraint(
            "source_session_id",
            "frame_id",
            name="uq_alert_frames_source_session_frame",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    frame_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL")
    )
    model_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    media_time_ms: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    image_jpeg: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    detections: Mapped[list[AlertFrameDetection]] = relationship(
        back_populates="frame",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AlertFrameDetection.id",
    )
    events: Mapped[list[AlertEvent]] = relationship(back_populates="frame")


class AlertFrameDetection(Base):
    __tablename__ = "alert_frame_detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_frame_id: Mapped[int] = mapped_column(
        ForeignKey("alert_frames.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    x2: Mapped[float] = mapped_column(Float, nullable=False)
    y2: Mapped[float] = mapped_column(Float, nullable=False)
    frame: Mapped[AlertFrame] = relationship(back_populates="detections")


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL")
    )
    model_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_videos: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_videos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_alerts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    videos: Mapped[list[BatchJobVideo]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="BatchJobVideo.order_index",
    )


class BatchJobVideo(Base):
    __tablename__ = "batch_job_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_job_id: Mapped[int] = mapped_column(
        ForeignKey("batch_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    video_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    video_stored_filename_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    fps_snapshot: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    processed_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_frames: Mapped[int | None] = mapped_column(Integer)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[BatchJob] = relationship(back_populates="videos")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_jobs.id", ondelete="SET NULL"), index=True
    )
    alert_frame_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_frames.id", ondelete="SET NULL"), index=True
    )
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL")
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL")
    )
    hazard_reason_id: Mapped[int | None] = mapped_column(Integer)
    rule_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    detected_classes_json: Mapped[str] = mapped_column(Text, nullable=False)
    matched_class_names_json: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="alert_started")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    media_time_ms: Mapped[int | None] = mapped_column(Integer)
    quiz_target_detection_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_frame_detections.id", ondelete="SET NULL")
    )
    quiz_correct_reason_snapshot: Mapped[str | None] = mapped_column(Text)
    quiz_wrong_reason_snapshot: Mapped[str | None] = mapped_column(Text)
    quiz_explanation_snapshot: Mapped[str | None] = mapped_column(Text)
    crop_x1: Mapped[float | None] = mapped_column(Float)
    crop_y1: Mapped[float | None] = mapped_column(Float)
    crop_x2: Mapped[float | None] = mapped_column(Float)
    crop_y2: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    frame: Mapped[AlertFrame | None] = relationship(back_populates="events")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)


class DatabaseSchemaError(RuntimeError):
    pass


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    if engine.dialect.name == "sqlite":
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        required_columns = {
            "model_settings": {"requested_device"},
            "alert_rules": {"hazard_reason_id"},
            "alert_events": {
                "hazard_reason_id",
                "quiz_wrong_reason_snapshot",
            },
        }
        app_tables = set(required_columns) | {"hazard_reasons"}
        if existing_tables & app_tables:
            missing_tables = app_tables - existing_tables
            missing_columns = {
                table_name: columns - {
                    column["name"]
                    for column in inspector.get_columns(table_name)
                }
                for table_name, columns in required_columns.items()
                if table_name in existing_tables
            }
            missing_columns = {
                table_name: columns
                for table_name, columns in missing_columns.items()
                if columns
            }
            if missing_tables or missing_columns:
                details = []
                if missing_tables:
                    details.append(f"不足テーブル: {', '.join(sorted(missing_tables))}")
                if missing_columns:
                    details.extend(
                        f"{table_name} の不足カラム: {', '.join(sorted(columns))}"
                        for table_name, columns in sorted(missing_columns.items())
                    )
                raise DatabaseSchemaError(
                        f"SQLite が旧スキーマです ({'; '.join(details)})."
                    "データベースをバックアップしてから v0.7.0 用に明示的に"
                    "初期化してください。モデル・動画ファイルは削除されません。"
                )

    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "sqlite":
        alert_event_columns = {
            column["name"] for column in inspect(engine).get_columns("alert_events")
        }
        if "batch_job_id" not in alert_event_columns:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE alert_events ADD COLUMN "
                    "batch_job_id INTEGER REFERENCES batch_jobs(id) ON DELETE SET NULL"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_alert_events_batch_job_id "
                    "ON alert_events (batch_job_id)"
                )

    with SessionLocal.begin() as db:
        if db.get(ModelSettings, 1) is None:
            db.add(ModelSettings(id=1, requested_device="auto"))
        if db.get(SourceSettings, 1) is None:
            legacy_camera = db.get(CameraSettings, 1)
            source_type = (
                "video"
                if legacy_camera is not None and legacy_camera.source_type == "video"
                else "insta360"
            )
            db.add(
                SourceSettings(
                    id=1,
                    source_type=source_type,
                    active_video_id=(
                        legacy_camera.active_video_id
                        if legacy_camera is not None and source_type == "video"
                        else None
                    ),
                    loop_enabled=(
                        legacy_camera.loop_enabled if legacy_camera is not None else True
                    ),
                )
            )
        if db.get(Insta360Settings, 1) is None:
            db.add(Insta360Settings(id=1))
        now = utc_now()
        interrupted_message = "Backend の再起動によりジョブが中断されました。自動再開はされません。"
        db.execute(
            update(BatchJob)
            .where(BatchJob.status.in_(("pending", "running")))
            .values(
                status="interrupted",
                finished_at=now,
                error_message=interrupted_message,
            )
        )
        db.execute(
            update(BatchJobVideo)
            .where(BatchJobVideo.status.in_(("pending", "running")))
            .values(status="interrupted", error_message=interrupted_message)
        )
