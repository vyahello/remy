"""Procedurally synthesized background music.

Generates royalty-free dark-synthwave / phonk style backing tracks with
numpy — no external files, no copyright risk, exact-length to the clip.
This is the safe default; a real curated track can be supplied with
--music FILE instead (see render.build_filtergraph).
"""

import numpy as np

SR = 44100

# minor-key root notes (Hz) for a moody, atmospheric feel
SCALE: dict[str, list[float]] = {
    # A minor pentatonic
    "synthwave": [55.00, 65.41, 73.42, 82.41, 98.00],
    # G minor, lower and heavier
    "phonk": [49.00, 58.27, 65.41, 73.42, 87.31],
}

# each style's natural tempo — phonk lives much faster than synthwave
STYLE_BPM: dict[str, int] = {
    "synthwave": 84,
    "phonk": 132,
}


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


def _supersaw(freq: float, n: int, detune: float = 0.012) -> np.ndarray:
    """Three slightly detuned saws — the classic synthwave pad."""
    out = np.zeros(n)
    for d in (-detune, 0.0, detune):
        out += _saw(freq * (1 + d), n)
    return out / 3


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


def _hat(n: int, rng: np.random.Generator) -> np.ndarray:
    """Short high-passed noise tick."""
    noise = rng.standard_normal(n)
    hp = np.diff(noise, prepend=0.0)  # crude one-zero highpass
    return hp * np.exp(-np.arange(n) / SR * 80)


def _snare(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    noise = np.diff(rng.standard_normal(n), prepend=0.0)
    tone = np.sin(2 * np.pi * 180 * t)
    return (0.8 * noise + 0.5 * tone) * np.exp(-t * 18)


def _cowbell(freq: float, n: int) -> np.ndarray:
    """Two detuned square partials — the Memphis phonk cowbell."""
    t = np.arange(n) / SR
    a = np.sign(np.sin(2 * np.pi * freq * t))
    b = np.sign(np.sin(2 * np.pi * freq * 1.48 * t))
    return (0.6 * a + 0.4 * b) * np.exp(-t * 14)


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


def generate(
    duration: float, bpm: int | None = None, style: str = "synthwave",
    seed: int = 0
) -> np.ndarray:
    """Return a mono float32 track of `duration` seconds in [-1, 1].

    `bpm=None` uses the style's natural tempo (STYLE_BPM).
    """
    bpm = bpm or STYLE_BPM.get(style, 84)
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    # the melodic bed gets sidechain-ducked under the kicks; drums stay
    # at full level on top — that pump is the genre's heartbeat
    bed = np.zeros(n)
    drums = np.zeros(n)
    kick_times: list[float] = []
    notes = SCALE.get(style, SCALE["synthwave"])
    beat = 60.0 / bpm
    bar = beat * 4

    def hit(buf: np.ndarray, sample: np.ndarray,
            at: float, vol: float) -> None:
        s = int(at * SR)
        if s >= n:
            return
        end = min(s + len(sample), n)
        buf[s:end] += vol * sample[:end - s]

    # --- melodic bed on a 4-bar minor progression ---
    prog = [0, 0, 3, 4]  # scale-degree indices
    t = 0.0
    bar_i = 0
    while t < duration:
        root = notes[prog[bar_i % len(prog)]]
        seg_n = min(int(bar * SR), n - int(t * SR))
        if seg_n <= 0:
            break
        s = int(t * SR)
        if style != "phonk":
            # held sub bass — phonk gets 808 hits in the drum loop instead
            bass = np.sin(2 * np.pi * root * np.arange(seg_n) / SR)
            bed[s:s + seg_n] += 0.30 * bass * _adsr(seg_n, 0.02, 0.3)
        pad = _supersaw(root * 2, seg_n)
        pad_vol = 0.08 if style == "phonk" else 0.10
        bed[s:s + seg_n] += pad_vol * pad * _adsr(seg_n, 0.4, 0.6)
        t += bar
        bar_i += 1

    # --- rhythm section ---
    k = _kick(int(0.25 * SR))
    if style == "phonk":
        # bouncy trap-style kick doubled by a gliding 808 on the bar's
        # root, snare backbeat, swung 8th hats with 16th rolls, and the
        # Memphis cowbell with a slapback echo
        sn = _snare(int(0.20 * SR), rng)
        swing = 0.06 * beat  # offbeats land late — that's the bounce
        bar_t = 0.0
        bar_i = 0
        while bar_t < duration:
            root = notes[prog[bar_i % len(prog)]]
            b808 = _bass808(root, int(0.5 * SR))
            for off in (0.0, 1.5, 2.0, 3.5):
                at = bar_t + off * beat
                hit(drums, k, at, 0.55)
                hit(drums, b808, at, 0.50)
                kick_times.append(at)
            for off in (1.0, 3.0):
                hit(drums, sn, bar_t + off * beat, 0.40)
            hh = 0.0
            while hh < 4.0:
                offbeat = hh % 1.0 >= 0.49
                accent = 0.06 if offbeat else 0.10
                hit(drums, _hat(int(0.04 * SR), rng),
                    bar_t + hh * beat + (swing if offbeat else 0.0),
                    accent)
                # sprinkle a 16th-note roll now and then
                step = 0.25 if rng.random() < 0.12 else 0.5
                hh += step
            bar_t += bar
            bar_i += 1
        # cowbell lead from the second bar on, with a slapback echo
        cn = int(0.3 * SR)
        slap = int(0.11 * SR)
        cb_t = bar
        while cb_t < duration:
            if rng.random() < 0.65:
                note = notes[rng.integers(0, len(notes))] * 8
                cb = _cowbell(note, cn)
                hit(drums, cb, cb_t, 0.13)
                hit(drums, cb, cb_t + slap / SR, 0.05)
            cb_t += beat / 2
    else:
        # synthwave: four-on-the-floor kick + sparse arp shimmer
        bt = 0.0
        while bt < duration:
            hit(drums, k, bt, 0.55)
            kick_times.append(bt)
            bt += beat
        an = int(beat / 2 * SR)
        at = bar  # start after first bar
        while at < duration:
            if rng.random() < 0.6:
                note = notes[rng.integers(0, len(notes))] * 4
                end = min(int(at * SR) + an, n)
                seg_n = end - int(at * SR)
                lead = _saw(note, seg_n) * _adsr(seg_n, 0.005, 0.15)
                hit(drums, lead, at, 0.07)
            at += beat / 2

    dip = 0.30 if style == "phonk" else 0.55
    track = bed * _sidechain(n, kick_times, dip=dip,
                             recover=min(0.30, beat * 0.6)) + drums
    track = _lowpass(track, 3800 if style == "phonk" else 2400)
    # normalize and soft-clip
    peak = np.max(np.abs(track)) or 1.0
    track = np.tanh(track / peak * 1.1)
    return track.astype(np.float32)


def write_wav(samples: np.ndarray, path: str) -> None:
    """Write a mono float track to a 16-bit PCM WAV."""
    import wave
    pcm = np.clip(samples, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
