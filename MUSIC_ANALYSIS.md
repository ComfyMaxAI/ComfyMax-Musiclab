# Music analysis — Phase 2B

Start `Launch Editor.cmd`, open an existing project, and click **Analyze Music**
in the header. Load Song still runs the existing Demucs preparation unchanged.
Music analysis uses the full MIX, regardless of the selected listening source.

The Music analysis tab shows Librosa/Grid/Span/Effective BPM, key, meter, first
downbeat, bar counts, raw labels, initial smoothing, final stabilized labels,
extension support, unknown recovery and the top five candidates.
Double-click a result row to listen to that interval. Prefix and tail intervals
are explicitly marked in the stored model. With the librosa backend the first downbeat is unknown and 4/4 is default
metadata. Configured Beat-Transformer can provide automatic evidence (see Phase 2A).

Processing runs in a QThread. Playback and navigation remain available. Project
switching, saving and marker edits are blocked until completion. Cancel is
cooperative at stage/beat boundaries; an active librosa call must finish first.
The initial Numba compilation may take longer than subsequent analyses.

Results are autosaved under `music_analysis` in project.json and preserved by
normal Save, recovery and Save As. Failed/cancelled analyses retain previous
results. Reanalysis is refused when future manual overrides exist, until an
explicit reconciliation workflow is implemented. No scene markers or snapshots
are generated or edited. Export Scenes remains unchanged.

**Export music_analysis.json** writes the same canonical music model separately.
Send this file plus the song title and several timestamped examples of correct
and incorrect chords/BPM/key for evaluation. Similarity scores and key profile
correlations are NOT calibrated probabilities.

## Backend

- Python 3.11; librosa 0.11.0, Numba 0.61.2, llvmlite 0.44.0.
- Existing NumPy 2.2.6, Torch/torchaudio, Qt, Demucs and Whisper remain pinned.
- Master sample frames remain authoritative; analysis uses 22050 Hz / hop 512.
- Highest-energy channel selection prevents antiphase cancellation, but can miss
  instruments present only in the other stereo channel.
- HPSS harmonic signal, CQT chroma, 60 major/minor/seventh templates, cosine similarity.
- Global major/minor key uses Krumhansl-Kessler profile correlation. It never
  restricts the chord vocabulary.
- Harmonic RMS controls N; low similarity/small winner margin yields unknown.
- A transition penalty stabilizes all 60 candidate scores via Viterbi decoding.
  Raw predictions are retained; consecutive identical labels merge into regions.
- Silence/unknown region scores are zero (not invented probabilities).
- Only 12-value beat chroma aggregates are persisted, not the full chromagram.
  No external feature cache is required, so Save As needs no new file handling.

Thresholds are centralized in `music.model.Settings` and recorded with every
analysis. Settings are currently a developer API, not a UI editor.

## Canonical model

Schema `comfymax.music.1` records analysis ID, source MIX checksum, backend and
algorithm versions, settings, timeline, tempo, key, meter, beats, first_downbeat,
raw_predictions and regions. All intervals are half-open [start_frame,end_frame).
Beat IDs/raw IDs/region IDs are unique within an analysis ID.

Regions reference their raw observations and contain nullable manual labels and
boundaries. `model.effective_chord(region)` resolves manual precedence without
overwriting automatic evidence. Display labels are derived from numeric pitch
classes and quality; no enharmonic spelling enters template scoring.

## Validation and limitations

Run `.venv\Scripts\python.exe -m unittest discover -s tests -v` and
`.venv\Scripts\python.exe -m pip check`.

Phase 1 has no graphical chord lane, manual editor, neural downbeat detector,
ABC export, melody analysis, automatic chords beyond triads/sevenths or retained extra stems.
Tempo may be half/double the perceived tempo. Rich chords, modulation and mixed
instrumentation can yield wrong triads or unknown. Full audio/features are held
in memory; intended for songs, not multi-hour recordings. Debug analysis is a
musical hypothesis and needs listening review.


## Phase 1.1 baseline tempo (retained, extended below)

