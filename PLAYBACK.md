# Local playback reliability

Playback uses sounddevice / PortAudio, not QAudioSink, QMediaPlayer or PyAudio.
On the tested Windows machine the default output is Studio 24c via MME at 44100 Hz.
The selected system device is retained; this change does not force another endpoint.

## Data and clocks

`load_audio()` reads each project PCM asset once using soundfile into contiguous
float32 NumPy arrays. It verifies the stored sample rate/frame count. MIX and
VOCALS must share sample shape; mono and stereo are supported. Playback has no
file reads, FFmpeg process, decoding or application resampling. OS/device format
conversion can still occur. Source PCM and scene/marker boundaries are unchanged.

PortAudio invokes the Python callback on its audio thread. The callback copies
bounded RAM samples, applies volume/clipping and fills the native device queue.
Qt timers do not supply audio. There is no GUI-owned callback lock. The callback
still needs Python's GIL; this is buffered callback playback, not a claim of hard
real-time or completely Python-free delivery.

The playhead reads PortAudio stream.time against outputBufferDacTime/frame anchors.
Queued/rendered-ahead samples do not move the playhead early. Visual updates run
at 25 Hz. The sample path is cached until view size, zoom, pan or source changes;
markers, drag previews, selection and playhead remain live. This removes repeated
per-pixel NumPy reductions from normal playhead repainting in both editors.

## Buffer choice

The previous stream requested latency='low' and fixed 512-sample callbacks.
On Studio 24c that meant 11.61 ms callbacks and 92.88 ms actual output latency;
the driver advertises 90 ms low / 180 ms high defaults.

The new stream requests 200 ms. Callback length is the next power of two above
40 ms of samples: 2048 samples at 44100/48000 Hz (46.44/42.67 ms). On the tested
44100 Hz MME device the actual latency is 232.20 ms after backend rounding.
This deliberately favors editor playback reliability over instrument-monitoring
latency. Actual configuration is logged because it varies by device/backend.

## Controls and lifecycle

Pause freezes the PCM producer, lets the already-queued audio finish, and parks
at that exact sample tail. On the tested device this can take up to about 232 ms.
It avoids a measured 20–27 ms repeated fragment caused by aborting and rewinding
to the approximate MME audible-clock estimate. Stop remains an immediate abort
at the estimated audible position. Both retain the open device; Resume restarts
the same stream. Seeking aborts old queued sound,
sets the exact requested sample position/range, clears stale timing anchors and
restarts the same device if playback was active. A paused seek remains paused.
A seek outside an audition restores full-song bounds, as before. Loop boundaries
remain sample-bounded, including wraps within a callback. Toggling looping flushes
old queued loop state but does not reopen the device.

MIX/VOCALS switches on the existing stream at the same source sample position.
A 5 ms crossfade uses aligned samples and reduces switch clicks. Starts/seeks
have a 5 ms fade-in without moving boundaries or shifting the sample clock.
Stop is an immediate abort; arbitrary abrupt stops can still create a small
transient, and non-periodic loop endpoints can still click.

The device is closed on document replacement, editor close or stream-start error.
No project schema/persistence changes are made.

## Diagnostics

Normal app startup enables a bounded rotating `.cache/playback.log` under the
application directory (256 KiB plus two backups). It records device configuration,
actual latency, callback count, output-underflow count, status flags and maximum
callback elapsed time. If file logging is unavailable, warnings remain on stderr.
`Transport.diagnostics()` exposes the same counters. Status is also shown in the
editor. Callback counters are cumulative across pause/seek; file logging happens
outside the callback.

A zero PortAudio underflow count is NOT proof of clean audio: the tested old MME
path inserted silent gaps without reporting underflows. Evaluation therefore also
captured the actual Windows render output through WASAPI loopback, never a
microphone, and compared its signal/time alignment with the decoded source PCM.
Intentional pauses/seeks are evaluated separately from continuous playback.

## Validation and remaining limits

See the task's playback report for before/after measurements and fixture results.
Run `.venv\Scripts\python.exe -m unittest discover -s tests -v` for regression
coverage, including PCM continuity, clock/range behavior, stream reuse, source
crossfade, loop/seek behavior, diagnostics and waveform-cache invalidation.

A device reset, sufficiently long GIL stall, extreme system load or driver issue
can still exceed the buffer. The app follows the backend's estimated DAC clock;
physical speaker/DAC delay is not calibrated. Switching device defaults while a
stream is open does not migrate that stream: reopen the project/editor. Music
Analysis algorithms, Demucs processing and marker/chunk semantics are unchanged.
