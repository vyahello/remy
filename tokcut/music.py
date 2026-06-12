"""Procedurally synthesized background music.

Generates royalty-free dark-synthwave / phonk backing tracks — no
external audio files, no copyright risk, exact-length to the clip.

The composition layer is real music theory, not noise: chord
progressions (phonk: i-i-VI-VII in G minor; synthwave: the classic
Am-F-C-G), a fixed cowbell riff motif, arpeggios, sidechain pumping and
swing. The mix is stereo, and when Spotify's `pedalboard` library is
installed (the `bot` extra pulls it in) the master bus runs through a
proper FX chain — compression, chorus/reverb, tape-style saturation and
a limiter. Without it a gentle lowpass+soft-clip fallback keeps the
module dependency-free.
"""

import wave

import numpy as np

try:  # optional pro mastering chain — see module docstring
    from pedalboard import (
        Chorus,
        Compressor,
        Distortion,
        HighpassFilter,
        Limiter,
        LowpassFilter,
        Pedalboard,
        Reverb,
    )
    HAS_PEDALBOARD = True
except ImportError:  # pragma: no cover — CI runs the fallback path
    HAS_PEDALBOARD = False

SR = 44100

# each style's natural tempo — phonk lives much faster than synthwave
STYLE_BPM: dict[str, int] = {
    "synthwave": 84,
    "phonk": 132,
}

# tonic of each style's key (Hz): A1 for synthwave, G1 for phonk
ROOT: dict[str, float] = {"synthwave": 55.0, "phonk": 49.00}

# chord progressions as semitone offsets from the tonic.
# synthwave: Am F C G (i-VI-III-VII) — the four chords of the genre.
# phonk: Gm Gm Eb F (i-i-VI-VII) — darker, more static.
PROG: dict[str, list[list[int]]] = {
    "synthwave": [[0, 3, 7], [8, 12, 15], [3, 7, 10], [10, 14, 17]],
    "phonk": [[0, 3, 7], [0, 3, 7], [8, 12, 15], [10, 14, 17]],
}

PENTA = [0, 3, 5, 7, 10]  # minor pentatonic (semitones)

# the cowbell riff: two 2-bar motifs over eighth notes (None = rest).
# A fixed melodic hook is what makes phonk memorable — random notes are
# what made the old generator sound broken.
MOTIFS = [
    [0, None, 3, None, 2, None, 1, 0,
     None, 3, None, 4, 3, None, 2, None],
    [4, None, 3, 2, None, 0, None, 2,
     3, None, 2, None, 0, None, None, None],
]


def _note(root: float, semi: float) -> float:
    return root * 2 ** (semi / 12)


def _adsr(n: int, attack: float = 0.01, release: float = 0.1) -> np.ndarray:
    env = np.ones(n)
    a = min(int(attack * SR), n)
    r = min(int(release * SR), n - a)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return env


def _saw(freq: float, n: int) -> np.ndarray:
    t = np.arange(n) / SR
    return 2 * (t * freq - np.floor(0.5 + t * freq))


def _kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    freq = 110 * np.exp(-t * 22) + 45
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    return body * np.exp(-t * 9)


def _bass808(freq: float, n: int) -> np.ndarray:
    """Booming sub with a pitch drop and tanh drive — the phonk 808."""
    t = np.arange(n) / SR
    sweep = freq * (1 + 2.2 * np.exp(-t * 35))
    phase = 2 * np.pi * np.cumsum(sweep) / SR
    return np.tanh(2.5 * np.sin(phase)) * np.exp(-t * 4.0)


def _hat(n: int, rng: np.random.Generator,
         decay: float = 80.0) -> np.ndarray:
    """High-passed noise tick; lower `decay` = open hat."""
    noise = rng.standard_normal(n)
    hp = np.diff(noise, prepend=0.0)  # crude one-zero highpass
    return hp * np.exp(-np.arange(n) / SR * decay)


