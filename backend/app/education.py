from __future__ import annotations

import random

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .database import AlertEvent, AlertFrame, AlertFrameDetection, AlertRule


MIN_TARGET_SIDE_PX = 6.0
TARGET_IMAGE_SIDE_PX = 64.0
MAX_UPSCALE = 4.0
MAX_IMAGE_EDGE_PX = 2048


def _quiz_geometry(event: AlertEvent) -> dict[str, object] | None:
    frame = event.frame
    if frame is None or not frame.image_jpeg or event.quiz_target_detection_id is None:
        return None
    if any(
        value is None
        for value in (event.crop_x1, event.crop_y1, event.crop_x2, event.crop_y2)
    ):
        return None
    detection = next(
        (
            item
            for item in frame.detections
            if item.id == event.quiz_target_detection_id
        ),
        None,
    )
    if detection is None:
        return None

    crop_x1 = max(0, min(frame.width, int(event.crop_x1)))
    crop_y1 = max(0, min(frame.height, int(event.crop_y1)))
    crop_x2 = max(0, min(frame.width, int(event.crop_x2)))
    crop_y2 = max(0, min(frame.height, int(event.crop_y2)))
    crop_width = crop_x2 - crop_x1
    crop_height = crop_y2 - crop_y1
    target_width = detection.x2 - detection.x1
    target_height = detection.y2 - detection.y1
    if (
        crop_width <= 0
        or crop_height <= 0
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
        (detection.x1 - crop_x1) * scale,
        (detection.y1 - crop_y1) * scale,
        (detection.x2 - crop_x1) * scale,
        (detection.y2 - crop_y1) * scale,
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


def generate_quiz_questions(db: Session, max_questions: int = 3) -> dict:
    events = db.scalars(
        select(AlertEvent)
        .options(selectinload(AlertEvent.frame).selectinload(AlertFrame.detections))
        .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
    ).all()
    rules = db.execute(
        select(AlertRule.id, AlertRule.correct_reason).where(
            AlertRule.correct_reason.is_not(None)
        )
    ).all()
    reason_by_rule = {
        rule_id: reason.strip()
        for rule_id, reason in rules
        if reason and reason.strip()
    }

    events_with_education = [
        event
        for event in events
        if event.quiz_correct_reason_snapshot
        and event.quiz_explanation_snapshot
    ]
    events_with_snapshots = [
        event
        for event in events_with_education
        if _quiz_geometry(event) is not None
    ]
    candidates = []
    events_with_wrong_reason = 0
    for event in events_with_snapshots:
        correct_reason = event.quiz_correct_reason_snapshot.strip()
        wrong_reasons = sorted(
            {
                reason
                for rule_id, reason in reason_by_rule.items()
                if rule_id != event.rule_id and reason != correct_reason
            }
        )
        if not wrong_reasons:
            continue
        events_with_wrong_reason += 1
        candidates.append(
            {
                "event": event,
                "geometry": _quiz_geometry(event),
                "wrong_reasons": wrong_reasons,
            }
        )

    candidates_by_frame = {}
    for candidate in candidates:
        candidates_by_frame.setdefault(
            candidate["event"].alert_frame_id,
            [],
        ).append(candidate)

    selected = []
    selected_frame_ids = set()
    rule_frames = {}
    for frame_id, frame_candidates in candidates_by_frame.items():
        frame_rule_ids = set()
        for candidate in frame_candidates:
            event = candidate["event"]
            rule_id = event.rule_id or f"snapshot:{event.rule_name_snapshot}"
            if rule_id in frame_rule_ids:
                continue
            frame_rule_ids.add(rule_id)
            rule_frames.setdefault(rule_id, []).append(frame_id)

    for rule_id in sorted(rule_frames, key=lambda item: len(rule_frames[item])):
        for frame_id in rule_frames[rule_id]:
            if frame_id in selected_frame_ids:
                continue
            candidate = next(
                item
                for item in candidates_by_frame[frame_id]
                if (
                    item["event"].rule_id
                    or f"snapshot:{item['event'].rule_name_snapshot}"
                )
                == rule_id
            )
            selected.append(candidate)
            selected_frame_ids.add(frame_id)
            break
        if len(selected) >= max_questions:
            break

    for frame_id, frame_candidates in candidates_by_frame.items():
        if len(selected) >= max_questions:
            break
        if frame_id in selected_frame_ids:
            continue
        candidate = frame_candidates[0]
        selected.append(candidate)
        selected_frame_ids.add(frame_id)

    rng = random.SystemRandom()
    questions = []
    for candidate in selected:
        event = candidate["event"]
        geometry = candidate["geometry"]
        wrong_reason = rng.choice(candidate["wrong_reasons"])
        choices = [
            {"id": "correct", "text": event.quiz_correct_reason_snapshot},
            {"id": "incorrect", "text": wrong_reason},
        ]
        rng.shuffle(choices)
        questions.append(
            {
                "alert_event_id": event.id,
                "image_url": f"/api/quiz/questions/{event.id}/image",
                "image_width": geometry["image_width"],
                "image_height": geometry["image_height"],
                "target_bbox": geometry["target_bbox"],
                "choices": choices,
                "correct_choice_id": "correct",
                "explanation": event.quiz_explanation_snapshot,
            }
        )

    unavailable_reason = None
    if not questions:
        if not events:
            unavailable_reason = "危険フレームがまだありません"
        elif not events_with_education:
            unavailable_reason = "教材設定がある発報履歴がまだありません"
        elif not events_with_snapshots:
            unavailable_reason = "出題できる大きさの教材画像がありません"
        elif not events_with_wrong_reason:
            unavailable_reason = "2択用の理由が不足しています"
        else:
            unavailable_reason = "出題できる大きさの教材画像がありません"

    return {
        "questions": questions,
        "available_question_count": len(questions),
        "unavailable_reason": unavailable_reason,
    }


def render_quiz_image(db: Session, alert_event_id: int) -> bytes:
    event = db.scalar(
        select(AlertEvent)
        .options(selectinload(AlertEvent.frame).selectinload(AlertFrame.detections))
        .where(AlertEvent.id == alert_event_id)
    )
    if event is None:
        raise LookupError("教材イベントが見つかりません")
    geometry = _quiz_geometry(event)
    if geometry is None or event.frame is None:
        raise LookupError("教材画像または正解対象がありません")

    frame_image = cv2.imdecode(
        np.frombuffer(event.frame.image_jpeg, dtype=np.uint8),
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