The existing beat tracker is unchanged. `tempo.librosa_bpm` retains its estimate.
`grid_bpm = 60 / median(diff(beat_seconds))`, using detected master-clock beats.
`effective_bpm` prefers the grid when at least 4 intervals exist, BPM is in
[30, 300], MAD/median <= 0.08, and at least 75% of intervals lie within 15% of
the median. Otherwise it falls back to librosa. Invalid/non-increasing grids
are rejected. All thresholds are Settings fields. `stability` is that inlier
fraction, not a probability; interval count, median, MAD, source and fallback
reason are saved. Empty/silent input may have no tempo.

A median resists isolated missed beats but cannot remove frame quantization:
at 22050 Hz / hop 512 a nominal 120-BPM grid can have a median interval of
22 hops (~117.45 BPM) despite average spacing near 0.5 s. We do not round to a
musically convenient BPM or invent subframe beat positions. Half/double-time
ambiguity also remains.

## Phase 1.1 seventh chords and evidence

The same template/scoring/Viterbi pipeline now recognizes maj, min, 7, min7,
maj7 at all 12 roots. `chords.QUALITIES` defines offsets and parent triads.
Equal tone weights are retained from Phase 1; a root boost was considered but
not introduced because register/instrumentation/inversions alter root energy.
Display suffixes are '', m, 7, m7, maj7; identities remain pitch classes.

An extension is eligible only when BOTH conditions hold:
- Extension chroma / mean parent-triad chroma >= 0.35
  (`extension_min_relative_energy`).
- Seventh cosine similarity minus parent-triad similarity >= 0.02
  (`extension_min_score_gain`).

Rejected extensions are excluded from raw selection AND smoothing. The original
min_similarity=0.65 and min_margin=0.035 are unchanged. N remains silence/low
harmonic energy, while unknown remains insufficient harmonic identification.
Top candidates retain original cosine scores and compact extension evidence
(pitch class, relative energy, thresholds, score gain, acceptance); consequently
a high-scoring rejected candidate can appear in the debug list without winning.
No full 60-state matrix is persisted. Beat-averaged evidence measures energy,
not duration; a loud brief passing tone can still pass the gate.

Viterbi keeps the original 0.12 transition penalty except for same-root
parent-triad/seventh transitions, which cost half (0.06). This permits a strong
one-beat seventh to survive two transitions while rejecting weak extensions.
Unknown/silence states and untouched raw predictions are retained.

## Compatibility

`schema_version` remains `comfymax.music.1`: these are additive fields.
Phase 1.1 used `analysis.algorithm_version=1.1`; Phase 1.2 writes `1.2`. New Settings fields have defaults.
Old tempo objects with only bpm/beat_unit load unchanged, including save/reopen;
the UI uses legacy bpm for Librosa/Effective and shows Grid as unknown.
For new results `tempo.bpm` aliases effective_bpm for existing consumers.
No destructive migration is performed. Manual region fields and the derived
`effective_chord` precedence are unchanged. Scene markers are never updated by
music analysis. Existing data is upgraded only by an explicit analysis or structural action.

## Real-song evaluation

1. Launch `Launch Editor.cmd` and open a project; optionally Save As a test copy.
2. Click Analyze Music and wait for completion in the Music analysis tab.
3. Compare Librosa, Grid and Effective BPM against tapping/listening. Inspect
   exported tempo.source, fallback_reason, interval_mad and stability if needed.
4. Listen to raw/smoothed rows by double-clicking. Compare known triads and
   sevenths; inspect candidate extension_evidence in the exported JSON.
5. Check that scene markers are unchanged. Save, close and reopen the project.
6. Export music_analysis.json and record timestamped correct/incorrect examples,
   including unknown/N, chord changes, passing tones and tempo disagreements.

Next: evaluate several real songs before adjusting thresholds or starting the
separate graphical chord-track/ABC phases. Neither is implemented here.


## Phase 1.2 temporal stabilization

Phase 1.1 scoring and its initial Viterbi pass are retained, including all 60
candidates and the existing extension gates. Phase 1.2 first groups scores into
24 triad families (maximum eligible score within each family), then reuses the
same Viterbi machinery for family stability. No key restriction is introduced.
A separate temporal decision selects extensions within stable family runs.
Raw labels, candidates, scores and chroma are not overwritten. `initial_regions`
and per-beat `initial_smoothed` preserve the initial automatic result; `regions`
holds the final automatic result. Manual/effective precedence is unchanged.