def _snare(n: int, rng: np.random.Generator) -> np.ndarray:
    """Snare with a short baked-in noise tail (room, not bathroom)."""
    t = np.arange(n) / SR
    noise = np.diff(rng.standard_normal(n), prepend=0.0)
    tone = np.sin(2 * np.pi * 180 * t)
    body = (0.8 * noise + 0.5 * tone) * np.exp(-t * 18)
    tail = noise * np.exp(-t * 6) * 0.18
    return body + tail


def _gated_snare(n: int, rng: np.random.Generator) -> np.ndarray:
    """The 80s gated-reverb snare: big tail, then a hard cut."""
    t = np.arange(n) / SR
    noise = np.diff(rng.standard_normal(n), prepend=0.0)
    tone = np.sin(2 * np.pi * 190 * t)
    env = np.maximum(np.exp(-t * 14), 0.30)  # decay, then held tail...
    gate = int(0.16 * SR)
    if gate < n:
        env[gate:] *= np.exp(-(t[gate:] - t[gate]) * 200)  # ...slammed
    return (0.85 * noise + 0.4 * tone) * env


def _cowbell(freq: float, n: int) -> np.ndarray:
    """Two detuned square partials — the Memphis phonk cowbell."""
    t = np.arange(n) / SR
    a = np.sign(np.sin(2 * np.pi * freq * t))
    b = np.sign(np.sin(2 * np.pi * freq * 1.48 * t))
    return (0.6 * a + 0.4 * b) * np.exp(-t * 14)


def _pluck(freq: float, n: int) -> np.ndarray:
    """Short bright pluck for arpeggios."""
    return _saw(freq, n) * np.exp(-np.arange(n) / SR * 18)


def _crackle(n: int, rng: np.random.Generator) -> np.ndarray:
    """Vinyl crackle + faint hiss — the Memphis tape patina."""
    clicks = np.where(rng.random(n) < 2.5e-4,
                      rng.standard_normal(n) * 2.0, 0.0)
    hiss = _lowpass(rng.standard_normal(n), 1500) * 0.10
    return clicks + hiss


def _pad_chord(freqs: list[float], n: int) -> np.ndarray:
    """Detuned-saw chord, voices spread across the stereo field."""
    out = np.zeros((n, 2))
    for f in freqs:
        for d, pan in ((-0.012, -0.7), (0.0, 0.0), (0.012, 0.7)):
            voice = _saw(f * (1 + d), n) / (3 * len(freqs))
            left = np.cos((pan + 1) * np.pi / 4)
            out[:, 0] += voice * left
            out[:, 1] += voice * np.sin((pan + 1) * np.pi / 4)
    return out


def _lowpass(sig: np.ndarray, cutoff: float = 2200) -> np.ndarray:
    """One-pole low-pass for warmth.

    Implemented as an FIR convolution with the (truncated) exponential
    impulse response of the one-pole — identical sound, but vectorized:
    the per-sample Python loop it replaces took seconds per track.
    """
    rc = 1.0 / (2 * np.pi * cutoff)
    alpha = (1 / SR) / (rc + 1 / SR)
    taps = int(np.ceil(np.log(1e-5) / np.log(1 - alpha))) + 1
    h = alpha * (1 - alpha) ** np.arange(taps)
    return np.convolve(sig, h)[: len(sig)]


def _sidechain(n: int, kick_times: list[float],
               dip: float = 0.35, recover: float = 0.30) -> np.ndarray:
    """Volume envelope that ducks on every kick and pumps back up.

    The pumping bed is what makes phonk/synthwave *breathe* — melodic
    content drops to `dip` at each kick and recovers over `recover`
    seconds.
    """
    env = np.ones(n)
    seg = int(recover * SR)
    ramp = np.linspace(dip, 1.0, seg)
    for kt in kick_times:
        s = int(kt * SR)
        if s >= n:
            continue
        e = min(s + seg, n)
        env[s:e] = np.minimum(env[s:e], ramp[: e - s])
    return env


