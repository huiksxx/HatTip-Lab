# Piper voice slot

HatTip Lab does not currently redistribute a Chinese voice model from this folder.

Before adding an `.onnx` file and its matching `.onnx.json` file to a public build,
record the model license, training dataset license, source URL, version/commit, and
SHA-256 digest in `docs/TTS_MODELS.md`. A repository-level license is not enough when
the model card declares the source dataset unknown or non-commercial.

Users can import a locally obtained Piper model from Settings → Voice. Imported models
are copied to `%APPDATA%/HatTipLab/piper` and are never included in a release archive.