Defaults and reasoning:

| Setting | Default | Purpose |
| --- | ---: | --- |
| extension_temporal_min_beats | 2 | Require consecutive support, not one isolated occurrence. |
| extension_temporal_min_fraction | 0.75 | A clear majority within a local context. |
| extension_temporal_window_beats | 4 | Four detected beats; this is not an assumed bar. |
| extension_single_beat_strong_energy | 0.9 | Extension energy comparable to core chord tones. |
| extension_single_beat_strong_gain | 0.10 | Substantial improvement over parent triad. |
| chord_change_min_beats | 2 | Inspect shorter family runs for hysteresis. |
| chord_change_min_score_advantage | 0.08 | Reject narrow short-lived family changes. |
| chord_change_single_beat_advantage | 0.20 | Restore decisive single-beat changes suppressed by Viterbi. |
| unknown_bridge_max_beats | 1 | Bridge only a short isolated gap by default. |
| unknown_bridge_min_similarity | 0.55 | Context-specific minimum; raw min_similarity is unchanged. |
| unknown_bridge_max_conflict | 0.10 | Reject bridging when a competing family is substantially stronger. |
| bar_min_consistency | 0.75 | Require a clear within-bar majority. |
| tempo_span_intervals | 8 | Average quantization across a longer observed span. |
| tempo_span_max_disagreement | 0.08 | Do not use a span estimate far from the stable median grid. |

A beat supports an extension only if the Phase 1.1 gates pass and its score is
within the unchanged min_margin of the best candidate. Sustained acceptance
requires a consecutive run of at least two supporting beats and the configured
fraction in a centered context of at least four beats (clipped at family edges).
The supporting run's median energy and score gain must also pass the original
extension gates. Shorter files/regions use only available detected beats.
Statistics for the whole family run and the local window are retained, including
support counts/fractions, maximum consecutive support, median energy/gain and
maximum gain. No full per-frame or candidate-score matrix is saved.

The one-beat exception is deliberately limited to dominant sevenths: strong
energy/gain, preceding same triad family and following fifth-down root in major
or minor. Thus G → G7 → C remains possible at every transposition. Other genuine
single-beat extensions may be suppressed; this is a conservative limitation.

Hysteresis compares a shorter-than-minimum family run with its strongest adjacent
family. Advantage below 0.08 replaces that run; strong single-beat raw evidence
with advantage >= 0.20 can restore a Viterbi-suppressed change. Temporal parameters
are tested separately from the untouched raw thresholds.

Unknown resolution has three documented paths:
- Retain an initial Viterbi resolution when family paths agree, score support is
  sufficient, conflict is limited, and either family evidence is decisive or
  at least two neighboring beats support it. The interval must remain short.
- Bridge an unresolved short unknown between matching stable families when its
  candidates support that family and no strong conflict exists.
- With explicit bar context, resolve an isolated unknown against a >=75% bar
  majority only with raw min_similarity support and a small score disadvantage.

New unknown bridges do not cross silence or arbitrarily replace long gaps.
`unknown_resolution` records original state, final label, reason and selected
similarity (not a calibrated probability). `stabilization_reason` explains changes.

## Phase 1.2 bars and manual downbeat

Bar context is disabled when first_downbeat.beat_id is null. No implicit detected
beat-1 downbeat is invented. In Music analysis, choose a beat index (0 = unknown)
and meter, then **Apply meter / first downbeat**, or select a table row and click
**Set selected beat as first downbeat**. These are manual metadata. The operation
rebuilds bars and structural smoothing from stored beat chroma; HPSS/CQT/audio
loading do not run. It autosaves normally and does not modify scene markers.

Bars retain numbered beat references, authoritative frame bounds, pickup and
incomplete flags, downbeat source and meter source. A prefix before the selected
first downbeat is a pickup. Phase 1.2 reserved source `auto`; Phase 2A now supplies it through the optional
rhythm backend described below.

