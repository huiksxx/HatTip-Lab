# Third-party notices

The following third-party components are redistributed with HatTip Lab:

- PixiJS 6.5.10 — https://github.com/pixijs/pixijs — MIT License
- pixi-live2d-display 0.4.0, including its Cubism 2 adapter — https://github.com/guansss/pixi-live2d-display — MIT License
- edge-tts 7.2.8 — https://github.com/rany2/edge-tts — GNU Lesser General Public License v3.0

The Windows build preserves the complete `edge-tts` package metadata and license in
its external `_internal` directory. The Python sources are collected as replaceable
files rather than only inside the executable archive.

The Windows package also carries the archived Piper `2023.11.14-2` Windows runtime
from https://github.com/rhasspy/piper. HatTip Lab invokes it as a separate process.
Piper source is MIT licensed; its official binary bundle includes eSpeak NG under
GPL-3.0-or-later, Piper Phonemize under MIT, and ONNX Runtime under MIT. The complete
license texts and runtime provenance are preserved in `assets/piper-runtime/`.

Copyright notices belong to their respective upstream authors.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Live2D Cubism Core and Live2D character model data are deliberately not
redistributed with HatTip Lab.

GPT-SoVITS, user reference audio, and unapproved Piper voice models are also
deliberately not redistributed with HatTip Lab.
