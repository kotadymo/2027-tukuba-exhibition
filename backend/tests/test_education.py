import unittest
from datetime import datetime, timezone

import cv2
import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import (
    AlertEvent,
    AlertFrame,
    AlertFrameDetection,
    AlertRule,
    Base,
    ModelClass,
    ModelRecord,
    ModelSettings,
)
from app.education import generate_quiz_questions, render_quiz_image
from app.main import _get_db, app


class EducationTests(unittest.TestCase):
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

        self.previous_dependency = app.dependency_overrides.get(_get_db)
        app.dependency_overrides[_get_db] = override_get_db
        self.client = TestClient(app)
        self.previous_runtime = getattr(app.state, "runtime", None)
        app.state.runtime = _FakeRuntime()
        success, encoded = cv2.imencode(
            ".jpg",
            np.full((100, 140, 3), 128, dtype=np.uint8),
        )
        self.assertTrue(success)
        self.jpeg = encoded.tobytes()

    def tearDown(self):
        self.client.close()
        if self.previous_dependency is None:
            app.dependency_overrides.pop(_get_db, None)
        else:
            app.dependency_overrides[_get_db] = self.previous_dependency
        if self.previous_runtime is None:
            del app.state.runtime
        else:
            app.state.runtime = self.previous_runtime
        self.engine.dispose()

    def test_quiz_generation_deduplicates_frames_and_maps_crop_coordinates(self):
        with Session(self.engine) as db:
            rules = [
                AlertRule(
                    id=1,
                    name="hand + tool",
                    correct_reason="手が工具に近づいているため",
                ),
                AlertRule(
                    id=2,
                    name="person + tool",
                    correct_reason="人が保護具を着用していないため",
                ),
            ]
            db.add_all(rules)
            db.flush()

            first_frame, first_target = self._frame(db, 1)
            duplicate_frame, duplicate_target = self._frame(db, 2)
            db.add_all(
                [
                    self._event(first_frame, first_target, rules[0], 1),
                    self._event(first_frame, first_target, rules[1], 2),
                    self._event(duplicate_frame, duplicate_target, rules[0], 3),
                ]
            )
            db.commit()

            response = generate_quiz_questions(db, 3)
            self.assertEqual(response["available_question_count"], 2)
            self.assertEqual(len(response["questions"]), 2)
            self.assertIsNone(response["unavailable_reason"])
            frame_ids = {
                db.get(AlertEvent, question["alert_event_id"]).alert_frame_id
                for question in response["questions"]
            }
            self.assertEqual(len(frame_ids), 2)
            rule_ids = {
                db.get(AlertEvent, question["alert_event_id"]).rule_id
                for question in response["questions"]
            }
            self.assertEqual(rule_ids, {1, 2})
            for question in response["questions"]:
                self.assertEqual(len(question["choices"]), 2)
                self.assertEqual(question["correct_choice_id"], "correct")
                correct = next(
                    choice
                    for choice in question["choices"]
                    if choice["id"] == "correct"
                )
                incorrect = next(
                    choice
                    for choice in question["choices"]
                    if choice["id"] == "incorrect"
                )
                self.assertNotEqual(correct["text"], incorrect["text"])
                self.assertEqual(question["image_width"], 320)
                self.assertEqual(question["image_height"], 256)
                self.assertEqual(question["target_bbox"], [64.0, 32.0, 128.0, 96.0])

            first_question = response["questions"][0]
            image = render_quiz_image(db, first_question["alert_event_id"])
            decoded = cv2.imdecode(
                np.frombuffer(image, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            self.assertEqual(decoded.shape[:2], (256, 320))
            db.close()

            api_response = self.client.post(
                "/api/quiz/generate",
                json={"max_questions": 3},
            )
            self.assertEqual(api_response.status_code, 200)
            api_question = api_response.json()["questions"][0]
            image_response = self.client.get(api_question["image_url"])
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response.headers["content-type"], "image/jpeg")
            self.assertEqual(
                self.client.get("/api/quiz/questions/999/image").status_code,
                404,
            )
            self.assertEqual(
                self.client.post(
                    "/api/quiz/generate",
                    json={"max_questions": 4},
                ).status_code,
                422,
            )

    def test_rule_crud_persists_education_configuration(self):
        with self.session_factory.begin() as db:
            model = ModelRecord(
                name="education-model",
                original_filename="education.pt",
                stored_filename="education.pt",
                file_size=1,
            )
            db.add(model)
            db.flush()
            db.add_all(
                [
                    ModelClass(model_id=model.id, class_id=0, class_name="hand"),
                    ModelClass(model_id=model.id, class_id=1, class_name="tool"),
                    ModelSettings(id=1, active_model_id=model.id),
                ]
            )

        payload = {
            "name": "hand + tool",
            "class_names": ["hand", "tool"],
            "education_target_class_name": "hand",
            "correct_reason": "手が工具に近づいているため",
            "explanation": "工具の可動部に巻き込まれる危険があります。",
        }
        created = self.client.post("/api/rules", json=payload)
        self.assertEqual(created.status_code, 201)
        rule = created.json()
        self.assertEqual(rule["education_target_class_name"], "hand")
        self.assertEqual(rule["correct_reason"], payload["correct_reason"])
        self.assertEqual(rule["explanation"], payload["explanation"])

        updated_payload = {
            **payload,
            "education_target_class_name": "tool",
            "correct_reason": "工具の回転部に近づいているため",
        }
        updated = self.client.put(
            f"/api/rules/{rule['id']}",
            json=updated_payload,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["education_target_class_name"], "tool")
        self.assertEqual(updated.json()["correct_reason"], updated_payload["correct_reason"])

        invalid = {
            **payload,
            "education_target_class_name": "person",
        }
        self.assertEqual(self.client.post("/api/rules", json=invalid).status_code, 422)

    def test_quiz_is_unavailable_without_another_rule_reason(self):
        with Session(self.engine) as db:
            rule = AlertRule(
                id=1,
                name="hand + tool",
                correct_reason="手が工具に近づいているため",
            )
            db.add(rule)
            db.flush()
            frame, target = self._frame(db, 1)
            db.add(self._event(frame, target, rule, 1))
            db.commit()

            response = generate_quiz_questions(db, 3)
            self.assertEqual(response["questions"], [])
            self.assertEqual(response["available_question_count"], 0)
            self.assertEqual(response["unavailable_reason"], "2択用の理由が不足しています")

    def _frame(self, db, frame_id):
        frame = AlertFrame(
            source_session_id=f"session-{frame_id}",
            frame_id=frame_id,
            model_name_snapshot="test-model",
            source_type="video",
            captured_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
            width=140,
            height=100,
            image_jpeg=self.jpeg,
            detections=[
                AlertFrameDetection(
                    class_id=0,
                    class_name="hand",
                    confidence=0.95,
                    x1=30,
                    y1=20,
                    x2=50,
                    y2=40,
                ),
                AlertFrameDetection(
                    class_id=1,
                    class_name="tool",
                    confidence=0.9,
                    x1=70,
                    y1=30,
                    x2=90,
                    y2=50,
                ),
            ],
        )
        db.add(frame)
        db.flush()
        return frame, frame.detections[0]

    @staticmethod
    def _event(frame, target, rule, frame_id):
        return AlertEvent(
            alert_frame_id=frame.id,
            rule_id=rule.id,
            rule_name_snapshot=rule.name,
            detected_classes_json='["hand", "tool"]',
            matched_class_names_json='["hand", "tool"]',
            event_type="alert_started",
            source_type="video",
            quiz_target_detection_id=target.id,
            quiz_correct_reason_snapshot=rule.correct_reason,
            quiz_explanation_snapshot="正しい理由を理解して、事故を防止します。",
            crop_x1=10,
            crop_y1=10,
            crop_x2=110,
            crop_y2=90,
            created_at=datetime(
                2026,
                11,
                23,
                0,
                0,
                4 - frame_id,
                tzinfo=timezone.utc,
            ),
        )


class _FakeRuntime:
    def __init__(self):
        self.rules = _FakeRules()

    def configure_rules(self, rules):
        self.rules.values = {
            rule["id"]: {
                "state": "idle",
                "incompatible": False,
                "missing_class_names": [],
            }
            for rule in rules
        }


class _FakeRules:
    def __init__(self):
        self.values = {}

    def statuses(self):
        return self.values


if __name__ == "__main__":
    unittest.main()
