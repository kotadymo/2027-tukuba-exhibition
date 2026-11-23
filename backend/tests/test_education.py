import random
import unittest
from datetime import datetime, timedelta, timezone

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
    HazardReason,
    ModelClass,
    ModelRecord,
    ModelSettings,
)
from app.education import _select_candidates, generate_quiz_questions, render_quiz_image
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
            reasons = [
                HazardReason(
                    id=1,
                    name="hand hazard",
                    correct_reason="手が工具に近づいているため",
                    wrong_reason="工具の色が明るいため",
                    explanation="工具の可動部に巻き込まれる危険があります。",
                ),
                HazardReason(
                    id=2,
                    name="PPE hazard",
                    correct_reason="保護具を着用していないため",
                    wrong_reason="作業台が広いため",
                    explanation="必要な保護具を着用してください。",
                ),
            ]
            db.add_all(reasons)
            db.flush()
            rules = [
                AlertRule(
                    id=1,
                    name="hand + tool",
                    hazard_reason_id=1,
                ),
                AlertRule(
                    id=2,
                    name="person + tool",
                    hazard_reason_id=2,
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
            self.assertEqual(
                {question["hazard_reason_id"] for question in response["questions"]},
                {1, 2},
            )
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

        master_payload = {
            "name": "moving tool",
            "correct_reason": "手が工具に近づいているため",
            "wrong_reason": "工具の色が明るいため",
            "explanation": "工具の可動部に巻き込まれる危険があります。",
            "enabled": True,
        }
        created_master = self.client.post("/api/hazard-reasons", json=master_payload)
        self.assertEqual(created_master.status_code, 201)
        hazard_reason_id = created_master.json()["id"]
        self.assertEqual(created_master.json()["reference_rule_count"], 0)
        self.assertEqual(
            self.client.post(
                "/api/hazard-reasons",
                json={
                    **master_payload,
                    "name": "duplicate correct answer",
                    "correct_reason": "  手が工具に近づいているため ",
                    "wrong_reason": "別の誤答",
                },
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/api/hazard-reasons",
                json={
                    **master_payload,
                    "correct_reason": "同じ 文章",
                    "wrong_reason": "同じ   文章",
                },
            ).status_code,
            422,
        )

        payload = {
            "name": "hand + tool",
            "class_names": ["hand", "tool"],
            "education_target_class_name": "hand",
            "hazard_reason_id": hazard_reason_id,
        }
        created = self.client.post("/api/rules", json=payload)
        self.assertEqual(created.status_code, 201)
        rule = created.json()
        self.assertEqual(rule["education_target_class_name"], "hand")
        self.assertEqual(rule["hazard_reason_id"], hazard_reason_id)

        shared_rule = self.client.post(
            "/api/rules",
            json={**payload, "name": "second rule using shared reason"},
        )
        self.assertEqual(shared_rule.status_code, 201)
        self.assertEqual(
            self.client.get("/api/hazard-reasons").json()[0]["reference_rule_count"],
            2,
        )
        self.assertEqual(
            self.client.delete(f"/api/hazard-reasons/{hazard_reason_id}").status_code,
            409,
        )

        updated_payload = {
            **payload,
            "education_target_class_name": "tool",
        }
        updated = self.client.put(
            f"/api/rules/{rule['id']}",
            json=updated_payload,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["education_target_class_name"], "tool")
        self.assertEqual(updated.json()["hazard_reason_id"], hazard_reason_id)

        invalid = {
            **payload,
            "education_target_class_name": "person",
        }
        self.assertEqual(self.client.post("/api/rules", json=invalid).status_code, 422)

    def test_quiz_without_events_reports_no_saved_material(self):
        with Session(self.engine) as db:
            response = generate_quiz_questions(db, 3)
            self.assertEqual(response["questions"], [])
            self.assertEqual(response["available_question_count"], 0)
            self.assertEqual(response["unavailable_reason"], "危険フレームがまだありません")

    def test_same_reason_from_ten_events_and_rules_generates_one_question(self):
        with Session(self.engine) as db:
            reason = HazardReason(
                id=1,
                name="shared reason",
                correct_reason="手が工具に近づいているため",
                wrong_reason="工具の色が明るいため",
                explanation="工具の可動部に巻き込まれる危険があります。",
            )
            rules = [
                AlertRule(id=1, name="hand + tool", hazard_reason=reason),
                AlertRule(id=2, name="hand + machine", hazard_reason=reason),
            ]
            db.add_all([reason, *rules])
            db.flush()
            for frame_id in range(1, 11):
                frame, target = self._frame(db, frame_id)
                rule = rules[frame_id % len(rules)]
                db.add(self._event(frame, target, rule, frame_id))
            db.commit()

            response = generate_quiz_questions(db, 3)
            self.assertEqual(response["available_question_count"], 1)
            event_ids = [question["alert_event_id"] for question in response["questions"]]
            self.assertEqual(len(set(event_ids)), 1)
            frame_ids = {
                db.get(AlertEvent, event_id).alert_frame_id for event_id in event_ids
            }
            self.assertEqual(len(frame_ids), 1)
            self.assertEqual(
                {question["hazard_reason_id"] for question in response["questions"]},
                {reason.id},
            )
            self.assertEqual(
                {rule.hazard_reason_id for rule in rules},
                {reason.id},
            )

    def test_candidate_selection_uses_distinct_reasons_and_caps_at_three(self):
        for reason_count, expected_count in ((2, 2), (3, 3), (4, 3)):
            with self.subTest(reason_count=reason_count):
                candidates = []
                event_id = 1
                frame_id = 1
                for reason_id in range(1, reason_count + 1):
                    for rule_id in (reason_id * 10, reason_id * 10 + 1):
                        candidates.append(
                            self._selection_candidate(
                                event_id=event_id,
                                frame_id=frame_id,
                                hazard_reason_id=reason_id,
                                rule_id=rule_id,
                            )
                        )
                        event_id += 1
                        frame_id += 1

                selected = _select_candidates(candidates, 3)
                self.assertEqual(len(selected), expected_count)
                self.assertEqual(
                    len({candidate["hazard_reason_id"] for candidate in selected}),
                    expected_count,
                )
                self.assertEqual(
                    len({candidate["frame_id"] for candidate in selected}),
                    expected_count,
                )
                self.assertEqual(
                    len({candidate["event_id"] for candidate in selected}),
                    expected_count,
                )

    def test_candidate_selection_randomizes_events_and_question_order(self):
        candidates = [
            self._selection_candidate(event_id, frame_id, reason_id, reason_id)
            for reason_id in range(1, 4)
            for event_id, frame_id in (
                (reason_id * 10, reason_id * 10),
                (reason_id * 10 + 1, reason_id * 10 + 1),
            )
        ]
        selections = [
            _select_candidates(candidates, 3, random.Random(seed))
            for seed in range(20)
        ]

        for selected in selections:
            self.assertEqual(len(selected), 3)
            self.assertEqual(
                len({candidate["hazard_reason_id"] for candidate in selected}),
                3,
            )
            self.assertEqual(
                len({candidate["frame_id"] for candidate in selected}),
                3,
            )
            self.assertEqual(
                len({candidate["event_id"] for candidate in selected}),
                3,
            )

        event_sets = {
            frozenset(candidate["event_id"] for candidate in selected)
            for selected in selections
        }
        question_orders = {
            tuple(candidate["event_id"] for candidate in selected)
            for selected in selections
        }
        self.assertGreater(len(event_sets), 1)
        self.assertGreater(len(question_orders), 1)

    def test_candidate_selection_uses_alternate_frames_for_distinct_reasons(self):
        candidates = [
            self._selection_candidate(1, 1, 1, 1),
            self._selection_candidate(2, 1, 2, 2),
            self._selection_candidate(3, 2, 2, 3),
            self._selection_candidate(4, 2, 3, 4),
            self._selection_candidate(5, 3, 3, 5),
        ]

        selected = _select_candidates(candidates, 3)

        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {candidate["hazard_reason_id"] for candidate in selected},
            {1, 2, 3},
        )
        self.assertEqual(
            len({candidate["frame_id"] for candidate in selected}),
            3,
        )
        self.assertEqual(
            len({candidate["event_id"] for candidate in selected}),
            3,
        )

    def test_candidate_selection_does_not_repeat_reasons_on_a_shared_frame(self):
        candidates = [
            self._selection_candidate(1, 1, 1, 1),
            self._selection_candidate(2, 1, 2, 2),
        ]

        selected = _select_candidates(candidates, 3)

        self.assertEqual(len(selected), 1)
        self.assertEqual(len({candidate["frame_id"] for candidate in selected}), 1)

    def test_invalid_target_geometry_is_not_a_quiz_candidate(self):
        with Session(self.engine) as db:
            reason = HazardReason(
                id=1,
                name="invalid target",
                correct_reason="手が工具に近いため",
                wrong_reason="工具の色が明るいため",
                explanation="工具の可動部に注意してください。",
            )
            rule = AlertRule(id=1, name="hand + tool", hazard_reason=reason)
            db.add_all([reason, rule])
            db.flush()
            frame, target = self._frame(db, 1)
            target.x2 = target.x1 + 1
            db.add(self._event(frame, target, rule, 1))
            db.commit()

            response = generate_quiz_questions(db, 3)

            self.assertEqual(response["questions"], [])
            self.assertEqual(response["available_question_count"], 0)

    def test_empty_saved_image_is_not_a_quiz_candidate(self):
        with Session(self.engine) as db:
            reason = HazardReason(
                id=1,
                name="missing image",
                correct_reason="手が工具に近いため",
                wrong_reason="工具の色が明るいため",
                explanation="工具の可動部に注意してください。",
            )
            rule = AlertRule(id=1, name="hand + tool", hazard_reason=reason)
            db.add_all([reason, rule])
            db.flush()
            frame, target = self._frame(db, 1)
            frame.image_jpeg = b""
            db.add(self._event(frame, target, rule, 1))
            db.commit()

            response = generate_quiz_questions(db, 3)

            self.assertEqual(response["questions"], [])
            self.assertEqual(response["available_question_count"], 0)

    def test_quiz_generation_does_not_select_jpeg_blobs(self):
        from sqlalchemy import event as sqlalchemy_event

        with Session(self.engine) as db:
            reason = HazardReason(
                id=1,
                name="shared reason",
                correct_reason="手が工具に近づいているため",
                wrong_reason="工具の色が明るいため",
                explanation="工具の可動部に巻き込まれる危険があります。",
            )
            rule = AlertRule(id=1, name="hand + tool", hazard_reason=reason)
            db.add_all([reason, rule])
            db.flush()
            frame, target = self._frame(db, 1)
            db.add(self._event(frame, target, rule, 1))
            db.commit()

            statements = []

            def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.lower())

            sqlalchemy_event.listen(self.engine, "before_cursor_execute", capture_statement)
            try:
                generate_quiz_questions(db, 3)
            finally:
                sqlalchemy_event.remove(
                    self.engine,
                    "before_cursor_execute",
                    capture_statement,
                )
            quiz_selects = [
                statement
                for statement in statements
                if "select" in statement and "alert_events" in statement
            ]
            self.assertTrue(quiz_selects)
            self.assertTrue(
                all(
                    "image_jpeg"
                    not in " ".join(statement.split()).split(" from ", 1)[0]
                    for statement in quiz_selects
                )
            )

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
    def _selection_candidate(event_id, frame_id, hazard_reason_id, rule_id):
        return {
            "event_id": event_id,
            "frame_id": frame_id,
            "hazard_reason_id": hazard_reason_id,
            "rule_id": rule_id,
        }

    @staticmethod
    def _event(frame, target, rule, frame_id):
        return AlertEvent(
            alert_frame_id=frame.id,
            rule_id=rule.id,
            hazard_reason_id=rule.hazard_reason_id,
            rule_name_snapshot=rule.name,
            detected_classes_json='["hand", "tool"]',
            matched_class_names_json='["hand", "tool"]',
            event_type="alert_started",
            source_type="video",
            quiz_target_detection_id=target.id,
            quiz_correct_reason_snapshot="手が工具に近づいているため",
            quiz_wrong_reason_snapshot="工具の色が明るいため",
            quiz_explanation_snapshot="正しい理由を理解して、事故を防止します。",
            crop_x1=10,
            crop_y1=10,
            crop_x2=110,
            crop_y2=90,
            created_at=datetime(2026, 11, 23, tzinfo=timezone.utc)
            - timedelta(seconds=frame_id),
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