Detected beats remain quarter-note units. 3/4 groups three detected beats; 6/8
also spans three quarter-note beats, while preserving 6/8 metadata. This is not
six detected eighth-note beats or compound-meter detection. Meters whose bar
length is fractional on this grid (e.g. 3/8) retain metadata but disable bar
context with an explicit reason. Partial final bars remain flagged.

Bar summaries include dominant triad family, strongest triad/seventh, mean and
median scores, consistency and possible internal change frames. Context may
suppress weak isolated deviations; strong C C G G remains two chords within one
bar. Pickup/context cannot establish a correct downbeat unless the user chooses
one accurately. Choosing the first detected beat can be wrong when onset is missed.

Manual chord overrides block structural reruns so they cannot be erased by
regrouping. Existing full reanalysis protection for manual meter/downbeat/key
also remains; editing structure again is allowed. No manual chord editor exists.

## Phase 1.2 span tempo

`span_bpm` is the median of `60 * 8 / (t[i+8] - t[i])` across valid overlapping
8-interval spans. It is separate from grid_bpm. Effective BPM prefers span only
when the Phase 1.1 stability/plausibility checks pass, span is plausible, and it
is within 8% of grid_bpm. Otherwise the existing grid/librosa fallback remains.
No nearest-common-tempo rounding is performed. Stable 120 BPM fixtures produce
about 120.185 BPM with their actual detected quantized positions.

## Phase 1.2 persistence and evaluation

Schema remains comfymax.music.1; fresh audio uses algorithm_version=1.2.
Structural reruns record structural_algorithm_version=1.2 and complete default
settings, retaining the original audio algorithm provenance. Phase 1/1.1 data
loads unchanged; structural actions explicitly add the optional metadata.
Legacy tempo remains intact during structural-only edits (Span may stay unknown).

`music.evaluation.evaluate(expected, predicted, rate)` accepts complete contiguous
frame annotations with root_pc/quality/bass_pc. Metrics are time-weighted exact
label overlap, root, triad-family and exact-quality accuracy, unknown percentage,
false seventh regions and exact-transition boundary matching. Boundary matches
are one-to-one within 0.5 seconds by default; false and missed transitions are
reported separately. Boundary timing error averages only matched transitions;
null means none matched. Baseline/after scores should use identical annotations.

Known limits: multi-beat melodic tones can still look like real extensions;
missing beats and bad beat alignment remain upstream constraints; no beatless
fallback segmentation was added. Temporal context can suppress legitimate brief
harmonic changes. The three local fixtures are evaluation data, not a guarantee
of performance on commercial recordings. Test on several songs before expanding
to graphical chord tracks, manual chord editing or ABC export.

## Phase 2A — optional Beat-Transformer rhythm

Phase 2A adds a rhythm boundary before the existing HPSS/CQT/key/chord/Viterbi/
temporal stages. Those algorithms remain unchanged. `rhythm.analyze()` retains
the original librosa API; `analyze_rhythm()` adds normalized backend evidence.
`master_beats()` converts both detector beat and downbeat seconds to the project
sample clock. Prefix, tail and unmetered intervals are preserved. No beat is
inserted at zero to cover silence.

### Selection and fallback

By default, an unconfigured installation uses librosa. To explicitly enable the
advanced backend, create `.cache/rhythm-backend.json`, or point the environment
variable `COMFYMAX_RHYTHM_CONFIG` to a JSON configuration. `pipeline.analyze()`
also accepts `rhythm_config` for developer/tests. No new UI settings are added.
Example for a separate native environment (replace absolute paths):

```json
{
  "backend": "beat-transformer",
  "command": ["C:/AudioRhythm/python.exe", "D:/ComfyMax-Audio-Chunker/src/comfymax_audio_chunker/music/beat_worker.py"],
  "path_style": "native",
  "model_root": "D:/AudioModels/Beat-Transformer",
  "checkpoint": "D:/AudioModels/Beat-Transformer/checkpoint/fold_4_trf_param.pt",
  "timeout_seconds": 1200
}
```

