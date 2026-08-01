# Bundled Piper Windows runtime

- Upstream: https://github.com/rhasspy/piper
- Release: `2023.11.14-2`
- Asset: `piper_windows_amd64.zip`
- Download SHA-256: `F3C58906402B24F3A96D92145F58ACBA6D86C9B5DB896D207F78DC80811EFCEA`
- Release source: https://github.com/rhasspy/piper/releases/tag/2023.11.14-2

HatTip Lab starts `piper.exe` as a separate process. The application does not import,
link to, or modify this runtime. Piper's own source is MIT licensed, while the official
runtime bundle includes eSpeak NG under GPL-3.0-or-later. License texts for the bundled
components are in `licenses/`. Anyone redistributing this directory must preserve them
and independently verify the corresponding-source obligations that apply to their
distribution channel.

No voice `.onnx` model is included in this directory.
