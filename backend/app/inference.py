from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ModelValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    bbox: list[float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox": self.bbox,
        }


class Detector:
    def __init__(self, model: Any, names: dict[int, str]):
        self.model = model
        self.names = names

    @classmethod
    def load(cls, path: Path) -> Detector:
        try:
            import numpy as np
            from ultralytics import YOLO

            model = YOLO(str(path))
            if getattr(model, "task", None) != "detect":
                raise ModelValidationError("物体検出（detect）モデルではありません")

            raw_names = model.names
            if isinstance(raw_names, dict):
                names = {int(class_id): str(name) for class_id, name in raw_names.items()}
            else:
                names = {class_id: str(name) for class_id, name in enumerate(raw_names)}
            if not names or any(not name.strip() for name in names.values()):
                raise ModelValidationError("モデルから有効なクラス一覧を取得できません")

            sample = np.zeros((416, 416, 3), dtype=np.uint8)
            model.predict(
                source=sample,
                device="cpu",
                imgsz=416,
                conf=0.4,
                iou=0.7,
                verbose=False,
            )
            return cls(model, names)
        except ModelValidationError:
            raise
        except Exception as exc:
            raise ModelValidationError(f"モデルを読み込めません: {exc}") from exc

    def predict(
        self,
        frame,
        confidence: float,
        iou_threshold: float,
        imgsz: int,
    ) -> list[Detection]:
        import numpy as np

        height, width = frame.shape[:2]
        results = self.model.predict(
            source=frame,
            device="cpu",
            conf=confidence,
            iou=iou_threshold,
            imgsz=imgsz,
            verbose=False,
        )
        if not results or results[0].boxes is None:
            return []

        boxes = results[0].boxes
        coordinates = boxes.xyxy.cpu().numpy()
        class_ids = boxes.cls.cpu().numpy().astype(np.int32)
        confidences = boxes.conf.cpu().numpy()
        detections = []
        for box, class_id, score in zip(coordinates, class_ids, confidences):
            class_name = self.names.get(int(class_id))
            if class_name is None:
                continue
            x1, y1, x2, y2 = (float(value) for value in box)
            detections.append(
                Detection(
                    class_id=int(class_id),
                    class_name=class_name,
                    confidence=float(score),
                    bbox=[
                        max(0.0, min(float(width), x1)),
                        max(0.0, min(float(height), y1)),
                        max(0.0, min(float(width), x2)),
                        max(0.0, min(float(height), y2)),
                    ],
                )
            )
        return detections
