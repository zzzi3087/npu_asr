# Third-party notices

The program uses independently licensed open-source components. Full license texts
and installed dependency metadata are included in `licenses/third-party/` in the source
and `third-party-licenses/` in the
Windows distribution. Python and Qt libraries are dynamically distributed; their
license terms and replacement rights continue to apply.

- FluidInference/eddy: Apache-2.0; architecture and OpenVINO TDT decoder conventions.
  https://github.com/FluidInference/eddy-audio
- Nerdstorm/LiveTranscribe: MIT, copyright 2026 Nerdstorm; Qwen split-model export
  layout and adapted exporter. https://github.com/Nerdstorm/LiveTranscribe
- Silero VAD: MIT, copyright 2020-present Silero Team.
  https://github.com/snakers4/silero-vad
- OpenVINO: Apache-2.0. https://github.com/openvinotoolkit/openvino
- PySide6/Qt: LGPL-3.0 and component-specific licenses.
  https://www.qt.io/qt-for-python and https://download.qt.io/archive/qt/
- Python: PSF license. https://www.python.org/downloads/source/
- ONNX Runtime: MIT. https://github.com/microsoft/onnxruntime
- FFmpeg: LGPL-3.0-or-later shared build (BtbN, --enable-version3, no GPL/nonfree
  variant). Launched as a separate command-line process for local media analysis
  and decoding; the shared libraries may be replaced with compatible builds.
  Its license is in `media-tools/LICENSE.txt` in the Windows distribution;
  GPL-3.0 and LGPL-3.0 texts are also in `third-party-licenses/`.
  Pinned FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/29e619e767
  Build recipes and dependencies: https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-09-30-13-08
  Binary provenance and archive hash: `resources/media-tools.json` in source,
  `media-tools/BUILD.json` in the distribution. FFmpeg authors and contributors
  retain copyright; this program does not modify the FFmpeg binaries.
- NumPy, Requests, SentencePiece, Tokenizers, SoundDevice, SoundFile, soxr,
  PyAudioWPatch, Keyring and their dependencies retain their respective licenses.

## Separately downloaded models

No model weights are included in the application installer or portable ZIP.

- NVIDIA Parakeet TDT 0.6B v2, OpenVINO conversion by FluidInference: CC-BY-4.0.
  https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v2-ov
  Original: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2
- Qwen3-ASR 1.7B: Apache-2.0. https://huggingface.co/Qwen/Qwen3-ASR-1.7B
- NVIDIA Parakeet TDT 0.6B v3, OpenVINO conversion by FluidInference: CC-BY-4.0.
  https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-ov
  Original model and tokenizer: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
  Model graphs are unmodified; application DSP, decoding and segmentation are adapted.
- Mega-ASR: Apache-2.0. https://huggingface.co/zhifeixie/Mega-ASR
- Silero VAD: MIT. https://github.com/snakers4/silero-vad

Qwen and Mega files distributed separately are derived OpenVINO conversions:
FP16 audio graphs, symmetric INT8 text weights and an embedding lookup table.
Mega is the merged robust branch with routing disabled. The pinned public sources,
SHA-256 checksums and download locations are recorded in `resources/model-catalog.json`.
These conversions are community builds, not official upstream binary releases.
