from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import AlertEvent, AlertFrame, AlertFrameDetection, utc_now
from .inference import Detection


def persist_alert_frame_events(
    db: Session,
    *,
    events: list[dict[str, Any]],
    source_session_id: str,
    frame_id: int,
    source_type: str,
    timestamp: str,
    video_id: int | None,
    media_time_ms: int | None,
    model_id: int | None,
    model_name: str | None,
    width: int,
    height: int,
    image_jpeg: bytes,
    detections: list[Detection],
) -> list[dict[str, Any]]:
    frame = db.scalars(
        select(AlertFrame).where(
            AlertFrame.source_session_id == source_session_id,
            AlertFrame.frame_id == frame_id,
        )
    ).first()
    if frame is None:
        frame_time = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
        frame = AlertFrame(
            source_session_id=source_session_id,
            frame_id=frame_id,
            model_id=model_id,
            model_name_snapshot=model_name,
            source_type=source_type,
            video_id=video_id,
            media_time_ms=media_time_ms,
            captured_at=frame_time,
            width=width,
            height=height,
            image_jpeg=image_jpeg,
            detections=[
                AlertFrameDetection(
                    class_id=detection.class_id,
                    class_name=detection.class_name,
                    confidence=detection.confidence,
                    x1=detection.bbox[0],
                    y1=detection.bbox[1],
                    x2=detection.bbox[2],
                    y2=detection.bbox[3],
                )
                for detection in detections
            ],
        )
        db.add(frame)
        db.flush()

    persisted_events = []
    for event in events:
        matched_classes = event["detected_classes"]
        record = AlertEvent(
            alert_frame_id=frame.id,
            rule_id=event["rule_id"],
            model_id=model_id,
            rule_name_snapshot=event["rule_name"],
            detected_classes_json=json.dumps(matched_classes, ensure_ascii=False),
            matched_class_names_json=json.dumps(matched_classes, ensure_ascii=False),
            event_type="alert_started",
            source_type=source_type,
            video_id=video_id,
            media_time_ms=media_time_ms,
            created_at=utc_now(),
        )
        db.add(record)
        db.flush()
        persisted_events.append(
            {
                **event,
                "alert_id": record.id,
                "frame_id": frame_id,
                "source_session_id": source_session_id,
            }
        )
    return persisted_events
