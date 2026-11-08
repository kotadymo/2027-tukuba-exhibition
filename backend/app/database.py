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


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detection_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    release_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    cooldown_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    classes: Mapped[list[AlertRuleClass]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="AlertRuleClass.id",
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


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_frame_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_frames.id", ondelete="SET NULL"), index=True
    )
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL")
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL")
    )
    rule_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    detected_classes_json: Mapped[str] = mapped_column(Text, nullable=False)
    matched_class_names_json: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="alert_started")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    media_time_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    frame: Mapped[AlertFrame | None] = relationship(back_populates="events")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "sqlite":
        columns = {
            column["name"] for column in inspect(engine).get_columns("alert_events")
        }
        with engine.begin() as connection:
            if "alert_frame_id" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE alert_events ADD COLUMN "
                    "alert_frame_id INTEGER REFERENCES alert_frames(id) ON DELETE SET NULL"
                )
            if "matched_class_names_json" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE alert_events ADD COLUMN matched_class_names_json TEXT"
                )

    with SessionLocal.begin() as db:
        if db.get(ModelSettings, 1) is None:
            db.add(ModelSettings(id=1))
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
