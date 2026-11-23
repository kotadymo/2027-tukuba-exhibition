from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ModelValidationError(ValueError):
    pass


class CUDADeviceError(ModelValidationError):
    pass


class CUDAUnavailableError(RuntimeError):
    pass


def get_cuda_status() -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        return {
            "cuda_available": False,
            "cuda_device_count": 0,
            "gpu_name": None,
            "cuda_error": f"PyTorch CUDA 情報を取得できません: {exc}",
        }

    try:
        available = bool(torch.cuda.is_available())
        count = int(torch.cuda.device_count()) if available else 0
        gpu_name = torch.cuda.get_device_name(0) if count else None
    except Exception as exc:
        return {
            "cuda_available": False,
            "cuda_device_count": 0,
            "gpu_name": None,
            "cuda_error": f"CUDA の状態確認に失敗しました: {exc}",
        }
    return {
        "cuda_available": available,
        "cuda_device_count": count,
        "gpu_name": gpu_name,
        "cuda_error": None,
    }


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
    def __init__(self, model: Any, names: dict[int, str], device: str = "cpu"):
        self.model = model
        self.names = names
        self.device = device

    @classmethod
    def load(cls, path: Path, device: str = "cpu") -> Detector:
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
            try:
                model.predict(
                    source=sample,
                    device=device,
                    imgsz=416,
                    conf=0.4,
                    iou=0.7,
                    verbose=False,
                )
            except Exception as exc:
                if device.startswith("cuda"):
                    raise CUDADeviceError(
                        f"CUDA でモデルを初期化できません: {exc}"
                    ) from exc
                raise ModelValidationError(
                    f"CPU でモデルを検証できません: {exc}"
                ) from exc
            return cls(model, names, device)
        except CUDADeviceError:
            raise
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
        try:
            results = self.model.predict(
                source=frame,
                device=self.device,
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
        except CUDADeviceError:
            raise
        except Exception as exc:
            if self.device.startswith("cuda"):
                raise CUDADeviceError(f"CUDA 推論に失敗しました: {exc}") from exc
            raise