The command is an argv array, executed without a shell. For WSL use e.g.
`["wsl.exe", "-d", "Ubuntu-24.04", "--exec", "/path/to/venv/bin/python",
"/mnt/d/ComfyMax-Audio-Chunker/src/comfymax_audio_chunker/music/beat_worker.py"]`,
`path_style: "wsl"`, and Linux model/checkpoint paths. Exchange audio is a
floating-point WAV in the project `.cache/rhythm-worker` directory; WSL mapping
supports local Windows drive paths, not UNC shares. The selected original-rate
channel is transferred, avoiding a 22.05→44.1 kHz roundtrip for this backend.
Temporary exchange files are removed after completion/failure.

Use `{"backend":"librosa"}` to explicitly disable the worker. A missing default
configuration simply means librosa. An explicitly named missing/malformed config,
missing executable/model, timeout, failed inference, invalid JSON/protocol,
nonfinite/unordered/out-of-range timestamps or insufficient usable beats falls
back to the existing librosa path. Reasons are logged and included in the
analysis warnings/provenance. Internal Python programming errors in orchestration
are not broadly caught. Worker tracebacks are retained in failure diagnostics.

### Isolation, model files and CPU

The worker is a standalone JSON process. Nothing imports its legacy dependencies
into the editor. No Flask, web routes, chord services or whole reference
application is copied. No packages were added to the Audio Chunker's environment;
its Torch/CUDA stack was not changed. Phase 2A forces CPU and never selects CUDA,
including on sm_120 hardware.

The validated external environment is Linux Python 3.10.16, Torch 2.6.0+cu124
(executed on CPU), NumPy 1.26.4, librosa 0.10.1, SciPy 1.13.1, SoundFile 0.12.1,
Numba 0.59.1 and madmom 0.17.dev0 (commit
`27f032e8947204902c675e5e341a3faf5dc86dae`). Existing installed packages were reused.
For a fresh environment, install compatible versions separately, including the
madmom build requirements; do not install them into the editor environment.
Legacy NumPy/collections compatibility shims are confined to the worker process.
Native Windows installation of this legacy stack was not validated.

The configured model root needs only upstream `code/DilatedTransformer.py`,
`code/DilatedTransformerLayer.py`, the checkpoint and its license. Use trusted
model code and checkpoints. The worker loads the state dict with
`weights_only=True`, records hashes and package versions, and does not download
anything. This machine has copies in `.cache/beat-transformer`; its configuration
reuses the existing external research Python interpreter but does not import the
research application's wrappers or services.

