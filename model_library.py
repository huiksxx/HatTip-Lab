"""Safe, user-owned Live2D model library.

The application ships no character model. Users import a model they created or
downloaded under terms they accepted themselves. Model packs are data-only and
never execute scripts.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


MANIFEST_NAME = "pet-model.json"
APP_DATA_NAME = "HatTipLab"
LEGACY_APP_DATA_NAME = "HermesPet"
DATA_DIR_ENV = "HATTIP_LAB_DATA_DIR"
LEGACY_DATA_DIR_ENV = "HERMES_PET_DATA_DIR"
MAX_FILES = 1200
MAX_TOTAL_BYTES = 600 * 1024 * 1024
MAX_FILE_BYTES = 240 * 1024 * 1024
SAFE_EXTENSIONS = {
    ".json",
    ".moc",
    ".moc3",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mtn",
    ".exp",
    ".wav",
    ".mp3",
    ".ogg",
}


class ModelImportError(RuntimeError):
    """Raised when a model pack is malformed or unsafe."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    entry: str
    format: str
    attribution: str = ""
    license_name: str = "User-provided"
    license_url: str = ""
    redistributable: bool = False
    scale: float = 1.0
    thumbnail: str = ""

    def to_dict(self, base_url: str | None = None) -> dict[str, object]:
        data = asdict(self)
        if base_url:
            encoded_id = quote(self.id, safe="")
            encoded_entry = "/".join(quote(part, safe="") for part in PurePosixPath(self.entry).parts)
            data["url"] = f"{base_url.rstrip('/')}/models/{encoded_id}/{encoded_entry}"
            if self.thumbnail:
                encoded_thumbnail = "/".join(
                    quote(part, safe="") for part in PurePosixPath(self.thumbnail).parts
                )
                data["thumbnail_url"] = (
                    f"{base_url.rstrip('/')}/models/{encoded_id}/{encoded_thumbnail}"
                )
        return data


