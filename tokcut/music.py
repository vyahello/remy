"""Procedurally synthesized background music.

Generates royalty-free dark-synthwave / phonk backing tracks — no
copyright risk, exact-length to the clip.

Three quality tiers, best available wins:
1. **SoundFont instruments** (`tinysoundfont` + a General MIDI .sf2,
   found via TOKCUT_SOUNDFONT or the usual system paths): the
   composition is played by real sampled instruments — choir, strings
   and piano for phonk, polysynth/saw-lead/synth-bass for synthwave,
   a real drum kit — with the synthesized 808 sub, cowbell riff and
   vinyl crackle layered on top.
2. **pedalboard mastering** (Spotify's FX library, pulled in by the
   `bot` extra): the master bus gets compression, chorus/reverb,
   tape-style saturation and a limiter.
3. Pure-numpy oscillator fallback for both — CI needs no extras.

The composition layer is real music theory either way: chord
progressions (phonk: i-i-VI-VII in G minor; synthwave: the classic
Am-F-C-G), a fixed cowbell riff motif, arpeggios, sidechain pumping,
swing and velocity humanization.
"""

import glob
import os
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

try:  # optional SoundFont instruments — see module docstring
    import tinysoundfont
    HAS_SOUNDFONT = True
except ImportError:  # pragma: no cover — CI runs the fallback path
    HAS_SOUNDFONT = False

SR = 44100

# each style's natural tempo — phonk lives much faster than synthwave
STYLE_BPM: dict[str, int] = {
    "synthwave": 84,
    "phonk": 132,
}

# tonic of each style's key: A1 for synthwave, G1 for phonk
ROOT_HZ: dict[str, float] = {"synthwave": 55.0, "phonk": 49.00}
ROOT_MIDI: dict[str, int] = {"synthwave": 33, "phonk": 31}

# chord progressions as semitone offsets from the tonic.
# synthwave: Am F C G (i-VI-III-VII) — the four chords of the genre.
# phonk: Gm Gm Eb F (i-i-VI-VII) — darker, more static.
PROG: dict[str, list[list[int]]] = {
    "synthwave": [[0, 3, 7], [8, 12, 15], [3, 7, 10], [10, 14, 17]],
    "phonk": [[0, 3, 7], [0, 3, 7], [8, 12, 15], [10, 14, 17]],
}

PENTA = [0, 3, 5, 7, 10]  # minor pentatonic (semitones)

# the riff: two 2-bar motifs over eighth notes (None = rest).
# A fixed melodic hook is what makes phonk memorable — random notes are
# what made the old generator sound broken.
MOTIFS = [
    [0, None, 3, None, 2, None, 1, 0,
     None, 3, None, 4, 3, None, 2, None],
    [4, None, 3, 2, None, 0, None, 2,
     3, None, 2, None, 0, None, None, None],
]

# General MIDI programs / percussion notes used by the SoundFont tier
GM_PIANO, GM_SYNTH_BASS, GM_STRINGS = 0, 38, 48
GM_SYNTH_STRINGS, GM_CHOIR, GM_SAW, GM_POLY = 50, 52, 81, 90
DRUM_KICK, DRUM_CLAP, DRUM_SNARE = 36, 39, 40
DRUM_HAT, DRUM_OHAT, DRUM_CRASH = 42, 46, 49

# a note event for the SoundFont renderer:
# (time_sec, dur_sec, midi_note, velocity, gm_program) — program -1
# means the percussion channel (bank 128)
Event = tuple[float, float, int, int, int]


def find_soundfont() -> str | None:
    """Locate a General MIDI .sf2 (TOKCUT_SOUNDFONT wins)."""
    env = os.environ.get("TOKCUT_SOUNDFONT", "")
    if env and os.path.exists(env):
        return env
    for pattern in ("/usr/share/sounds/sf2/*.sf2",
                    "/usr/share/soundfonts/*.sf2",
                    os.path.expanduser("~/.tokcut/*.sf2")):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def _note_hz(root: float, semi: float) -> float:
    return root * 2 ** (semi / 12)


# --------------------------------------------------- synthesized one-shots

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
            out[:, 0] += voice * np.cos((pan + 1) * np.pi / 4)
            out[:, 1] += voice * np.sin((pan + 1) * np.pi / 4)
    return out


def _lowpass(sig: np.ndarray, cutoff: float = 2200) -> np.ndarray:
    """One-pole low-pass, vectorized as a truncated-exponential FIR."""
    rc = 1.0 / (2 * np.pi * cutoff)
    alpha = (1 / SR) / (rc + 1 / SR)
    taps = int(np.ceil(np.log(1e-5) / np.log(1 - alpha))) + 1
    h = alpha * (1 - alpha) ** np.arange(taps)
    return np.convolve(sig, h)[: len(sig)]


