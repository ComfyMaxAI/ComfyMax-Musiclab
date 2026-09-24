# Optional SheetSage2 component

Run `Install_SheetSage.bat` from the Audio Chunker folder after normal setup.
SheetSage2 is optional. Missing components never prevent opening saved projects.

## Requirements and installation

Windows x64, the existing Audio Chunker Python 3.11 environment, internet for first setup,
and about 10 GB free during installation. The pinned CUDA 13.3 runtime requires a compatible
NVIDIA driver; tested hardware is RTX 5060 Ti. Download size: approximately 3.55 GB, including
the 2.71 GB model, the 270 MB audio.cpp archive and 575 MB CUDA library archive.
No separate Audio.cpp installation, CUDA toolkit or model path editing is needed.
CPU-only machines are not the validated tester target; see the phase report for CPU tests.

The installer prints progress, verifies SHA256, stages before activation, and preserves valid
installed files. Retry with the same BAT after network failure or Ctrl+C. Completed downloads
are reused only after verification. Partial downloads restart. A forcibly killed setup may
leave `.cache/sheetsage-install.lock`; close setup, remove that lock only, then retry.
Invalid runtime replacements retain the previous directory as a backup under `.cache`.
The installer never changes project files or existing explicit backend overrides.

Locations relative to this installation:

- `engines/sheetsage/runtime/`: upstream audio.cpp binaries, all accompanying DLLs/specs/license.
- `models/sheetsage2/sheetsage2-orig.gguf`: exact original-dtype checkpoint.
- `.cache/sheetsage-downloads/`: verified installer downloads; not used for inference.

To remove this optional component, close the editor and remove only the two managed directories
`engines/sheetsage/runtime` and `models/sheetsage2`. Saved project evidence remains readable.
Running the BAT again reinstalls it. Neither directory belongs in Git.

## Pinned sources and identity

- [audio.cpp v0.8.1](https://github.com/0xShug0/audio.cpp/releases/tag/v0.8.1), commit f2b4937.
  `audio-v0.8.1-bin-windows-x64-cuda13.3.zip`: SHA256
  `aad5dffe4398b325018cf38e58e6555998ef97948a72ac7582de46e91155ca24`.
  `audio-v0.8.1-cudart-windows-x64-cuda13.3.zip`: SHA256
  `5c0a8b1022500b2df2062b7215584408ad07d43358f38a1a3ace0ac6daa441a9`.
  Hashes match the upstream GitHub release asset digests.
- Executable SHA256: `dc92dbd6ea4763cd298f03cc487c8fc5c0ba2c198abfa3b5e075af8700ec428c`.
- [audio-cpp/SheetSage2-GGUF](https://huggingface.co/audio-cpp/SheetSage2-GGUF/tree/6dd648b0acf1ba9e662d8509a3eabcb201911c2c), pinned revision
  `6dd648b0acf1ba9e662d8509a3eabcb201911c2c`.
  `sheetsage2-orig.gguf`: 2,708,224,512 bytes; SHA256
  `52bb5846c452037d39931aa8050885b6c751b9c7afcc8ef6d6d3067d241731a4`.

Archives are verified before extraction. All installed runtime files get a size/hash manifest;
full runtime hashes and the pinned model/executable hashes are checked in the worker before
inference. UI readiness uses presence/size checks to avoid reading gigabytes in the GUI.
A same-size corruption is therefore rejected on execution, not necessarily on initial display.

## Licensing and attribution

Audio.cpp: Copyright 2026 ShugoAI LLC, Apache-2.0. Source and binary redistribution are allowed
under its notice/license conditions; the exact license is in `licenses/audio.cpp-APACHE-2.0.txt`.
The upstream runtime contains GGML, Microsoft runtime and NVIDIA CUDA libraries with their own
terms. Audio.cpp's Apache license does not relicense those libraries. This repository publishes
only installer/source/notice files; it does not redistribute those binary archives. The installer
downloads the upstream archives unchanged. A future bundled binary release needs a separate
review of [NVIDIA CUDA terms](https://docs.nvidia.com/cuda/eula/index.html) and Microsoft runtime
redistribution conditions; this phase does not authorize a repackaged binary distribution.

SheetSage2 / MERT2 weights: **CC BY-NC 4.0 — non-commercial use only**, with attribution and
modification notices. This applies also when users download the weights themselves.
Model redistribution is permitted for non-commercial purposes under those conditions;
commercial rights are not granted. No weights are included in this repository.

Attribute the [SheetSage2 creators (m-a-p)](https://huggingface.co/m-a-p/SheetSage2/tree/eab522a8168e8b8b8c4856bf8609cd86198f01fe)
and [MERT2 / MERT-v2-FullSong creators](https://huggingface.co/m-a-p/MERT-v2-FullSong).
The [audio-cpp conversion](https://huggingface.co/audio-cpp/SheetSage2-GGUF) merges the LoRA/backbone
into original-dtype GGUF; it is a conversion, not a new model or an official m-a-p release.
Audio Chunker does not modify these weights. See the verbatim upstream license, third-party
notices and conversion model card in `licenses/`. No endorsement is implied.

## Discovery and advanced configuration

Priority: `COMFYMAX_SHEETSAGE_CONFIG` JSON path → local `.cache/sheetsage-backend.json` → managed
runtime/model → unavailable. An explicit but invalid configuration fails visibly; there is no
fallback to another installation. Relative executable/model paths resolve from the app root.
No external developer installation is searched. Normal setup needs no JSON file.

Defaults remain backend `cuda`, threads 4, max_tokens 5120, timeout_seconds 1200. Advanced
configurations may specify the existing audio.cpp backend values `cuda`, `cpu`, `best` (Auto).
Those values are runtime requests, not a promise of equivalent output or tested CPU speed.
The release keeps CUDA as default. Exact executable/model paths, hashes, runtime version and
requested backend are recorded in project provenance. Native ABC/events are preserved.

After setup, restart the editor, open Music Analysis and check `SheetSage2: Ready`.
Select the SheetSage2 checkbox when running Analyze Music. If an analysis already exists,
only the independent SheetSage evidence is added; its musical output is not fused with the grid.
