---
license: cc-by-nc-4.0
library_name: transformers
pipeline_tag: feature-extraction
base_model: m-a-p/SheetSage2
tags:
- gguf
- sheetsage2
- audio
- music
- music-transcription
- midi
- abc-notation
- custom_code
- arxiv:2502.11840
- arxiv:2510.02797
- arxiv:2212.01884
- arxiv:2503.08638
---

# SheetSage2-GGUF

GGUF conversion of [m-a-p/SheetSage2](https://huggingface.co/m-a-p/SheetSage2) for [audio.cpp](https://github.com/0xShug0/audio.cpp). SheetSage2 transcribes music into melody, chords, beats, key, structure, and editable scores. This is a converted checkpoint, not a new model or an official upstream release.

## Demo

<video controls playsinline preload="metadata" width="100%" src="https://huggingface.co/audio-cpp/SheetSage2-GGUF/resolve/main/sheetsage2_demo.mp4"></video>

[Watch or download the demo](https://huggingface.co/audio-cpp/SheetSage2-GGUF/resolve/main/sheetsage2_demo.mp4).

## Upstream

- SheetSage2: [pinned revision eab522a8168e8b8b8c4856bf8609cd86198f01fe](https://huggingface.co/m-a-p/SheetSage2/tree/eab522a8168e8b8b8c4856bf8609cd86198f01fe).
- Backbone: [m-a-p/MERT-v2-FullSong](https://huggingface.co/m-a-p/MERT-v2-FullSong).
- SheetSage2 source safetensors SHA256: `b235f68091a5f5b644000f2b5acb57d1e70432aca2b34ab1b9cf27236e1f4274`.
- Backbone source safetensors SHA256: `e6dd2ab187d6dd62b6521cd7d8f932e237acf0c5757745a7232082e28391350d`.

The packaged file uses the audio.cpp SheetSage2 runtime, not the upstream Transformers loader.

## Weights and Size

| File | Storage | Size |
|---|---|---|
| `sheetsage2-orig.gguf` | Unquantized FP32, merged model | 2,708,224,512 bytes (2.71 GB / 2.52 GiB) |

The upstream `model.safetensors` is only 228,738,564 bytes (228.74 MB): it contains SheetSage2 adapters and task-specific weights and relies on the separately downloaded MERT-v2-FullSong backbone. The GGUF includes that backbone with the LoRA updates already merged, plus the task weights and embedded configuration. No separate backbone checkpoint is needed at inference time.

The merged GGUF stores 677,020,953 FP32 values across 1,039 tensors. The larger size comes from packaging the complete merged model, not GGUF container overhead or accidental duplication. Both the upstream SheetSage2 safetensors and this GGUF use FP32; this is not an FP16-to-FP32 size increase.

## Conversion Validation

The tensor audit passed all 1,039 tensors: 96 matched the official FP32 LoRA merge byte-for-byte, and 943 matched the source tensors unchanged. There were no missing or extra tensors, dtype or shape errors, or byte mismatches.

GGUF SHA256: `52bb5846c452037d39931aa8050885b6c751b9c7afcc8ef6d6d3067d241731a4`.

Two complete songs (69.84 s and 161.53 s) matched Python token sequences and ABC output exactly with FP32 weights and CUDA TF32 enabled in both implementations. Event timestamps differed only by floating-point rounding (at most 2.85e-14 s). This is matched-math-policy validation, not a guarantee of identical output across backends or with Python TF32 disabled.

## Q8 Warning

**Q8 is not safe for preserving transcription parity. Use `sheetsage2-orig.gguf`.**

On the tested 161.53-second song, Q8_0 first changed a chord at 30.48 seconds. Compared with orig, it produced 538 instead of 542 events and 387 instead of 392 notes. At shared subbeats, 17 chord fields and 32 melody fields differed, and the ABC output changed. This is more than a one-off timestamp drift. It is evidence from one song, not a general accuracy benchmark; Q8 is not included in this package.

## License

The upstream weights are licensed under **Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**. This conversion retains that license; conversion does not grant commercial-use rights. Attribute the original SheetSage2 and MERT2 creators and source repositories, and identify the GGUF conversion and LoRA merge as modifications.

See the [upstream license](https://huggingface.co/m-a-p/SheetSage2/blob/eab522a8168e8b8b8c4856bf8609cd86198f01fe/LICENSE), [CC BY-NC 4.0 terms](https://creativecommons.org/licenses/by-nc/4.0/), and [upstream third-party notices](https://huggingface.co/m-a-p/SheetSage2/blob/eab522a8168e8b8b8c4856bf8609cd86198f01fe/THIRD_PARTY_NOTICES.md). Code and dependencies retain their separate licenses.