def _hit(buf: np.ndarray, sample: np.ndarray, at: float,
         vol: float, pan: float = 0.0) -> None:
    """Place a mono one-shot into a stereo bus with equal-power pan."""
    n = len(buf)
    s = int(at * SR)
    if s >= n or s < 0:
        return
    end = min(s + len(sample), n)
    cut = sample[: end - s]
    buf[s:end, 0] += vol * np.cos((pan + 1) * np.pi / 4) * cut
    buf[s:end, 1] += vol * np.sin((pan + 1) * np.pi / 4) * cut


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


# ------------------------------------------------------ SoundFont renderer

def _sf_render(sf2: str, events: list[Event], duration: float) -> np.ndarray:
    """Play note events through sampled GM instruments. Returns (n, 2)."""
    synth = tinysoundfont.Synth(samplerate=SR)
    sfid = synth.sfload(sf2)
    n = int(duration * SR)
    out = np.zeros(n * 2, dtype=np.float32)

    free = [c for c in range(16) if c != 9]
    chans: dict[int, int] = {}

    def channel(prog: int) -> int:
        if prog == -1:
            if -1 not in chans:
                synth.program_select(9, sfid, 128, 0)  # GM drum kit
                chans[-1] = 9
            return 9
        if prog not in chans:
            ch = free.pop(0)
            synth.program_select(ch, sfid, 0, prog)
            chans[prog] = ch
        return chans[prog]

    timeline: list[tuple[float, int, int, int, int]] = []
    for t, dur, note, vel, prog in events:
        note = min(127, max(0, note))
        timeline.append((max(0.0, t), 1, note, min(127, max(1, vel)),
                         prog))
        timeline.append((t + dur, 0, note, 0, prog))
    timeline.sort(key=lambda e: (e[0], e[1]))

    pos = 0
    for t, on, note, vel, prog in timeline:
        s = min(n, int(t * SR))
        if s > pos:
            chunk = synth.generate(s - pos)
            out[pos * 2: s * 2] = np.frombuffer(bytes(chunk), np.float32)
            pos = s
        if pos >= n:
            break
        ch = channel(prog)
        if on:
            synth.noteon(ch, note, vel)
        else:
            synth.noteoff(ch, note)
    if pos < n:
        chunk = synth.generate(n - pos)
        out[pos * 2:] = np.frombuffer(bytes(chunk), np.float32)
    return out.reshape(-1, 2).astype(np.float64)


