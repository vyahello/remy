import os
import wave

import numpy as np

from tokcut import music as M


def test_generate_length_and_range():
    track = M.generate(2.0, bpm=84, style="synthwave")
    assert track.shape == (int(2.0 * M.SR), 2)  # stereo
    assert track.dtype == np.float32
    assert np.abs(track).max() <= 1.0 + 1e-6


def test_generate_is_deterministic():
    a = M.generate(1.5, seed=7)
    b = M.generate(1.5, seed=7)
    assert np.array_equal(a, b)


def test_generate_styles_differ():
    a = M.generate(1.5, style="synthwave", seed=1)
    b = M.generate(1.5, style="phonk", seed=1)
    assert not np.array_equal(a, b)


def test_generate_not_silent():
    track = M.generate(2.0)
    assert np.abs(track).mean() > 0.01


def test_write_wav_roundtrip(tmp_path):
    out = tmp_path / "m.wav"
    M.write_wav(M.generate(1.0), str(out))
    assert os.path.exists(out)
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == M.SR
        assert w.getnchannels() == 2  # stereo
        assert w.getnframes() == int(1.0 * M.SR)


def test_style_bpm_defaults():
    from tokcut.music import STYLE_BPM, generate
    assert STYLE_BPM["phonk"] > STYLE_BPM["synthwave"]
    # bpm=None resolves to the style default and still renders
    track = generate(2.0, style="phonk")
    assert len(track) == 2 * 44100


def test_phonk_differs_from_explicit_slow_bpm():
    from tokcut.music import generate
    fast = generate(2.0, style="phonk")          # 132 default
    slow = generate(2.0, bpm=84, style="phonk")  # pinned slow
    assert not (fast == slow).all()