Architecture reference: [upstream Beat-Transformer](https://github.com/zhaojw1998/Beat-Transformer)
(MIT). The original model uses instrument-channel spectrograms. Our explicit
`hpss-five-view-v1` preprocessing uses original, pre-emphasized, percussive,
harmonic and low-pass audio, with 44.1 kHz / FFT 4096 / hop 1024 / 128 mel bins.
These are **not separated instrument stems**; this retains the lightweight local
research approach and may underperform true demixed input. No Demucs changes or
Spleeter dependency were introduced. This limitation is persisted in metadata.

Inference uses up to 3000 core frames plus 1024 context frames on each side, then
a single global DBN decode. The CPU runtime is substantially slower than librosa.
Long-track neural chunk accuracy has not yet been benchmarked. A shared cancellation sentinel and a worker-side deadline terminate inference
even when a WSL launcher does not forward process termination. The parent allows
three seconds for graceful process exit before killing its launcher.

### JSON protocol and evidence

One stdin JSON request contains `protocol: "comfymax.rhythm.1"`, `audio_path`,
`model_root`, `checkpoint`, `device: "cpu"`, a `cancel_path` sentinel and
`timeout_seconds` deadline. Stdout contains exactly one JSON
response; diagnostics go to stderr. Success includes `success: true`, ordered
`beats`/`downbeats` in seconds, nullable `bpm`, `beat_unit: "1/4"`, nullable
`meter: {numerator, denominator}`, `meter_reliable`, `downbeats_reliable`, and
`metadata`. Failure includes `success: false` and `error`, and exits nonzero.
Model-specific tensors/logits do not leave the worker.

The DBN considers 3/4 and 4/4. It does not fabricate downbeats or presume 4/4 when
there is no evidence. Reliable downbeats require at least three observations and
median downbeat activation >=0.2 (a heuristic gate, not calibrated confidence).
At the application boundary they must map to existing beats within min(70 ms,
20% of median beat spacing). Automatic meter additionally requires at least two
complete, consistent bar spans. Unsupported/invalid meter retains default 4/4;
unusable downbeats retain a null/default first downbeat. Beat timestamps more
than 100 ms before detected leading activity are rejected, allowing centered
STFT onset uncertainty without extrapolating beats deep into silence. Activity
uses 10 ms RMS blocks and existing silence thresholds, not filename-specific
knowledge. Full silence falls back to librosa.

Schema stays `comfymax.music.1`; meter source additively accepts `auto` alongside
`manual`/`default`. First downbeat uses the existing `auto` value. New audio runs
record algorithm `2A`, harmonic algorithm `1.2`, and structural algorithm `1.2`.
`analysis.backend` remains librosa for the harmonic feature stack;
`analysis.rhythm_backend` separately records requested/selected rhythm backend,
fallback reason, model identity, master-clock downbeats, beat associations,
detected/reported meter and prefix rejection evidence. Manual protection and
project persistence are unchanged. Old Phase 1/1.2 documents require no migration.

Existing grid/span tempo stabilization remains authoritative. On the neural
path, `tempo.detector_bpm` stores the worker estimate and `librosa_bpm` is null;
`tempo.source="detector"` is the fallback when the grid is insufficient/unstable.
Grid/span can still provide effective BPM. Do not mistake the raw detector BPM
for the effective tempo. The existing UI consequently shows Librosa BPM as
unknown for neural results; full provenance is available in exported JSON.

### Reference and tests

From the project directory, using the locally configured backend:

```powershell
.\.venv\Scripts\python.exe -m comfymax_audio_chunker.music.reference `
  'C:\Users\danie\Downloads\C-Am-F-G_1sec-Render.wav' `
  --compare-librosa --output '.cache\phase2a-reference'
```

Use `--config path.json` to select an explicit config. The command writes two
complete analysis documents and a summary without creating or modifying a
project. It does not substitute the research observations for model inference.
The known 1.000 s leading silence is an evaluation property, not a model input.

Focused offline tests: `.venv\Scripts\python.exe -m unittest discover -s tests
-p test_music_phase2a.py -v`. Tests use model fixtures and tiny fake processes;
no model download is needed. To run the existing full regression suite on a
machine with neural inference enabled, set `COMFYMAX_RHYTHM_CONFIG` temporarily
to a file containing `{"backend":"librosa"}`, then run unittest discovery. This
keeps the existing librosa-specific baseline assertions deterministic. Phase 2A
fixture tests explicitly select the advanced backend themselves.

## Phase 2B — optional Chord-CNN-LSTM

Phase 2A rhythm code, configuration, beat conversion and workers remain unchanged.
The optional chord backend runs after the complete existing template/Phase 1.2
pipeline. `analysis.chord_backend` records requested/selected backend, model
provenance, fallback reason, the final policy and separate timing measurements.
Unconfigured installations use `template`; this machine explicitly enables the
CPU worker in `.cache/chord-backend.json`. Override this with
`COMFYMAX_CHORD_CONFIG`, or use `pipeline.analyze(chord_config=...)`.

Example isolated WSL configuration (replace the interpreter path):

```json
{
  "backend": "chord-cnn-lstm",
  "command": ["wsl.exe", "-d", "Ubuntu-24.04", "--exec", "/path/to/python3.10", "/mnt/d/ComfyMax-Audio-Chunker/src/comfymax_audio_chunker/music/chord_worker.py"],
  "path_style": "wsl",
  "model_root": "/mnt/d/ComfyMax-Audio-Chunker/.cache/chord-cnn-lstm",
  "timeout_seconds": 1200,
  "alignment": {
    "tolerance_seconds": 0.08,
    "downbeat_preference_seconds": 0.015,
    "max_beat_fraction": 0.2,
    "min_grid_stability": 0.75
  }
}
```

Use `{"backend":"template"}` to disable the advanced chord backend. Native
isolated environments can use `path_style: "native"` and native executable/model
paths. The subprocess argv is passed without a shell. The editor exports its
existing selected channel at the master sample rate as temporary float WAV;
only the worker resamples to its 22050 Hz feature rate. Project timing never
uses the model's feature-frame indices directly.

### Worker and environment

The small standalone worker uses the already available WSL Python 3.10.16,
Torch 2.6.0+cu124 (CPU only), librosa 0.10.1 and NumPy 1.26.4 environment. No
packages were installed/upgraded in either environment for this phase. GPU
auto-selection in the research model is overridden inside the worker. The
editor's Python 3.11, Torch/CUDA and playback system remain unchanged.

Only the model's inference architecture, decoder, chord vocabulary, required
`mir` support modules, five state dictionaries and MIT license are kept in the
local `.cache/chord-cnn-lstm` directory. No Flask/UI/routes, BTC, SongFormer or
whole application is imported/copied. The external Python interpreter remains
in its existing research location but the model assets are independent copies.

The model is derived from [Large-Vocabulary Chord Recognition](https://github.com/music-x-lab/ISMIR2019-Large-Vocabulary-Chord-Recognition).
The worker follows the locally tested inference path: 288-bin hybrid CQT,
36 bins/octave, F#0 minimum, hop 512, five checkpoint predictions averaged within
this model, then its submission-vocabulary HMM. This is the model's native
five-fold inference, not consensus between different chord detectors. The local
decoder uses transition penalty 10, initial state N and an N-emission multiplier
of 0.5; these facts and artifact hashes are recorded, not silently changed.

Protocol `comfymax.chords.1` accepts `audio_path`, `model_root`, `device: "cpu"`,
`launch_unix`, `cancel_path`, and `timeout_seconds` in one stdin JSON request.
One stdout JSON response includes `success`, `backend`, `segments`, `metadata`
and `timings`. Segment fields are `start_seconds`, `end_seconds`, `label` and
nullable `evidence`. Logs/tracebacks go to stderr. The worker reuses only the
dependency-free Phase 2A watchdog for cancellation/deadlines. Missing executables,
model files, process failures, malformed/overlapping/nonfinite output and timeout
fall back to the already computed template result with warnings and provenance.
Internal orchestration programming errors are not broadly swallowed.

### Three separate result layers

1. **Raw neural detection**: `chord_detections.raw_segments` is the unmodified
   worker output, including original label, original seconds and N. A SHA-256
   integrity digest detects accidental mutation. This is decoded CNN/HMM output,
   not unsmoothed frame logits. Its segments have no calibrated chord probability;
   `evidence` and final `score` are null rather than invented confidence values.
2. **Grid-aligned detection**: `normalized_segments` explicitly maps vocabulary
   and converts seconds through `seconds_to_frame`; `aligned_segments` adds the
   bounded grid adjustments. Each edge records original/aligned frame, snap flag,
   reason, beat ID and beat/downbeat kind. Original timestamps remain intact.
3. **Final Phase 2B result**: canonical `regions` uses these decoded identities
   and aligned bounds. No template-uncertainty veto, neural short-change removal,
   template seventh replacement or one-chord-per-bar rule is applied. Unobserved
   gaps are explicitly unknown; the first musical chord is never extended to 0.

Original `raw_predictions` (chroma/templates/candidates), `initial_regions` and
all original Phase 1.2 diagnostics remain. `template_regions` stores the full
stabilized template result. Existing `temporal.py` computes template context as
before; its small neural branch reruns that context and alignment from immutable
neural evidence after a manual meter/downbeat edit. It does not rerun the neural
model or replace its identities. Bar harmonic summaries remain explicitly
**template context**, not neural confidence or neural consensus.

For neural regions, `raw_ids` means all overlapping template intervals, rather
than forcing exact whole-interval boundaries. A `detection_id` links every
observed final region to its original and aligned segment. The validator checks
complete final coverage, immutable evidence, master clocks, deterministic
alignment, region projection and the separate original template model. The
existing table shows multiple final labels when a neural change splits one
raw beat; audition still uses the selected existing beat interval. Detailed
sub-beat boundaries are inspectable in JSON and reference comparison exports.

Schema remains `comfymax.music.1` with additive evidence and an explicitly tagged
neural-region variant; old Phase 1/1.2/2A files remain readable. New runs record
algorithm 2B; template harmonic/structural algorithm provenance remains 1.2.
Manual key/meter/downbeat/chord protections, Save/Save As/recovery and scene
boundaries are preserved. New neural files require the updated application;
older application versions are not expected to understand the neural variant.

### Vocabulary

The submission dictionary's explicit qualities are maj, min, 7, maj7, min7,
dim, aug, dim7, hdim7, sus2, sus4, sus4(b7), 9, maj9, min9, 11 and 13. Roots
and slash basses become pitch classes. Relative bass degrees such as /b3, /5,
/b7 and /2 are resolved relative to the root; explicit note basses are supported.
N/N/C map to no-chord. X/unknown map to unknown. Unsupported labels, qualities or
bass syntax remain unknown with their original string and mapping reason;
there is no silent major/minor simplification or new template vocabulary.

### Alignment rules

Default maximum movement is **80 ms**, further capped at 20% of median beat
spacing. The grid needs at least five beats, four intervals, stability >=0.75
and no tempo-grid fallback reason. With insufficient evidence, boundaries stay
unchanged. A nearby observed automatic downbeat or manually designated downbeat
is preferred only if at most 15 ms farther than the nearest candidate and still
within the same absolute tolerance. Ordinary beats, including beats 2/3/4, are
eligible; mid-bar changes outside tolerance remain at their neural timestamp.

Project endpoints remain fixed. Shared segment boundaries are moved once for
both neighbors. Any moves that collapse/reorder a segment or gap are cancelled,
never repaired by deleting a short chord. N is preserved. The absolute tolerance
can be 0–250 ms; downbeat preference must fit inside it, and the fraction cap is
0–0.5 (strictly positive). These values are stored in the analysis, so structural
reruns do not pick up a changed external configuration silently.

Alignment is a heuristic, not guaranteed ground truth. A biased beat estimate
can move a good neural boundary farther from the true musical change; the
reference report includes such cases. Timing quantization is around 23.22 ms at
the model hop. Missing leading/trailing model coverage is marked unknown, and a
last-frame overhang up to 50 ms is clipped to the project end while its original
seconds remain in evidence.

### Reference procedure and performance

Run on original WAVs, not old JSON:

```powershell
.\.venv\Scripts\python.exe -m comfymax_audio_chunker.music.chord_reference `
  'C:\Users\danie\Downloads\C-Am-F-G_1sec-Render.wav' --output '.cache\phase2b-A'
.\.venv\Scripts\python.exe -m comfymax_audio_chunker.music.chord_reference `
  'C:\Users\danie\Downloads\G-C-D-G-85bpm-1sec_Render.wav' --output '.cache\phase2b-B'
```

Each output directory contains complete `music_analysis.json` plus `comparison.md`
with timestamped template/raw CNN/aligned CNN/final columns and per-boundary
alignment reasons. Optional `--chord-config` and `--rhythm-config` select explicit
configs. The reference procedure never modifies an editor project.

Timings separately record worker startup (launch/interpreter/request latency,
using the host/WSL wall clock), dependency imports, model loading, CQT preprocessing,
neural inference, model HMM decoding, worker roundtrip, alignment/postprocessing
and total analysis. Total includes rhythm and template analysis; roundtrip
includes worker phases and IPC. Do not sum total and its nested components.
The current CNN worker processes the whole track in memory; long recordings and
GPU execution have not been optimized or benchmarked here.

Focused offline tests: `.venv\Scripts\python.exe -m unittest discover -s tests
-p test_music_phase2b.py -v`. For all existing regressions, temporarily point
`COMFYMAX_RHYTHM_CONFIG` at `{"backend":"librosa"}` and `COMFYMAX_CHORD_CONFIG`
at `{"backend":"template"}` files so legacy detector-specific assertions stay
deterministic. New backend tests select/mimic neural results explicitly and
never download weights. No BTC-SL, chord voting or fusion is included.
