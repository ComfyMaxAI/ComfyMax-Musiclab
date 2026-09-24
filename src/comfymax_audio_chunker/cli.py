import argparse
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("comfymax")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def command(args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-4000:]}")
    return result.stdout


def setup_cache(offline):
    os.environ["TORCH_HOME"] = str(ROOT/".cache"/"torch")
    os.environ["HF_HOME"] = str(ROOT/".cache"/"huggingface")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"


def separate(mix, sr, out, device, offline):
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] > 4*1024**3 else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable. Use --device cpu or rerun setup.ps1.")
    if offline:
        # htdemucs 4.0.1 is a one-model bag with this checkpoint.
        checkpoint = Path(os.environ["TORCH_HOME"])/"hub"/"checkpoints"/"955717e8-8726e21a.th"
        if not checkpoint.exists():
            raise RuntimeError("Demucs weights not cached. Run once without --offline to download them.")
    LOG.info("Separating vocals with htdemucs on %s", device)
    torch.manual_seed(0)
    model = get_model("htdemucs")
    model.eval()
    if sr != model.samplerate or mix.shape[1] != model.audio_channels:
        raise RuntimeError("Unexpected Demucs audio format")
    wav = torch.from_numpy(mix.T.copy())
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std()
    # Fall back to all-channel variance for anti-phase stereo.
    if std < 1e-8:
        std = wav.std()
    if std < 1e-8:
        vocals = np.zeros_like(mix)
    else:
        with torch.inference_mode():
            estimates = apply_model(model, ((wav-mean)/std)[None], device=device,
                                    shifts=1, split=True, overlap=.25, progress=True,
                                    num_workers=0)[0]
        vocals = (estimates[model.sources.index("vocals")]*std+mean).cpu().numpy().T
        del estimates
    sf.write(out/"vocals.wav", vocals, sr, subtype="FLOAT")
    del model, wav, ref
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return vocals, device


def transcribe(path, args, duration, active, *, word_timestamps=True, raw_output=None):
    from faster_whisper import WhisperModel
    from .regions import overlap_fraction
    LOG.info("Transcribing vocals with %s on CPU (int8)", args.model)
    model = WhisperModel(args.model, device="cpu", compute_type="int8",
                         download_root=str(ROOT/".cache"/"whisper"), local_files_only=args.offline)
    segments, info = model.transcribe(str(path), language=args.language, task='transcribe',
                                     multilingual=False, initial_prompt=getattr(args,'initial_prompt',None), beam_size=5,
                                     word_timestamps=word_timestamps, vad_filter=False,
                                     condition_on_previous_text=False)
    results, words = [], []
    for seg in segments:
        if raw_output is not None:
            from dataclasses import asdict
            raw_output.append(asdict(seg))
        start, end = max(0., min(duration, seg.start)), max(0., min(duration, seg.end))
        # Speech-oriented no_speech_prob can be high even for clearly sung words.
        # Keep it as a diagnostic, but require poor decoding or weak acoustic
        # support to reject a segment, rather than rejecting singing by itself.
        suspect = seg.avg_logprob < -1.0 or seg.compression_ratio > 2.4
        suspect = suspect or overlap_fraction(start, end, active) < .25
        results.append(dict(id=seg.id, start=start, end=end, text=seg.text.strip(),
                            avg_logprob=seg.avg_logprob, no_speech_prob=seg.no_speech_prob,
                            compression_ratio=seg.compression_ratio, suspect=bool(suspect)))
        if getattr(seg,'temperature',None) is not None:
            results[-1]['temperature']=seg.temperature
        for w in seg.words or []:
            a, b = max(0., min(duration, w.start)), max(0., min(duration, w.end))
            if b <= a:
                continue
            words.append(dict(id=len(words), segment_id=seg.id, start=a, end=b, word=w.word,
                              probability=w.probability, suspect=bool(suspect or w.probability < .25 or
                              overlap_fraction(a,b,active) < .2)))
        LOG.info("Transcript %.1f / %.1f seconds: %s", end, duration, seg.text.strip())
    del model
    return results, words, dict(language=info.language, probability=info.language_probability)


