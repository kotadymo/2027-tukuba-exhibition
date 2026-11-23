from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    AlertEvent,
    AlertFrame,
    AlertFrameDetection,
    utc_now,
)
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
    batch_job_id: int | None = None,
) -> list[dict[str, Any]]:
    frame = db.scalars(
        select(AlertFrame).where(
            AlertFrame.source_session_id == source_session_id,
            AlertFrame.frame_id == frame_id,
        )
    ).first()
    created_frame = frame is None
    if created_frame:
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

    if created_frame:
        frame_detections = list(frame.detections)
    else:
        frame_detections = db.scalars(
            select(AlertFrameDetection).where(
                AlertFrameDetection.alert_frame_id == frame.id
            )
        ).all()

    persisted_events = []
    for event in events:
        matched_classes = event["detected_classes"]
        education_snapshot = _education_snapshot(
            event,
            frame_detections,
            width,
            height,
        )
        record = AlertEvent(
            batch_job_id=batch_job_id,
            alert_frame_id=frame.id,
            rule_id=event["rule_id"],
            model_id=model_id,
            hazard_reason_id=education_snapshot.get("hazard_reason_id"),
            rule_name_snapshot=event["rule_name"],
            detected_classes_json=json.dumps(matched_classes, ensure_ascii=False),
            matched_class_names_json=json.dumps(matched_classes, ensure_ascii=False),
            event_type="alert_started",
            source_type=source_type,
            video_id=video_id,
            media_time_ms=media_time_ms,
            quiz_target_detection_id=education_snapshot.get("target_detection_id"),
            quiz_correct_reason_snapshot=education_snapshot.get("correct_reason"),
            quiz_wrong_reason_snapshot=education_snapshot.get("wrong_reason"),
            quiz_explanation_snapshot=education_snapshot.get("explanation"),
            crop_x1=education_snapshot.get("crop_x1"),
            crop_y1=education_snapshot.get("crop_y1"),
            crop_x2=education_snapshot.get("crop_x2"),
            crop_y2=education_snapshot.get("crop_y2"),
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


def _education_snapshot(
    event: dict[str, Any],
    detections: list[AlertFrameDetection],
    width: int,
    height: int,
) -> dict[str, Any]:
    target_class_name = event.get("education_target_class_name")
    hazard_reason_id = event.get("hazard_reason_id")
    correct_reason = event.get("correct_reason")
    wrong_reason = event.get("wrong_reason")
    explanation = event.get("explanation")
    snapshot = {
        "hazard_reason_id": hazard_reason_id,
        "correct_reason": correct_reason,
        "wrong_reason": wrong_reason,
        "explanation": explanation,
    }
    required_classes = set(event.get("detected_classes") or [])
    if (
        not target_class_name
        or hazard_reason_id is None
        or target_class_name not in required_classes
        or not correct_reason
        or not wrong_reason
        or not explanation
    ):
        return snapshot

    best_by_class: dict[str, AlertFrameDetection] = {}
    for detection in detections:
        if detection.class_name not in required_classes:
            continue
        current = best_by_class.get(detection.class_name)
        if current is None or detection.confidence > current.confidence:
            best_by_class[detection.class_name] = detection
    if required_classes - best_by_class.keys():
        return snapshot

    target = best_by_class.get(target_class_name)
    if target is None:
        return snapshot
    x1 = min(detection.x1 for detection in best_by_class.values())
    y1 = min(detection.y1 for detection in best_by_class.values())
    x2 = max(detection.x2 for detection in best_by_class.values())
    y2 = max(detection.y2 for detection in best_by_class.values())
    union_width = x2 - x1
    union_height = y2 - y1
    if (
        not all(math.isfinite(value) for value in (x1, y1, x2, y2))
        or union_width <= 0
        or union_height <= 0
        or width <= 0
        or height <= 0
    ):
        return snapshot

    margin_x = max(union_width * 0.25, 16)
    margin_y = max(union_height * 0.25, 16)
    crop_x1 = max(0, math.floor(x1 - margin_x))
    crop_y1 = max(0, math.floor(y1 - margin_y))
    crop_x2 = min(width, math.ceil(x2 + margin_x))
    crop_y2 = min(height, math.ceil(y2 + margin_y))
    if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
        return snapshot

    return {
        **snapshot,
        "target_detection_id": target.id,
        "crop_x1": crop_x1,
        "crop_y1": crop_y1,
        "crop_x2": crop_x2,
        "crop_y2": crop_y2,
    }