def _master(track: np.ndarray, style: str) -> np.ndarray:
    """Master bus: pedalboard FX chain, or a gentle fallback."""
    if HAS_PEDALBOARD:
        if style == "phonk":
            board = Pedalboard([
                HighpassFilter(cutoff_frequency_hz=30),
                Compressor(threshold_db=-16, ratio=3.0,
                           attack_ms=5, release_ms=120),
                Distortion(drive_db=4),          # tape-style grit
                LowpassFilter(cutoff_frequency_hz=9000),
                Reverb(room_size=0.18, wet_level=0.06, dry_level=0.94),
                Limiter(threshold_db=-2.0, release_ms=120),
            ])
        else:
            board = Pedalboard([
                HighpassFilter(cutoff_frequency_hz=28),
                Chorus(rate_hz=0.7, depth=0.2, mix=0.3),
                Reverb(room_size=0.45, wet_level=0.16, dry_level=0.88,
                       width=1.0),
                Compressor(threshold_db=-14, ratio=2.5,
                           attack_ms=8, release_ms=180),
                Limiter(threshold_db=-2.0, release_ms=150),
            ])
        out = board(track.astype(np.float32), SR)
        return np.clip(np.asarray(out, dtype=np.float32), -1.0, 1.0)
    # fallback: warm lowpass + soft clip, per channel
    cutoff = 3800 if style == "phonk" else 2400
    out = np.stack([_lowpass(track[:, c], cutoff) for c in (0, 1)],
                   axis=1)
    peak = float(np.max(np.abs(out))) or 1.0
    return np.tanh(out / peak * 1.1).astype(np.float32)