def stamp(t):
    return f"{int(t//60):02d}:{t%60:06.3f}"


def report(data):
    lines = ["ComfyMax Audio Chunker — Stage 1 analysis", "", f"Source: {data['source']['path']}",
             f"Duration: {stamp(data['timeline']['duration'])}",
             f"Original SHA-256 verified unchanged: {data['source']['unchanged']}",
             "Timeline: decoded audio starts at 00:00.000; timestamps are estimates, not approved cuts.", "",
             "REVIEW NOTES", *[f"- {w}" for w in data['warnings']], "", "REGIONS"]
    for r in data['regions']:
        lines.append(f"{stamp(r['start'])} – {stamp(r['end'])}  {r['kind'].upper()} (estimated)")
    lines += ["", "PHRASE CANDIDATES (not cut points)"]
    for p in data['phrases']:
        lines.append(f"{stamp(p['start'])} – {stamp(p['end'])}  {p['text']}")
    lines += ["", "ALL TRANSCRIPT SEGMENTS (? = suspect)"]
    for s in data['segments']:
        lines.append(f"{'?' if s['suspect'] else ' '} {stamp(s['start'])} – {stamp(s['end'])}  {s['text']}")
    lines += ["", "WORDS (? = suspect)"]
    for w in data['words']:
        lines.append(f"{'?' if w['suspect'] else ' '} {stamp(w['start'])} – {stamp(w['end'])}  {w['word'].strip()}")
    return "\n".join(lines)+"\n"


