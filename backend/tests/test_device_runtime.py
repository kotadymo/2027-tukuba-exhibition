import unittest
from pathlib import Path
from unittest.mock import call, patch

from app.inference import CUDADeviceError, CUDAUnavailableError
from app.runtime import LiveRuntime, WebSocketHub


class DeviceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = LiveRuntime(WebSocketHub())
        self.model_path = Path("test-model.pt")

    async def test_auto_load_falls_back_to_cpu_and_keeps_requested_mode(self):
        self.runtime._cuda_status = {
            "cuda_available": True,
            "cuda_device_count": 1,
            "gpu_name": "Test GPU",
            "cuda_error": None,
        }
        cpu_detector = _Detector("cpu")
        persisted = []
        with patch(
            "app.runtime.Detector.load",
            side_effect=[CUDADeviceError("CUDA initialization failed"), cpu_detector],
        ) as load:
            status = await self.runtime.activate_model(
                self.model_path,
                7,
                "test model",
                lambda: persisted.append(True),
            )

        self.assertEqual(
            load.call_args_list,
            [
                call(self.model_path, "cuda:0"),
                call(self.model_path, "cpu"),
            ],
        )
        self.assertEqual(persisted, [True])
        self.assertEqual(status["requested_device"], "auto")
        self.assertEqual(status["effective_device"], "cpu")
        self.assertIn("CUDA の初期化に失敗", status["fallback_reason"])

    async def test_unavailable_manual_cuda_does_not_commit_or_switch_to_cpu(self):
        self.runtime._cuda_status = {
            "cuda_available": False,
            "cuda_device_count": 0,
            "gpu_name": None,
            "cuda_error": None,
        }
        persisted = []

        with self.assertRaises(CUDAUnavailableError):
            await self.runtime.switch_device(
                "cuda",
                lambda: persisted.append(True),
            )

        status = self.runtime.get_device_status()
        self.assertEqual(persisted, [])
        self.assertEqual(status["requested_device"], "auto")
        self.assertEqual(status["effective_device"], "none")
        self.assertIn("CUDA", status["device_error"])

    async def test_auto_inference_failure_falls_back_once_to_cpu(self):
        self.runtime._cuda_status["cuda_available"] = True
        self.runtime._install_detector(
            _Detector("cuda:0"),
            7,
            "test model",
            self.model_path,
            "cuda:0",
            None,
        )
        generation = self.runtime.inference.current_model_generation()

        with patch("app.runtime.Detector.load", return_value=_Detector("cpu")) as load:
            await self.runtime._handle_cuda_failure("CUDA inference failed", generation)

        self.assertEqual(load.call_args, call(self.model_path, "cpu"))
        status = self.runtime.get_device_status()
        self.assertEqual(status["requested_device"], "auto")
        self.assertEqual(status["effective_device"], "cpu")
        self.assertIn("CUDA 推論に失敗", status["fallback_reason"])

    async def test_manual_cuda_inference_failure_disables_model_without_cpu_fallback(self):
        self.runtime.requested_device = "cuda"
        self.runtime._install_detector(
            _Detector("cuda:0"),
            7,
            "test model",
            self.model_path,
            "cuda:0",
            None,
        )
        generation = self.runtime.inference.current_model_generation()

        with patch("app.runtime.Detector.load") as load:
            await self.runtime._handle_cuda_failure("CUDA inference failed", generation)

        load.assert_not_called()
        status = self.runtime.get_device_status()
        self.assertEqual(status["requested_device"], "cuda")
        self.assertEqual(status["effective_device"], "none")
        self.assertFalse(status["model_ready"])
        self.assertIn("CPU へは自動切り替えません", status["device_error"])


class _Detector:
    names = {0: "hand", 1: "tool"}

    def __init__(self, device):
        self.device = device


if __name__ == "__main__":
    unittest.main()
