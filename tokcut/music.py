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


def _lowpass(sig: np.ndarray, cutoff: float = 2200) -> np.ndarray:
    """One-pole low-pass for warmth."""
    rc = 1.0 / (2 * np.pi * cutoff)
    alpha = (1 / SR) / (rc + 1 / SR)
    out = np.empty_like(sig)
    acc = 0.0
    for i, x in enumerate(sig):
        acc += alpha * (x - acc)
        out[i] = acc
    return out


def generate(
    duration: float, bpm: int = 84, style: str = "synthwave", seed: int = 0
) -> np.ndarray:
    """Return a mono float32 track of `duration` seconds in [-1, 1]."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    track = np.zeros(n)
    notes = SCALE.get(style, SCALE["synthwave"])
    beat = 60.0 / bpm
    bar = beat * 4

    # --- bass + pad on a 4-bar minor progression ---
    prog = [0, 0, 3, 4]  # scale-degree indices
    t = 0.0
    bar_i = 0
    while t < duration:
        root = notes[prog[bar_i % len(prog)]]
        seg_n = min(int(bar * SR), n - int(t * SR))
        if seg_n <= 0:
            break
        s = int(t * SR)
        # sub bass
        bass = np.sin(2 * np.pi * root * np.arange(seg_n) / SR)
        track[s:s + seg_n] += 0.30 * bass * _adsr(seg_n, 0.02, 0.3)
        # pad an octave up
        pad = _supersaw(root * 2, seg_n)
        track[s:s + seg_n] += 0.10 * pad * _adsr(seg_n, 0.4, 0.6)
        t += bar
        bar_i += 1

    # --- kick on every beat ---
    kn = int(0.25 * SR)
    k = _kick(kn)
    bt = 0.0
    while bt < duration:
        s = int(bt * SR)
        end = min(s + kn, n)
        track[s:end] += 0.55 * k[:end - s]
        bt += beat

    # --- sparse arp shimmer (phonk/synthwave lead) ---
    an = int(beat / 2 * SR)
    at = bar  # start after first bar
    while at < duration:
        if rng.random() < 0.6:
            note = notes[rng.integers(0, len(notes))] * 4
            s = int(at * SR)
            end = min(s + an, n)
            lead = _saw(note, end - s) * _adsr(end - s, 0.005, 0.15)
            track[s:end] += 0.07 * lead
        at += beat / 2

    track = _lowpass(track, 2400)
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
