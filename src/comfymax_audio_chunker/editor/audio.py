"""Sample-bounded playback; the UI follows the device clock, not a timer."""
from collections import deque
from dataclasses import dataclass
import logging
import math
import time
import numpy as np
import sounddevice as sd
import soundfile as sf

LOG = logging.getLogger(__name__)
PLAYBACK_LATENCY = 0.20
CALLBACK_SECONDS = 0.04


def configure_playback_logging(path):
    """Bounded diagnostic log, configured at app startup, never by the callback."""
    from logging.handlers import RotatingFileHandler
    path = path.resolve()
    if any(getattr(handler, 'baseFilename', None) == str(path) for handler in LOG.handlers):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=256*1024, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        LOG.addHandler(handler); LOG.setLevel(logging.INFO)
    except OSError:
        LOG.warning('Playback file logging unavailable; warnings still go to stderr.', exc_info=True)


@dataclass
class Cursor:
    start: int
    end: int
    frame: int
    loop: bool = False

    def render(self, source, output):
        output.fill(0)
        offset = 0
        while offset < len(output):
            if self.frame >= self.end:
                if self.loop and self.end > self.start:
                    self.frame = self.start
                else:
                    break
            n = min(len(output)-offset, self.end-self.frame)
            output[offset:offset+n] = source[self.frame:self.frame+n]
            self.frame += n
            offset += n
        return offset


def load_audio(document):
    arrays, peaks = {}, {}
    for key, asset in document.data['assets'].items():
        wave, rate = sf.read(document.root / asset['path'], dtype='float32', always_2d=True)
        if not np.all(np.isfinite(wave)):
            raise ValueError(f'{key} audio contains invalid samples')
        if rate != document.data['timeline']['sample_rate'] or len(wave) != document.data['timeline']['frames']:
            raise ValueError(f'{key} audio does not match the project sample clock')
        arrays[key] = np.ascontiguousarray(wave)
        # Preserve both channels' extrema, including anti-phase stereo.
        block = 256
        count = (len(wave)+block-1)//block
        padded = np.pad(wave, ((0, count*block-len(wave)), (0,0)))
        groups = padded.reshape(count, block, wave.shape[1])
        peaks[key] = (groups.min(axis=(1,2)), groups.max(axis=(1,2)), block/rate)
    return arrays, peaks


