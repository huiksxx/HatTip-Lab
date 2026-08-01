from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from model_library import MANIFEST_NAME, ModelImportError, ModelLibrary, default_data_dir


def make_cubism4_model(root: Path) -> Path:
    (root / "textures").mkdir(parents=True)
    (root / "avatar.moc3").write_bytes(b"moc-data")
    (root / "textures" / "avatar.png").write_bytes(b"png-data")
    entry = root / "avatar.model3.json"
    entry.write_text(
        json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "avatar.moc3",
                    "Textures": ["textures/avatar.png"],
                },
            }
        ),
        encoding="utf-8",
    )
    return entry


class ModelLibraryTests(unittest.TestCase):
    def test_default_data_dir_uses_new_brand_and_preserves_legacy_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=False):
                os.environ.pop("HATTIP_LAB_DATA_DIR", None)
                os.environ.pop("HERMES_PET_DATA_DIR", None)
                self.assertEqual(default_data_dir(), base / "HatTipLab")

                legacy = base / "HermesPet"
                legacy.mkdir()
                self.assertEqual(default_data_dir(), legacy)

                current = base / "HatTipLab"
                current.mkdir()
                self.assertEqual(default_data_dir(), current)

    def test_imports_data_only_model_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            entry = make_cubism4_model(source)
            (source / "dangerous.js").write_text("alert(1)", encoding="utf-8")
            (source / "pet-model.json").write_text(
                json.dumps(
                    {
                        "name": "My Avatar",
                        "license": {"attribution": "© Test", "redistributable": False},
                        "display": {"scale": 1.2},
                    }
                ),
                encoding="utf-8",
            )
            library = ModelLibrary(base / "data")

            model = library.import_model(entry)

            self.assertEqual(model.name, "My Avatar")
            self.assertEqual(model.format, "cubism4")
            self.assertEqual(model.scale, 1.2)
            installed = library.models_dir / model.id
            self.assertTrue((installed / MANIFEST_NAME).is_file())
            self.assertTrue((installed / "avatar.moc3").is_file())
            self.assertFalse((installed / "dangerous.js").exists())
            self.assertEqual(library.get(model.id), model)

    def test_rejects_missing_model_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            entry = make_cubism4_model(base)
            (base / "avatar.moc3").unlink()
            library = ModelLibrary(base / "data")
            with self.assertRaises(ModelImportError):
                library.import_model(entry)

    def test_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../avatar.model3.json", "{}")
            library = ModelLibrary(base / "data")
            with self.assertRaises(ModelImportError):
                library.import_model(archive)

    def test_installs_user_supplied_core(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "live2dcubismcore.min.js"
            source.write_bytes(b"var Live2DCubismCore=" + b"x" * 120_000)
            library = ModelLibrary(base / "data")
            library.install_core(source)
            self.assertTrue(library.runtime_available())


if __name__ == "__main__":
    unittest.main()