def run(args):
    import numpy as np
    import soundfile as sf
    from .regions import activity, merge_intervals, partition, phrases
    source = args.song.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Song must be a file")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not on PATH. Install FFmpeg and reopen PowerShell.")
    out = args.output.expanduser().resolve() if args.output else ROOT/"runs"/(datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    # Never reuse a directory or overwrite any source/artifact.
    out.mkdir(parents=True, exist_ok=False)
    handler = logging.FileHandler(out/"analysis.log", encoding="utf-8")
    LOG.addHandler(handler)
    started = time.perf_counter()
    before = sha256(source)
    LOG.info("Output: %s", out)
    try:
        metadata = json.loads(command(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                      "-show_streams", "-show_format", "-of", "json", source]))
        if not metadata.get("streams"):
            raise ValueError("No audio stream found")
        LOG.info("Decoding an analysis copy; original stays read-only")
        decoded = out/"analysis_mix.wav"
        command(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", source, "-map", "0:a:0",
                 "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", decoded])
        mix, sr = sf.read(decoded, dtype="float32", always_2d=True)
        if not len(mix) or not np.all(np.isfinite(mix)):
            raise ValueError("Audio is empty or contains non-finite samples")
        duration = len(mix)/sr
        vocals, device = separate(mix, sr, out, args.device, args.offline)
        separation_only=getattr(args,'separate_only',False)
        active, detector = ([],{'enabled':False}) if separation_only else activity(vocals, mix, sr, args.floor_db, args.relative_db, args.ratio_db)
        del vocals, mix
        # No speech VAD trimming: singing and non-lexical vocals must retain their time positions.
        segments, words, language = ([],[],None) if separation_only else transcribe(out/"vocals.wav", args, duration, active)
        merged = merge_intervals(active + [(w['start'], w['end']) for w in words if not w['suspect']], duration)
        warnings = ["Automatic estimates require listening review. No cut points or scene files were created.",
                    "Whisper may mishear lyrics, hallucinate words, or place singing timestamps inaccurately.",
                    "Stem energy may detect instrumental bleed; quiet, breathy or wordless vocals may be missed.",
                    "Instrumental means no detected vocals, including silence; it is not a guaranteed absence of singing.",
                    "Phrase groups use word pauses and predicted punctuation, not verified musical or sentence boundaries."]
        if not words:
            warnings.append("No words recognized. Check vocals.wav; energy-based regions are still reported.")
        if any(w['suspect'] for w in words):
            warnings.append("Suspect words are retained for review but excluded from phrase candidates.")
        if separation_only:
            warnings=['Demucs separation only. Transcription and region detection were skipped. Place markers manually in the editor.']
        after = sha256(source)
        if after != before:
            raise RuntimeError("Source changed during analysis; results are invalid. The app never writes to the source.")
        data = dict(schema_version="1.0", application="ComfyMax Audio Chunker", stage=1,
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    source=dict(path=str(source), sha256_before=before, sha256_after=after, unchanged=True,
                                audio_stream=metadata['streams'][0]),
                    timeline=dict(origin="first decoded audio sample", duration=duration, analysis_sample_rate=sr,
                                  analysis_frames=round(duration*sr), timestamp_unit="seconds",
                                  note="Analysis resampling does not alter the original. Future export must map times to original decoded samples."),
                    processing=dict(demucs_model="htdemucs", demucs_device=device, whisper_model=None if separation_only else args.model,
                                    whisper_device="cpu", compute_type="int8", vad_filter=False, language=language,
                                    detector=detector, phrase_pause_seconds=.65, seed=0,
                                    versions={n:importlib.metadata.version(n) for n in ('demucs','faster-whisper','torch','torchaudio','numpy','soundfile')},
                                    elapsed_seconds=time.perf_counter()-started),
                    regions=[] if separation_only else partition(merged,duration), energy_vocal_intervals=active,
                    phrases=phrases(words), segments=segments, words=words, warnings=warnings,
                    artifacts=dict(vocals="vocals.wav", analysis_mix="analysis_mix.wav", log="analysis.log"))
        (out/"analysis.json").write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        (out/"analysis.txt").write_text(report(data), encoding="utf-8")
        LOG.info("Complete: %s", out/"analysis.txt")
        return 0
    except BaseException as exc:
        LOG.exception("Analysis did not complete")
        (out/"failure.json").write_text(json.dumps(dict(error=str(exc), source=str(source),
                   source_unchanged=source.exists() and sha256(source)==before), indent=2), encoding="utf-8")
        raise
    finally:
        LOG.removeHandler(handler)
        handler.close()


def main():
    p = argparse.ArgumentParser(description="ComfyMax Audio Chunker — Stage 1 local song analysis")
    p.add_argument("song", type=Path, help="Full mixed song; never modified")
    p.add_argument("--output", type=Path, help="New output directory (must not already exist)")
    p.add_argument("--device", choices=("auto","cpu","cuda"), default="auto", help="Demucs device; auto uses CPU if GPU has under 4 GiB free")
    p.add_argument("--model", default="small", help="faster-whisper model name or local model folder; default: small")
    p.add_argument("--language", help="Language code, e.g. en; otherwise auto-detect")
    p.add_argument("--offline", action="store_true", help="Require previously cached models")
    p.add_argument("--separate-only", action="store_true", help="Prepare full mix and Demucs vocals without transcription or region detection")
    p.add_argument("--floor-db", type=float, default=-48.)
    p.add_argument("--relative-db", type=float, default=-30.)
    p.add_argument("--ratio-db", type=float, default=-22.)
    args = p.parse_args()
    if any(not (-120 <= v <= 0) for v in (args.floor_db,args.relative_db,args.ratio_db)):
        p.error("Detector thresholds must be between -120 and 0 dB")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    setup_cache(args.offline)
    try:
        return run(args)
    except KeyboardInterrupt:
        LOG.error("Cancelled; partial artifacts retained.")
        return 130
    except Exception as exc:
        LOG.error("%s", exc)
        return 1