def default_data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV) or os.environ.get(LEGACY_DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    current = base / APP_DATA_NAME
    legacy = base / LEGACY_APP_DATA_NAME
    return legacy if legacy.exists() and not current.exists() else current


def _safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not path.parts or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise ModelImportError(f"模型包含不安全路径：{value}")
    return path


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_").lower()
    return value[:36] or "live2d-model"


class ModelLibrary:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_data_dir()).resolve()
        self.models_dir = self.root / "models"
        self.runtime_dir = self.root / "runtime"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    @property
    def core_path(self) -> Path:
        return self.runtime_dir / "live2dcubismcore.min.js"

    def runtime_available(self) -> bool:
        return self.core_path.is_file() and self.core_path.stat().st_size > 100_000

    def install_core(self, source: Path) -> None:
        source = source.resolve()
        if source.name.casefold() != "live2dcubismcore.min.js":
            raise ModelImportError("请选择名为 live2dcubismcore.min.js 的官方 Cubism Core 文件")
        if not source.is_file() or not (100_000 <= source.stat().st_size <= 5_000_000):
            raise ModelImportError("Cubism Core 文件大小异常")
        beginning = source.read_bytes()[:4096]
        if b"Live2DCubismCore" not in beginning and b"live2d" not in beginning.lower():
            raise ModelImportError("所选文件不像 Live2D Cubism Core")
        temporary = self.core_path.with_suffix(".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, self.core_path)

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        if not self.models_dir.exists():
            return models
        for manifest_path in sorted(self.models_dir.glob(f"*/{MANIFEST_NAME}")):
            try:
                model = self._read_manifest(manifest_path)
            except (OSError, ValueError, TypeError, ModelImportError):
                continue
            models.append(model)
        return models

    def get(self, model_id: str) -> ModelInfo | None:
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", model_id or ""):
            return None
        manifest = self.models_dir / model_id / MANIFEST_NAME
        try:
            return self._read_manifest(manifest)
        except (OSError, ValueError, TypeError, ModelImportError):
            return None

    def model_root(self, model_id: str) -> Path | None:
        model = self.get(model_id)
        return (self.models_dir / model.id).resolve() if model else None

    def import_model(self, source: Path) -> ModelInfo:
        source = source.expanduser().resolve()
        if not source.exists():
            raise ModelImportError("所选模型不存在")
        if source.is_file() and source.suffix.casefold() == ".zip":
            with tempfile.TemporaryDirectory(prefix="hattip-lab-model-") as temporary:
                extracted = Path(temporary)
                self._extract_zip(source, extracted)
                return self._import_directory(extracted, None)
        if source.is_file() and source.name.casefold().endswith((".model3.json", ".model.json")):
            return self._import_directory(source.parent, source.name)
        if source.is_dir():
            return self._import_directory(source, None)
        raise ModelImportError("请选择 .model3.json、.model.json、模型目录或 ZIP")

    def _extract_zip(self, archive_path: Path, destination: Path) -> None:
        file_count = 0
        total_size = 0
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile as exc:
            raise ModelImportError("模型 ZIP 已损坏") from exc
        with archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                relative = _safe_relative(item.filename)
                if relative.suffix.casefold() not in SAFE_EXTENSIONS and relative.name != MANIFEST_NAME:
                    continue
                file_count += 1
                total_size += item.file_size
                if file_count > MAX_FILES or total_size > MAX_TOTAL_BYTES or item.file_size > MAX_FILE_BYTES:
                    raise ModelImportError("模型包超过安全大小限制")
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    def _discover_entry(self, source_root: Path, selected: str | None) -> Path:
        if selected:
            candidate = source_root / selected
            if candidate.is_file():
                return candidate
        candidates = list(source_root.rglob("*.model3.json")) + list(source_root.rglob("*.model.json"))
        candidates = [path for path in candidates if path.name != MANIFEST_NAME]
        candidates.sort(key=lambda path: (len(path.relative_to(source_root).parts), str(path).casefold()))
        if not candidates:
            raise ModelImportError("没有找到 .model3.json 或 .model.json")
        return candidates[0]

    def _validate_model_settings(self, entry: Path, root: Path) -> tuple[str, dict[str, Any]]:
        try:
            settings = json.loads(entry.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ModelImportError("模型设置 JSON 无法读取") from exc
        if not isinstance(settings, dict):
            raise ModelImportError("模型设置 JSON 格式无效")
        format_name = "cubism4" if entry.name.casefold().endswith(".model3.json") else "cubism2"
        references: list[str] = []
        if format_name == "cubism4":
            file_refs = settings.get("FileReferences", {})
            if not isinstance(file_refs, dict) or not file_refs.get("Moc"):
                raise ModelImportError("Cubism 3/4 模型缺少 FileReferences.Moc")
            references.append(str(file_refs["Moc"]))
            references.extend(str(item) for item in file_refs.get("Textures", []) if isinstance(item, str))
        else:
            if not settings.get("model"):
                raise ModelImportError("Cubism 2 模型缺少 model 文件引用")
            references.append(str(settings["model"]))
            references.extend(str(item) for item in settings.get("textures", []) if isinstance(item, str))
        entry_root = entry.parent
        for reference in references:
            relative = _safe_relative(reference)
            resolved = entry_root.joinpath(*relative.parts).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                raise ModelImportError(f"模型缺少引用文件：{reference}")
        return format_name, settings

    def _source_metadata(self, root: Path, entry: Path) -> dict[str, Any]:
        manifests = [entry.parent / MANIFEST_NAME, root / MANIFEST_NAME]
        for path in manifests:
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
        return {}

    @staticmethod
    def _discover_thumbnail(root: Path, entry: Path, preferred: Any = "") -> str:
        if isinstance(preferred, str) and preferred.strip():
            try:
                relative = _safe_relative(preferred.strip())
                candidate = root.joinpath(*relative.parts).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    return candidate.relative_to(root).as_posix()
            except (OSError, ValueError, ModelImportError):
                pass
        images = [
            path
            for path in entry.parent.rglob("*")
            if path.is_file() and path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        images.sort(
            key=lambda path: (
                0
                if any(word in path.stem.casefold() for word in ("thumbnail", "preview", "icon"))
                else 1,
                len(path.relative_to(root).parts),
                str(path).casefold(),
            )
        )
        return images[0].relative_to(root).as_posix() if images else ""

    def _import_directory(self, source_root: Path, selected: str | None) -> ModelInfo:
        source_root = source_root.resolve()
        entry = self._discover_entry(source_root, selected)
        format_name, _ = self._validate_model_settings(entry, source_root)
        metadata = self._source_metadata(source_root, entry)
        display = metadata.get("display", {}) if isinstance(metadata.get("display"), dict) else {}
        license_data = metadata.get("license", {}) if isinstance(metadata.get("license"), dict) else {}
        name = str(metadata.get("name") or entry.name.split(".model", 1)[0]).strip()[:80]
        model_id = f"{_slugify(name)}-{secrets.token_hex(3)}"
        staging = self.models_dir / f".installing-{model_id}"
        destination = self.models_dir / model_id
        staging.mkdir(parents=True, exist_ok=False)
        try:
            count = 0
            total_size = 0
            for item in source_root.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                relative = item.relative_to(source_root)
                if item.suffix.casefold() not in SAFE_EXTENSIONS and item.name != MANIFEST_NAME:
                    continue
                count += 1
                size = item.stat().st_size
                total_size += size
                if count > MAX_FILES or total_size > MAX_TOTAL_BYTES or size > MAX_FILE_BYTES:
                    raise ModelImportError("模型目录超过安全大小限制")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            relative_entry = entry.relative_to(source_root).as_posix()
            thumbnail = self._discover_thumbnail(
                source_root, entry, display.get("thumbnail", "")
            )
            model = ModelInfo(
                id=model_id,
                name=name or "Live2D Model",
                entry=relative_entry,
                format=format_name,
                attribution=str(license_data.get("attribution", ""))[:500],
                license_name=str(license_data.get("name", "User-provided"))[:100],
                license_url=str(license_data.get("url", ""))[:500],
                redistributable=bool(license_data.get("redistributable", False)),
                scale=max(0.1, min(5.0, float(display.get("scale", 1.0)))),
                thumbnail=thumbnail,
            )
            (staging / MANIFEST_NAME).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        **asdict(model),
                        "license_confirmed_by_user": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(staging, destination)
            return model
        except Exception:
            if staging.exists() and staging.is_relative_to(self.models_dir):
                shutil.rmtree(staging)
            raise

    def _read_manifest(self, path: Path) -> ModelInfo:
        data = json.loads(path.read_text(encoding="utf-8"))
        model_id = str(data["id"])
        if path.parent.name != model_id or not re.fullmatch(r"[a-z0-9_-]{1,64}", model_id):
            raise ModelImportError("模型 ID 无效")
        entry = _safe_relative(str(data["entry"])).as_posix()
        if not path.parent.joinpath(*PurePosixPath(entry).parts).is_file():
            raise ModelImportError("模型入口不存在")
        return ModelInfo(
            id=model_id,
            name=str(data["name"])[:80],
            entry=entry,
            format=str(data["format"]),
            attribution=str(data.get("attribution", ""))[:500],
            license_name=str(data.get("license_name", "User-provided"))[:100],
            license_url=str(data.get("license_url", ""))[:500],
            redistributable=bool(data.get("redistributable", False)),
            scale=float(data.get("scale", 1.0)),
            thumbnail=self._discover_thumbnail(
                path.parent, path.parent / entry, data.get("thumbnail", "")
            ),
        )
