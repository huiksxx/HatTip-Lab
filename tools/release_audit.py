"""Fail a release if proprietary, user-owned, or unapproved voice assets leaked."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLISH_ROOTS = (
    ROOT / "web",
    ROOT / "model-pack-template",
    ROOT / "assets" / "piper",
    ROOT / "assets" / "piper-runtime",
)
BANNED_NAMES = {"live2dcubismcore.min.js", "live2d.min.js"}
BANNED_SUFFIXES = {".moc", ".moc3", ".cmo", ".cmo3", ".can3"}
BANNED_VOICE_SUFFIXES = {".pth", ".ckpt"}
BANNED_USER_DIRS = {"voices", "sovits", "tts-cache"}
# Add a digest only after docs/TTS_MODELS.md records an auditable redistribution grant.
APPROVED_PIPER_MODELS: set[str] = set()
APPROVED_RUNTIME_DIGESTS = {
    "piper.exe": "96f3da3811151580073e40bb4dd20eb0fb8115f5f5f76e2fb54282b3edfa5c1f",
    "espeak-ng.dll": "9588480f8197df62fd8461a8431f8eaec6e8e7749c5ffcbe7fee656fe40a2189",
    "piper_phonemize.dll": "4b5f344b2f31204a8a94a0bf485f93e4971671e81188a0d67f326e113bfb0b2e",
    "onnxruntime.dll": "a630f67f4a82b6689e4178bf81d362d945522dd907b2a92cb43cebf72c83a06f",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check release trees for private Live2D runtime or model data")
    parser.add_argument("paths", nargs="*", type=Path, help="release directories to inspect")
    options = parser.parse_args()
    roots = tuple(path.resolve() for path in options.paths) or PUBLISH_ROOTS
    findings: list[Path] = []
    for base in roots:
        if not base.exists():
            print(f"Release audit target does not exist: {base}")
            return 2
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            parts = {part.casefold() for part in path.parts}
            unapproved_piper = (
                path.suffix.casefold() == ".onnx"
                and "piper" in parts
                and digest(path) not in APPROVED_PIPER_MODELS
            )
            runtime_digest = APPROVED_RUNTIME_DIGESTS.get(path.name.casefold())
            modified_runtime = bool(runtime_digest and digest(path) != runtime_digest)
            user_data_leak = bool(parts & BANNED_USER_DIRS) and "piper-runtime" not in parts
            if (
                path.name.casefold() in BANNED_NAMES
                or path.suffix.casefold() in BANNED_SUFFIXES | BANNED_VOICE_SUFFIXES
                or user_data_leak
                or unapproved_piper
                or modified_runtime
            ):
                try:
                    findings.append(path.relative_to(ROOT))
                except ValueError:
                    findings.append(path)
    if findings:
        print("Release audit failed. Remove these non-redistributable assets:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Release audit passed: no Cubism Core, raw Live2D data, user voices, or unapproved Piper models.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