def generate(
    duration: float, bpm: int | None = None, style: str = "synthwave",
    seed: int = 0
) -> np.ndarray:
    """Return a stereo float32 track, shape (n, 2), in [-1, 1].

    `bpm=None` uses the style's natural tempo (STYLE_BPM).
    """
    bpm = bpm or STYLE_BPM.get(style, 84)
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    # the melodic bed gets sidechain-ducked under the kicks; drums stay
    # at full level on top — that pump is the genre's heartbeat
    bed = np.zeros((n, 2))
    drums = np.zeros((n, 2))
    kick_times: list[float] = []
    root = ROOT.get(style, 55.0)
    prog = PROG.get(style, PROG["synthwave"])
    beat = 60.0 / bpm
    bar = beat * 4

    def hit(buf: np.ndarray, sample: np.ndarray, at: float,
            vol: float, pan: float = 0.0) -> None:
        s = int(at * SR)
        if s >= n:
            return
        end = min(s + len(sample), n)
        cut = sample[: end - s]
        buf[s:end, 0] += vol * np.cos((pan + 1) * np.pi / 4) * cut
        buf[s:end, 1] += vol * np.sin((pan + 1) * np.pi / 4) * cut

    # --- harmonic bed: stereo chord pads over the progression ---
    t = 0.0
    bar_i = 0
    while t < duration:
        chord = prog[bar_i % len(prog)]
        seg_n = min(int(bar * SR), n - int(t * SR))
        if seg_n <= 0:
            break
        s = int(t * SR)
        freqs = [_note(root * 2, semi) for semi in chord]
        pad_vol = 0.30 if style == "phonk" else 0.42
        bed[s:s + seg_n] += (pad_vol * _pad_chord(freqs, seg_n)
                             * _adsr(seg_n, 0.4, 0.6)[:, None])
        if style != "phonk":
            # 8th-note octave-pump bass — the synthwave engine room
            slot = int(beat / 2 * SR)
            for ki in range(8):
                at = t + ki * beat / 2
                f = root if ki % 2 == 0 else root * 2
                m = min(slot, n - int(at * SR))
                if m <= 0:
                    break
                bs = (np.sin(2 * np.pi * f * np.arange(m) / SR)
                      + 0.3 * _saw(f, m))
                hit(bed, bs * _adsr(m, 0.005, 0.10), at, 0.26)
        t += bar
        bar_i += 1

    # --- rhythm + melody ---
    k = _kick(int(0.25 * SR))
    if style == "phonk":
        # trap kick doubled by a gliding 808 on the bar's root, snare
        # backbeat, swung hats with rolls and an open hat, the cowbell
        # riff motif, and vinyl crackle under everything
        sn = _snare(int(0.30 * SR), rng)
        swing = 0.06 * beat
        bar_t = 0.0
        bar_i = 0
        while bar_t < duration:
            chord_root = _note(root, prog[bar_i % len(prog)][0])
            b808 = _bass808(chord_root, int(0.55 * SR))
            for off in (0.0, 1.5, 2.0, 3.5):
                at = bar_t + off * beat
                hit(drums, k, at, 0.55)
                hit(drums, b808, at, 0.50)
                kick_times.append(at)
            for off in (1.0, 3.0):
                hit(drums, sn, bar_t + off * beat, 0.42)
            hh = 0.0
            while hh < 4.0:
                offbeat = hh % 1.0 >= 0.49
                accent = 0.06 if offbeat else 0.10
                hit(drums, _hat(int(0.04 * SR), rng),
                    bar_t + hh * beat + (swing if offbeat else 0.0),
                    accent, pan=0.25)
                step = 0.25 if rng.random() < 0.12 else 0.5
                hh += step
            if bar_i % 2 == 1:  # open hat closing every other bar
                hit(drums, _hat(int(0.18 * SR), rng, decay=22.0),
                    bar_t + 3.5 * beat + swing, 0.07, pan=0.25)
            bar_t += bar
            bar_i += 1
        # the riff: a fixed 2-bar cowbell motif (from the second bar on),
        # with a slapback echo — alternating motifs every 4 bars
        cn = int(0.3 * SR)
        slap = 0.11
        pos = bar
        slot8 = beat / 2
        step_i = 0
        while pos < duration:
            motif = MOTIFS[(step_i // 32) % len(MOTIFS)]
            deg = motif[step_i % len(motif)]
            if deg is not None:
                semi = PENTA[deg % 5] + 12 * (deg // 5)
                cb = _cowbell(_note(root * 8, semi), cn)
                hit(drums, cb, pos, 0.13, pan=-0.2)
                hit(drums, cb, pos + slap, 0.05, pan=0.3)
            pos += slot8
            step_i += 1
        bed[:, 0] += 0.012 * _crackle(n, rng)
        bed[:, 1] += 0.012 * _crackle(n, rng)
    else:
        # synthwave: four-on-the-floor, gated snare on 2 & 4, offbeat
        # hats, and a 16th-note arpeggio cycling the chord tones
        gsn = _gated_snare(int(0.30 * SR), rng)
        bt = 0.0
        beat_i = 0
        while bt < duration:
            hit(drums, k, bt, 0.55)
            kick_times.append(bt)
            if beat_i % 2 == 1:
                hit(drums, gsn, bt, 0.30)
            hit(drums, _hat(int(0.05 * SR), rng),
                bt + beat / 2, 0.06, pan=0.3)
            bt += beat
            beat_i += 1
        an = int(beat / 4 * SR)
        pos = bar  # arp enters after the first bar
        step_i = 0
        while pos < duration:
            chord = prog[int(pos / bar) % len(prog)]
            semi = chord[[0, 1, 2, 1][step_i % 4]] + 12
            pan = 0.6 if step_i % 2 else -0.6
            hit(drums, _pluck(_note(root * 4, semi), an),
                pos, 0.07, pan=pan)
            pos += beat / 4
            step_i += 1

    dip = 0.30 if style == "phonk" else 0.55
    env = _sidechain(n, kick_times, dip=dip,
                     recover=min(0.30, beat * 0.6))
    track = bed * env[:, None] + drums
    peak = float(np.max(np.abs(track))) or 1.0
    track = (track / peak * 0.85).astype(np.float32)
    return _master(track, style)


def write_wav(samples: np.ndarray, path: str) -> None:
    """Write a mono (n,) or stereo (n, 2) float track to 16-bit PCM WAV."""
    pcm = np.clip(samples, -1, 1)
    if pcm.ndim == 1:
        pcm = pcm[:, None]
    data = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
