from __future__ import annotations

import random

import cv2
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import AlertEvent, AlertFrame, AlertFrameDetection, AlertRule


MIN_TARGET_SIDE_PX = 6.0
TARGET_IMAGE_SIDE_PX = 64.0
MAX_UPSCALE = 4.0
MAX_IMAGE_EDGE_PX = 2048
QUIZ_METADATA_PAGE_SIZE = 100
MAX_QUIZ_METADATA_ROWS = 5000


def _quiz_geometry(
    *,
    width: int,
    height: int,
    crop_x1: float,
    crop_y1: float,
    crop_x2: float,
    crop_y2: float,
    target_x1: float,
    target_y1: float,
    target_x2: float,
    target_y2: float,
) -> dict[str, object] | None:
    crop_x1 = max(0, min(width, int(crop_x1)))
    crop_y1 = max(0, min(height, int(crop_y1)))
    crop_x2 = max(0, min(width, int(crop_x2)))
    crop_y2 = max(0, min(height, int(crop_y2)))
    crop_width = crop_x2 - crop_x1
    crop_height = crop_y2 - crop_y1
    target_width = target_x2 - target_x1
    target_height = target_y2 - target_y1
    if (
        crop_width <= 0
        or crop_height <= 0
        or width <= 0
        or height <= 0
        or min(target_width, target_height) < MIN_TARGET_SIDE_PX
    ):
        return None

    scale = min(
        MAX_UPSCALE,
        TARGET_IMAGE_SIDE_PX / min(target_width, target_height),
        MAX_IMAGE_EDGE_PX / max(crop_width, crop_height),
    )
    if scale <= 0:
        return None
    image_width = max(1, round(crop_width * scale))
    image_height = max(1, round(crop_height * scale))
    target_bbox = [
        (target_x1 - crop_x1) * scale,
        (target_y1 - crop_y1) * scale,
        (target_x2 - crop_x1) * scale,
        (target_y2 - crop_y1) * scale,
    ]
    target_bbox = [
        max(0.0, min(float(image_width if index % 2 == 0 else image_height), value))
        for index, value in enumerate(target_bbox)
    ]
    if target_bbox[2] <= target_bbox[0] or target_bbox[3] <= target_bbox[1]:
        return None
    return {
        "crop": (crop_x1, crop_y1, crop_x2, crop_y2),
        "scale": scale,
        "image_width": image_width,
        "image_height": image_height,
        "target_bbox": target_bbox,
    }


def _candidate_query():
    return (
        select(
            AlertEvent.id.label("event_id"),
            AlertEvent.alert_frame_id.label("frame_id"),
            AlertEvent.rule_id,
            AlertEvent.hazard_reason_id,
            AlertEvent.rule_name_snapshot,
            AlertEvent.quiz_target_detection_id,
            AlertEvent.quiz_correct_reason_snapshot,
            AlertEvent.quiz_wrong_reason_snapshot,
            AlertEvent.quiz_explanation_snapshot,
            AlertEvent.crop_x1,
            AlertEvent.crop_y1,
            AlertEvent.crop_x2,
            AlertEvent.crop_y2,
            AlertEvent.created_at,
            AlertFrame.width,
            AlertFrame.height,
            AlertFrameDetection.x1.label("target_x1"),
            AlertFrameDetection.y1.label("target_y1"),
            AlertFrameDetection.x2.label("target_x2"),
            AlertFrameDetection.y2.label("target_y2"),
        )
        .join(AlertFrame, AlertFrame.id == AlertEvent.alert_frame_id)
        .join(
            AlertFrameDetection,
            AlertFrameDetection.id == AlertEvent.quiz_target_detection_id,
        )
        .where(
            func.length(AlertFrame.image_jpeg) > 0,
            AlertFrameDetection.alert_frame_id == AlertEvent.alert_frame_id,
            AlertEvent.hazard_reason_id.is_not(None),
            AlertEvent.quiz_target_detection_id.is_not(None),
            AlertEvent.quiz_correct_reason_snapshot.is_not(None),
            AlertEvent.quiz_wrong_reason_snapshot.is_not(None),
            AlertEvent.quiz_explanation_snapshot.is_not(None),
            AlertEvent.crop_x1.is_not(None),
            AlertEvent.crop_y1.is_not(None),
            AlertEvent.crop_x2.is_not(None),
            AlertEvent.crop_y2.is_not(None),
        )
        .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
    )


def _candidate_from_row(row) -> dict[str, object] | None:
    geometry = _quiz_geometry(
        width=row.width,
        height=row.height,
        crop_x1=row.crop_x1,
        crop_y1=row.crop_y1,
        crop_x2=row.crop_x2,
        crop_y2=row.crop_y2,
        target_x1=row.target_x1,
        target_y1=row.target_y1,
        target_x2=row.target_x2,
        target_y2=row.target_y2,
    )
    correct_reason = (row.quiz_correct_reason_snapshot or "").strip()
    wrong_reason = (row.quiz_wrong_reason_snapshot or "").strip()
    explanation = (row.quiz_explanation_snapshot or "").strip()
    if (
        geometry is None
        or not correct_reason
        or not wrong_reason
        or correct_reason.split() == wrong_reason.split()
        or not explanation
    ):
        return None
    return {
        "event_id": row.event_id,
        "frame_id": row.frame_id,
        "rule_id": row.rule_id,
        "hazard_reason_id": row.hazard_reason_id,
        "correct_reason": correct_reason,
        "wrong_reason": wrong_reason,
        "explanation": explanation,
        "geometry": geometry,
    }


def _select_candidates(
    candidates: list[dict[str, object]],
    max_questions: int,
) -> list[dict[str, object]]:
    return _select_distinct_candidates(candidates, max_questions)


