# TTS engines and voice-model distribution

HatTip Lab supports three speech layers:

1. **Edge TTS** as the recommended zero-setup option. It uses Microsoft Edge's
   online speech service, requires no API key or downloaded voice model, and is
   redistributed as the LGPL-3.0 `edge-tts` Python package with its package metadata
   and license preserved in the Windows build.
2. **Piper** for fast offline synthesis. Windows cannot install the old MIT Python
   package because its phonemizer has no Windows wheel, while current Python releases
   are GPL-3.0+. HatTip Lab therefore invokes the official archived Windows runtime as
   a separate process. The runtime directory preserves its component license notices.
3. **GPT-SoVITS** as an optional user-installed enhancement. Its repository, Python
   environment, pretrained assets, and user reference audio live under the HatTip Lab
   user-data directory and are not part of this repository or release archive.

## Why no Chinese model is committed yet

The requested `zh_CN-huayan-medium` model card lists its source dataset license as
`Unknown`, so HatTip Lab does not redistribute that model. `zh_CN-xiao_ya-medium` is
marked non-commercial. `zh_CN-chaowen-medium` lists a CC0 fine-tuning dataset but also
states that it was fine-tuned from Xiao Ya, so it is not treated as an unambiguous
commercially redistributable default here.

This is a distribution precaution, not a legal conclusion. A future bundled model
must have an explicit model license and a traceable training-data license that permits
the intended distribution. Until then, users may import a model they are entitled to
use from Settings → Voice.

## User data locations

- Piper models: `%APPDATA%/HatTipLab/piper/`
- GPT-SoVITS installation: `%APPDATA%/HatTipLab/sovits/`
- GPT-SoVITS reference audio: `%APPDATA%/HatTipLab/voices/`

These paths must remain excluded from source control and release packages.

Engine selection records a preferred engine rather than a hard prerequisite. If that
engine is unavailable, HatTip Lab falls back through the other configured engines and
finally keeps the text response. Missing Piper or GPT-SoVITS assets therefore never
block saving unrelated settings or switching Live2D models.
