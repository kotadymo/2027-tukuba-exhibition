import unittest

from app.inference import Detection
from app.runtime import RuleEngine


class RuleEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = RuleEngine()
        self.engine.configure(
            [
                {
                    "id": 1,
                    "name": "two objects",
                    "class_names": ["hand", "tool"],
                    "enabled": True,
                    "detection_duration_ms": 500,
                    "release_duration_ms": 1000,
                    "cooldown_ms": 10000,
                }
            ]
        )
        self.engine.set_model_classes({"hand", "tool"})
        self.both_detected = [
            Detection(0, "hand", 0.9, [0, 0, 10, 10]),
            Detection(1, "tool", 0.9, [10, 10, 20, 20]),
        ]

    def evaluate(self, frame_time, detections=None):
        return self.engine.evaluate(
            detections=detections if detections is not None else self.both_detected,
            frame_time=frame_time,
            source_type="video",
            video_id=7,
            media_time_ms=int(frame_time * 1000),
            model_id=3,
        )

    def test_requires_all_classes_in_consecutive_frames_and_duration(self):
        matched, events = self.evaluate(
            0.0, [Detection(0, "hand", 0.9, [0, 0, 10, 10])]
        )
        self.assertEqual(matched, [])
        self.assertEqual(events, [])

        matched, events = self.evaluate(0.1)
        self.assertEqual(matched, [1])
        self.assertEqual(events, [])
        _, events = self.evaluate(0.4)
        self.assertEqual(events, [])
        _, events = self.evaluate(0.6)
        self.assertEqual([event["type"] for event in events], ["alert_started"])

    def test_failed_pending_condition_resets_continuity(self):
        self.evaluate(0.0)
        self.evaluate(0.3)
        self.evaluate(0.4, [])
        _, events = self.evaluate(0.6)
        self.assertEqual(events, [])
        _, events = self.evaluate(1.1)
        self.assertEqual([event["type"] for event in events], ["alert_started"])

    def test_active_alert_releases_and_cooldown_blocks_repeat_until_expired(self):
        self.evaluate(0.0)
        _, events = self.evaluate(0.5)
        self.assertEqual([event["type"] for event in events], ["alert_started"])
        self.evaluate(0.8, [])
        _, events = self.evaluate(1.8, [])
        self.assertEqual([event["type"] for event in events], ["alert_cleared"])

        self.evaluate(2.0)
        self.evaluate(2.5)
        for frame_time in (3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5):
            _, events = self.evaluate(frame_time)
            self.assertEqual(events, [])
        _, events = self.evaluate(10.5)
        self.assertEqual([event["type"] for event in events], ["alert_started"])

    def test_model_incompatibility_excludes_rule(self):
        self.engine.set_model_classes({"hand"})
        status = self.engine.statuses()[1]
        self.assertTrue(status["incompatible"])
        self.assertEqual(status["missing_class_names"], ["tool"])
        matched, events = self.evaluate(0.0)
        self.assertEqual(matched, [])
        self.assertEqual(events, [])

    def test_reset_can_clear_cooldown(self):
        self.evaluate(0.0)
        _, events = self.evaluate(0.5)
        self.assertEqual([event["type"] for event in events], ["alert_started"])
        events = self.engine.reset(clear_cooldown=True, reason="video_restart")
        self.assertEqual([event["type"] for event in events], ["alert_cleared"])
        self.evaluate(0.0)
        _, events = self.evaluate(0.5)
        self.assertEqual([event["type"] for event in events], ["alert_started"])


if __name__ == "__main__":
    unittest.main()
