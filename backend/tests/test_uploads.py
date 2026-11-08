import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import upload_model


class ModelUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_validation_uses_a_supported_temporary_file_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine("sqlite://")
            Base.metadata.create_all(engine)
            try:
                with Session(engine) as db:
                    upload = UploadFile(
                        filename="detector.pt",
                        file=io.BytesIO(b"model contents"),
                    )
                    with patch("app.main.MODELS_DIR", Path(directory)):
                        with patch(
                            "app.main.Detector.load",
                            return_value=SimpleNamespace(names={0: "person"}),
                        ) as load_model:
                            result = await upload_model(upload, db)

                    temporary_path = load_model.call_args.args[0]
                    model_files = list(Path(directory).glob("*.pt"))
                    self.assertEqual(temporary_path.suffix, ".pt")
                    self.assertTrue(temporary_path.name.endswith(".uploading.pt"))
                    self.assertFalse(temporary_path.exists())
                    self.assertEqual(len(model_files), 1)
                    self.assertTrue(model_files[0].is_file())
                    self.assertEqual(
                        result["classes"],
                        [{"class_id": 0, "class_name": "person"}],
                    )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