class Transport:
    def __init__(self, arrays, rate):
        if not arrays or 'mix' not in arrays or type(rate) is not int or rate <= 0:
            raise ValueError('Playback needs a MIX and a positive sample rate.')
        shape = arrays['mix'].shape
        if len(shape) != 2 or shape[1] not in (1,2) or not shape[0]:
            raise ValueError('Playback requires nonempty mono or stereo PCM.')
        if any(a.shape != shape or a.dtype != np.float32 or not a.flags.c_contiguous for a in arrays.values()):
            raise ValueError('Playback sources must share contiguous float32 PCM shape.')
        self.arrays, self.rate = arrays, rate
        self.channels = shape[1]
        # ~40–46 ms callbacks, with 200 ms requested device buffering. The
        # default Studio 24c MME advertises 180 ms high-latency operation.
        self.blocksize = max(64, 2 ** math.ceil(math.log2(rate * CALLBACK_SECONDS)))
        self.requested_latency = PLAYBACK_LATENCY
        self.callbacks = self.output_underflows = self.status_events = 0
        self.max_callback_seconds = 0.0
        self.actual_latency = None
        self.last_status = ''
        self._reported_status_events = 0
        self._render_source = 'mix'
        self._fade_frames = min(self.blocksize, max(1, round(rate * .005)))
        self._fade = np.linspace(0, 1, self._fade_frames, dtype=np.float32)[:,None]
        self._fade_out = 1-self._fade
        self._mix_scratch = np.empty((self.blocksize, self.channels), np.float32)
        self._fade_in = True
        self._draining = False
        self.total = len(arrays['mix'])
        self.source = 'mix'
        self.volume = .7
        self.stream = None
        self.cursor = Cursor(0, self.total, 0)
        self.anchors = deque(maxlen=64)
        self.parked = 0
        self.active = False
        self.warning = ''

    def _callback(self, output, frames, timing, status):
        started = time.perf_counter()
        if self._draining:
            output.fill(0)
            return
        c = self.cursor
        # Single callback writer; the GUI snapshots the deque under the GIL.
        # No GUI-owned mutex, logging, disk access or decoding on this path.
        self.anchors.append((timing.outputBufferDacTime, c.frame, c.start, c.end, c.loop, frames))
        source = self.source
        old = Cursor(c.start, c.end, c.frame, c.loop) if source != self._render_source else None
        c.render(self.arrays[source], output)
        if old is not None:
            n = min(frames, self._fade_frames)
            scratch = self._mix_scratch[:n]
            old.render(self.arrays[self._render_source], scratch)
            np.multiply(output[:n], self._fade[:n], out=output[:n])
            np.multiply(scratch, self._fade_out[:n], out=scratch)
            np.add(output[:n], scratch, out=output[:n])
            self._render_source = source
        if self._fade_in:
            n = min(frames, self._fade_frames)
            np.multiply(output[:n], self._fade[:n], out=output[:n])
            self._fade_in = False
        np.multiply(output, self.volume, out=output)
        np.clip(output, -1., 1., out=output)
        self.callbacks += 1
        if status:
            self.status_events += 1
            self.output_underflows += int(status.output_underflow)
            self.warning = self.last_status = str(status)
        self.max_callback_seconds = max(self.max_callback_seconds, time.perf_counter()-started)

    def diagnostics(self):
        return dict(backend='sounddevice/PortAudio', sample_rate=self.rate, channels=self.channels,
                    dtype='float32', blocksize=self.blocksize, block_seconds=self.blocksize/self.rate,
                    requested_latency=self.requested_latency, actual_latency=self.actual_latency,
                    callbacks=self.callbacks, output_underflows=self.output_underflows,
                    status_events=self.status_events, last_status=self.last_status,
                    max_callback_seconds=self.max_callback_seconds)

    def position(self):
        if not self.active or self.stream is None:
            return self.parked
        now = self.stream.time
        anchors = tuple(self.anchors)
        candidates = [a for a in anchors if a[0] <= now]
        if not candidates:
            return self.parked
        when, frame, start, end, loop, length = candidates[-1]
        elapsed = min(length, max(0, int((now-when)*self.rate)))
        if loop:
            return start + (frame-start+elapsed) % (end-start)
        return min(end, frame+elapsed)

    def halt(self):
        was_active = self.active
        self.parked = self.position()
        self.active = False
        if self.stream is not None and was_active:
            self.stream.abort()  # Discard old queued sound; retain the open device.

    def close(self):
        self.halt()
        if self.stream is not None:
            old, self.stream = self.stream, None
            old.close()
        LOG.info('Playback closed: %s', self.diagnostics())

    def play(self, start, end, position=None, loop=False):
        start, end = max(0, int(start)), min(self.total, int(end))
        if start >= end:
            return
        self.halt()
        position = start if position is None else max(start, min(int(position), end-1))
        self.cursor = Cursor(start, end, position, loop)
        self.parked = position
        self.anchors.clear()
        self.warning = ''
        self._render_source = self.source
        self._fade_in = True
        try:
            if self.stream is None:
                self.stream = sd.OutputStream(samplerate=self.rate, channels=self.channels, dtype='float32',
                                              blocksize=self.blocksize, latency=self.requested_latency,
                                              callback=self._callback)
                self.actual_latency = float(self.stream.latency)
                LOG.info('Playback opened device=%s settings=%s', self.stream.device, self.diagnostics())
            self.active = True
            self.stream.start()
        except BaseException:
            self.close()
            raise

    def pause(self):
        if not self.active or self.stream is None:
            return
        # MME's estimated audible clock can lag the true output by a device
        # period. Abort + rewind to that estimate repeats a short fragment.
        # Freeze the PCM producer, drain the queued audio, then park at its
        # exact sample tail. This trades at most the output buffer for accuracy.
        self._draining = True
        try:
            self.stream.stop()
            c = self.cursor
            self.parked = c.start if c.loop and c.frame >= c.end else c.frame
            self.active = False
        finally:
            self._draining = False

    def resume(self):
        c = self.cursor
        self.play(c.start, c.end, self.parked if self.parked < c.end else c.start, c.loop)

    def seek(self, frame):
        was_active = self.active
        c = self.cursor
        self.halt()
        self.parked = max(0, min(self.total, int(frame)))
        # Seeking outside an audition changes to the full song; no hidden jump back.
        if not c.start <= self.parked < c.end:
            self.cursor = Cursor(0, self.total, self.parked, c.loop)
        if was_active and self.parked < self.total:
            self.resume()

    def switch(self, source):
        if source not in self.arrays or source == self.source:
            return
        # Both arrays share the exact sample clock. Switch on the existing
        # callback stream without seeking, flushing, or restarting playback.
        self.source = source  # Atomic reference swap; applied on the next callback.

    def set_loop(self, enabled):
        # Rebuild from the audible frame so queued old loop state is flushed.
        was_active = self.active
        self.halt()
        self.cursor.loop = enabled
        if was_active:
            self.resume()

    def poll(self):
        if self.status_events != self._reported_status_events:
            self._reported_status_events = self.status_events
            LOG.warning('Playback status: %s', self.diagnostics())
        pos = self.position()
        if self.active and not self.cursor.loop and pos >= self.cursor.end:
            self.halt()
            self.parked = self.cursor.end
        return pos