def _select_distinct_candidates(
    candidates: list[dict[str, object]],
    max_questions: int,
) -> list[dict[str, object]]:
    candidates_by_reason: dict[int, list[dict[str, object]]] = {}
    for candidate in candidates:
        candidates_by_reason.setdefault(candidate["hazard_reason_id"], []).append(
            candidate
        )

    reason_groups = sorted(
        candidates_by_reason.items(),
        key=lambda item: len(
            {candidate["frame_id"] for candidate in item[1]}
        ),
    )
    frame_reasons: dict[int, int] = {}
    selected_by_reason: dict[int, dict[str, object]] = {}

    def assign(
        reason_id: int,
        visited_frames: set[int],
    ) -> bool:
        for candidate in candidates_by_reason[reason_id]:
            frame_id = candidate["frame_id"]
            if frame_id in visited_frames:
                continue
            visited_frames.add(frame_id)
            assigned_reason = frame_reasons.get(frame_id)
            if assigned_reason is None or assign(assigned_reason, visited_frames):
                frame_reasons[frame_id] = reason_id
                selected_by_reason[reason_id] = candidate
                return True
        return False

    for reason_id, _ in reason_groups:
        assign(reason_id, set())
        if len(selected_by_reason) >= max_questions:
            break
    return list(selected_by_reason.values())


def _distinct_reason_count(
    candidates: list[dict[str, object]],
    max_questions: int,
) -> int:
    return len(_select_distinct_candidates(candidates, max_questions))


def generate_quiz_questions(db: Session, max_questions: int = 3) -> dict:
    candidates: list[dict[str, object]] = []
    offset = 0
    while offset < MAX_QUIZ_METADATA_ROWS:
        rows = db.execute(
            _candidate_query()
            .offset(offset)
            .limit(min(QUIZ_METADATA_PAGE_SIZE, MAX_QUIZ_METADATA_ROWS - offset))
        ).all()
        if not rows:
            break
        candidates.extend(
            candidate
            for row in rows
            if (candidate := _candidate_from_row(row)) is not None
        )
        offset += len(rows)
        if (
            _distinct_reason_count(candidates, max_questions) >= max_questions
            or len(rows) < QUIZ_METADATA_PAGE_SIZE
        ):
            break

    selected = _select_candidates(candidates, max_questions)
    rng = random.SystemRandom()
    questions = []
    for candidate in selected:
        geometry = candidate["geometry"]
        choices = [
            {"id": "correct", "text": candidate["correct_reason"]},
            {"id": "incorrect", "text": candidate["wrong_reason"]},
        ]
        rng.shuffle(choices)
        questions.append(
            {
                "alert_event_id": candidate["event_id"],
                "hazard_reason_id": candidate["hazard_reason_id"],
                "image_url": f"/api/quiz/questions/{candidate['event_id']}/image",
                "image_width": geometry["image_width"],
                "image_height": geometry["image_height"],
                "target_bbox": geometry["target_bbox"],
                "choices": choices,
                "correct_choice_id": "correct",
                "explanation": candidate["explanation"],
            }
        )

    unavailable_reason = None
    if not questions:
        event_count = db.scalar(select(func.count()).select_from(AlertEvent)) or 0
        if not event_count:
            unavailable_reason = "危険フレームがまだありません"
        else:
            unavailable_reason = "教材設定と保存画像がそろった発報履歴がありません"

    return {
        "questions": questions,
        "available_question_count": len(questions),
        "unavailable_reason": unavailable_reason,
    }


def render_quiz_image(db: Session, alert_event_id: int) -> bytes:
    row = db.execute(
        select(
            AlertFrame.image_jpeg,
            AlertFrame.width,
            AlertFrame.height,
            AlertEvent.crop_x1,
            AlertEvent.crop_y1,
            AlertEvent.crop_x2,
            AlertEvent.crop_y2,
            AlertFrameDetection.x1.label("target_x1"),
            AlertFrameDetection.y1.label("target_y1"),
            AlertFrameDetection.x2.label("target_x2"),
            AlertFrameDetection.y2.label("target_y2"),
        )
        .join(AlertFrame, AlertFrame.id == AlertEvent.alert_frame_id)
        .join(
            AlertFrameDetection,
            AlertFrameDetection.id == AlertEvent.quiz_target_detection_id,
        )
        .where(
            AlertEvent.id == alert_event_id,
            AlertFrameDetection.alert_frame_id == AlertEvent.alert_frame_id,
        )
    ).first()
    if row is None:
        raise LookupError("教材イベントが見つかりません")
    geometry = _quiz_geometry(
        width=row.width,
        height=row.height,
        crop_x1=row.crop_x1,
        crop_y1=row.crop_y1,
        crop_x2=row.crop_x2,
        crop_y2=row.crop_y2,
        target_x1=row.target_x1,
        target_y1=row.target_y1,
        target_x2=row.target_x2,
        target_y2=row.target_y2,
    )
    if geometry is None or not row.image_jpeg:
        raise LookupError("教材画像または正解対象がありません")

    frame_image = cv2.imdecode(
        np.frombuffer(row.image_jpeg, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if frame_image is None:
        raise ValueError("保存されたアラート画像をデコードできません")
    x1, y1, x2, y2 = geometry["crop"]
    crop = frame_image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("教材画像の切り出し範囲が空です")

    scale = geometry["scale"]
    if scale != 1:
        interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        crop = cv2.resize(
            crop,
            (geometry["image_width"], geometry["image_height"]),
            interpolation=interpolation,
        )
    success, encoded = cv2.imencode(
        ".jpg",
        crop,
        [int(cv2.IMWRITE_JPEG_QUALITY), 92],
    )
    if not success:
        raise ValueError("教材画像を JPEG に変換できません")
    return encoded.tobytes()