def _compose_sf(duration: float, bpm: int, style: str, sf2: str,
                rng: np.random.Generator
                ) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """SoundFont-tier composition. Returns (bed, drums, kick_times)."""
    n = int(duration * SR)
    beat = 60.0 / bpm
    bar = beat * 4
    root_m = ROOT_MIDI.get(style, 33)
    prog = PROG.get(style, PROG["synthwave"])
    bed_ev: list[Event] = []
    drum_ev: list[Event] = []
    kick_times: list[float] = []

    def vel(base: int, spread: int = 8) -> int:
        return int(base + rng.integers(-spread, spread + 1))

    def shift(t: float) -> float:  # human timing jitter (hats/arp only)
        return t + float(rng.uniform(-0.004, 0.004))

    t = 0.0
    bar_i = 0
    while t < duration:
        chord = prog[bar_i % len(prog)]
        if style == "phonk":
            for s in chord:  # eerie choir + low strings carry the bar
                bed_ev.append((t, bar * 0.98, root_m + 24 + s,
                               vel(52, 4), GM_CHOIR))
                bed_ev.append((t, bar * 0.98, root_m + 12 + s,
                               vel(40, 4), GM_STRINGS))
        else:
            for s in chord:  # polysynth + synth-string pad stack
                bed_ev.append((t, bar * 0.98, root_m + 24 + s,
                               vel(58, 4), GM_POLY))
                bed_ev.append((t, bar * 0.98, root_m + 12 + s,
                               vel(44, 4), GM_SYNTH_STRINGS))
            for k in range(8):  # 8th-note octave-pump synth bass
                note = root_m + (0 if k % 2 == 0 else 12)
                bed_ev.append((t + k * beat / 2, beat * 0.45, note,
                               vel(88), GM_SYNTH_BASS))
        t += bar
        bar_i += 1

    bed = _sf_render(sf2, bed_ev, duration)

    drums = np.zeros((n, 2))
    k_punch = _kick(int(0.25 * SR))
    if style == "phonk":
        swing = 0.06 * beat
        bar_t, bar_i = 0.0, 0
        while bar_t < duration:
            chord_root = _note_hz(ROOT_HZ["phonk"],
                                  prog[bar_i % len(prog)][0])
            b808 = _bass808(chord_root, int(0.55 * SR))
            for off in (0.0, 1.5, 2.0, 3.5):
                at = bar_t + off * beat
                drum_ev.append((at, 0.2, DRUM_KICK, vel(115, 5), -1))
                _hit(drums, k_punch, at, 0.40)  # synth layer = punch
                _hit(drums, b808, at, 0.50)     # synth layer = sub
                kick_times.append(at)
            for off in (1.0, 3.0):
                at = bar_t + off * beat
                drum_ev.append((at, 0.2, DRUM_SNARE, vel(105, 5), -1))
                drum_ev.append((at, 0.2, DRUM_CLAP, vel(68), -1))
            hh = 0.0
            while hh < 4.0:
                offbeat = hh % 1.0 >= 0.49
                at = bar_t + hh * beat + (swing if offbeat else 0.0)
                drum_ev.append((shift(at), 0.1, DRUM_HAT,
                                vel(48 if offbeat else 64), -1))
                hh += 0.25 if rng.random() < 0.12 else 0.5
            if bar_i % 2 == 1:
                drum_ev.append((bar_t + 3.5 * beat + swing, 0.3,
                                DRUM_OHAT, vel(52), -1))
            bar_t += bar
            bar_i += 1
        # the riff: synthesized Memphis cowbell + piano doubling an
        # octave up — a fixed 2-bar motif, alternating every 4 bars
        cn = int(0.3 * SR)
        pos = bar
        step_i = 0
        while pos < duration:
            motif = MOTIFS[(step_i // 32) % len(MOTIFS)]
            deg = motif[step_i % len(motif)]
            if deg is not None:
                semi = PENTA[deg % 5] + 12 * (deg // 5)
                cb = _cowbell(_note_hz(ROOT_HZ["phonk"] * 8, semi), cn)
                _hit(drums, cb, pos, 0.12, pan=-0.2)
                _hit(drums, cb, pos + 0.11, 0.045, pan=0.3)
                drum_ev.append((pos, 0.20, root_m + 36 + semi,
                                vel(72), GM_PIANO))
            pos += beat / 2
            step_i += 1
        bed[:, 0] += 0.012 * _crackle(n, rng)
        bed[:, 1] += 0.012 * _crackle(n, rng)
    else:
        gsn = _gated_snare(int(0.30 * SR), rng)
        bt, beat_i = 0.0, 0
        while bt < duration:
            drum_ev.append((bt, 0.2, DRUM_KICK, vel(112, 5), -1))
            _hit(drums, k_punch, bt, 0.30)
            kick_times.append(bt)
            if beat_i % 2 == 1:
                drum_ev.append((bt, 0.2, DRUM_SNARE, vel(96, 5), -1))
                _hit(drums, gsn, bt, 0.18)  # the 80s gated tail
            drum_ev.append((shift(bt + beat / 2), 0.1, DRUM_HAT,
                            vel(52), -1))
            if beat_i % 32 == 0:
                drum_ev.append((bt, 0.6, DRUM_CRASH, vel(70), -1))
            bt += beat
            beat_i += 1
        pos = bar  # saw-lead 16th arpeggio enters after the first bar
        step_i = 0
        while pos < duration:
            chord = prog[int(pos / bar) % len(prog)]
            semi = chord[[0, 1, 2, 1][step_i % 4]] + 24
            drum_ev.append((shift(pos), beat * 0.22, root_m + semi,
                            vel(58), GM_SAW))
            pos += beat / 4
            step_i += 1

    drums += _sf_render(sf2, drum_ev, duration)
    return bed, drums, kick_times


# ----------------------------------------------------- oscillator fallback

def _compose_osc(duration: float, bpm: int, style: str,
                 rng: np.random.Generator
                 ) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Pure-numpy composition (no SoundFont). Same musical structure."""
    n = int(duration * SR)
    bed = np.zeros((n, 2))
    drums = np.zeros((n, 2))
    kick_times: list[float] = []
    root = ROOT_HZ.get(style, 55.0)
    prog = PROG.get(style, PROG["synthwave"])
    beat = 60.0 / bpm
    bar = beat * 4

    t = 0.0
    bar_i = 0
    while t < duration:
        chord = prog[bar_i % len(prog)]
        seg_n = min(int(bar * SR), n - int(t * SR))
        if seg_n <= 0:
            break
        s = int(t * SR)
        freqs = [_note_hz(root * 2, semi) for semi in chord]
        pad_vol = 0.30 if style == "phonk" else 0.42
        bed[s:s + seg_n] += (pad_vol * _pad_chord(freqs, seg_n)
                             * _adsr(seg_n, 0.4, 0.6)[:, None])
        if style != "phonk":
            for ki in range(8):  # 8th-note octave-pump bass
                at = t + ki * beat / 2
                f = root if ki % 2 == 0 else root * 2
                m = min(int(beat / 2 * SR), n - int(at * SR))
                if m <= 0:
                    break
                bs = (np.sin(2 * np.pi * f * np.arange(m) / SR)
                      + 0.3 * _saw(f, m))
                _hit(bed, bs * _adsr(m, 0.005, 0.10), at, 0.26)
        t += bar
        bar_i += 1

    k = _kick(int(0.25 * SR))
    if style == "phonk":
        sn = _snare(int(0.30 * SR), rng)
        swing = 0.06 * beat
        bar_t, bar_i = 0.0, 0
        while bar_t < duration:
            chord_root = _note_hz(root, prog[bar_i % len(prog)][0])
            b808 = _bass808(chord_root, int(0.55 * SR))
            for off in (0.0, 1.5, 2.0, 3.5):
                at = bar_t + off * beat
                _hit(drums, k, at, 0.55)
                _hit(drums, b808, at, 0.50)
                kick_times.append(at)
            for off in (1.0, 3.0):
                _hit(drums, sn, bar_t + off * beat, 0.42)
            hh = 0.0
            while hh < 4.0:
                offbeat = hh % 1.0 >= 0.49
                _hit(drums, _hat(int(0.04 * SR), rng),
                     bar_t + hh * beat + (swing if offbeat else 0.0),
                     0.06 if offbeat else 0.10, pan=0.25)
                hh += 0.25 if rng.random() < 0.12 else 0.5
            if bar_i % 2 == 1:
                _hit(drums, _hat(int(0.18 * SR), rng, decay=22.0),
                     bar_t + 3.5 * beat + swing, 0.07, pan=0.25)
            bar_t += bar
            bar_i += 1
        cn = int(0.3 * SR)
        pos = bar
        step_i = 0
        while pos < duration:
            motif = MOTIFS[(step_i // 32) % len(MOTIFS)]
            deg = motif[step_i % len(motif)]
            if deg is not None:
                semi = PENTA[deg % 5] + 12 * (deg // 5)
                cb = _cowbell(_note_hz(root * 8, semi), cn)
                _hit(drums, cb, pos, 0.13, pan=-0.2)
                _hit(drums, cb, pos + 0.11, 0.05, pan=0.3)
            pos += beat / 2
            step_i += 1
        bed[:, 0] += 0.012 * _crackle(n, rng)
        bed[:, 1] += 0.012 * _crackle(n, rng)
    else:
        gsn = _gated_snare(int(0.30 * SR), rng)
        bt, beat_i = 0.0, 0
        while bt < duration:
            _hit(drums, k, bt, 0.55)
            kick_times.append(bt)
            if beat_i % 2 == 1:
                _hit(drums, gsn, bt, 0.30)
            _hit(drums, _hat(int(0.05 * SR), rng),
                 bt + beat / 2, 0.06, pan=0.3)
            bt += beat
            beat_i += 1
        an = int(beat / 4 * SR)
        pos = bar
        step_i = 0
        while pos < duration:
            chord = prog[int(pos / bar) % len(prog)]
            semi = chord[[0, 1, 2, 1][step_i % 4]] + 12
            _hit(drums, _pluck(_note_hz(root * 4, semi), an),
                 pos, 0.07, pan=0.6 if step_i % 2 else -0.6)
            pos += beat / 4
            step_i += 1

    return bed, drums, kick_times


# ----------------------------------------------------------------- master

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

    sf2 = find_soundfont() if HAS_SOUNDFONT else None
    if sf2:
        bed, drums, kick_times = _compose_sf(duration, bpm, style,
                                             sf2, rng)
    else:
        bed, drums, kick_times = _compose_osc(duration, bpm, style, rng)

    # the melodic bed pumps under the kicks — the genre's heartbeat
    dip = 0.30 if style == "phonk" else 0.55
    env = _sidechain(n, kick_times, dip=dip,
                     recover=min(0.30, (60.0 / bpm) * 0.6))
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
