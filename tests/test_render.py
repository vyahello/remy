from tokcut import render as R


def test_atempo_chain_simple():
    assert R.atempo_chain(1.5) == "atempo=1.500000"


def test_atempo_chain_splits_large_speed():
    chain = R.atempo_chain(3.2)
    factors = [float(p.split("=")[1]) for p in chain.split(",")]
    product = 1.0
    for f in factors:
        assert 0.5 <= f <= 2.0
        product *= f
    assert abs(product - 3.2) < 1e-3


def test_atempo_chain_extreme():
    chain = R.atempo_chain(6.0)
    factors = [float(p.split("=")[1]) for p in chain.split(",")]
    assert all(f <= 2.0 for f in factors)
    product = 1.0
    for f in factors:
        product *= f
    assert abs(product - 6.0) < 1e-3


SRC = {"w": 1038, "h": 1616, "fps": 60, "audio": True}
LAY = {"vw": 1080, "vh": 1680, "vx": 0, "vy": 120, "cap_x": 191,
       "cap_y": 1277}


def test_filtergraph_concat_count():
    segs = [(0, 5, 1.0), (5, 10, 2.0), (10, 15, 3.2)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60)
    assert "concat=n=3" in fc
    assert v == "[vout]"


def test_filtergraph_muted_by_default():
    # source has audio, but default export is silent for in-app sound
    segs = [(0, 5, 1.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60)
    assert a is None
    assert "concat=n=1:v=1[vc]" in fc


def test_filtergraph_keep_audio_retains_ambient():
    segs = [(0, 5, 1.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60, keep_audio=True)
    assert a == "[amb]"
    assert "concat=n=1:v=1:a=1" in fc


def test_filtergraph_music_adds_amix():
    segs = [(0, 5, 1.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60, with_music=True)
    assert "amix=inputs=2" in fc
    assert a == "[aout]"


def test_filtergraph_no_audio_source():
    segs = [(0, 5, 1.0)]
    src = dict(SRC, audio=False)
    fc, v, a = R.build_filtergraph(segs, src, LAY, 60, keep_audio=True)
    assert a is None
    assert "concat=n=1:v=1[vc]" in fc


def test_filtergraph_music_no_audio_source_uses_music_only():
    segs = [(0, 5, 1.0)]
    src = dict(SRC, audio=False)
    fc, v, a = R.build_filtergraph(segs, src, LAY, 60, with_music=True)
    assert a == "[aout]"
    assert "[2:a]volume=0.8[aout]" in fc


def test_filtergraph_landscape_no_caption():
    # lay=None: native resolution, no pad/overlay, no caption input
    segs = [(0, 5, 1.0), (5, 10, 2.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, None, 60)
    assert "overlay" not in fc
    assert "pad=" not in fc
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in fc
    assert v == "[vout]"


def test_filtergraph_landscape_music_index():
    # without a caption input, music is input n (right after segments)
    segs = [(0, 5, 1.0), (5, 10, 2.0)]
    fc, _v, a = R.build_filtergraph(segs, SRC, None, 60, with_music=True)
    assert "[2:a]volume" in fc
    assert a == "[aout]"


def test_filtergraph_vertical_music_index_unchanged():
    # with a caption input at n, music sits at n+1
    segs = [(0, 5, 1.0), (5, 10, 2.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, LAY, 60, with_music=True)
    assert "[3:a]volume" in fc


def test_filtergraph_landscape_keeps_crop():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(
        segs, SRC, None, 60, crop=(10, 20, 800, 600))
    assert "crop=800:600:10:20" in fc


def test_look_filter_variants():
    sdr_cam = {"transfer": "bt709"}
    hdr = {"transfer": "arib-std-b67"}
    assert "unsharp" in R.look_filter(sdr_cam, screen=True)
    assert "unsharp" not in R.look_filter(hdr, screen=False)
    assert "saturation=1.08" in R.look_filter(hdr, screen=False)
    assert "brightness" in R.look_filter(sdr_cam, screen=False)


def test_filtergraph_applies_look():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, None, 60,
                                     look="eq=contrast=1.05")
    assert "eq=contrast=1.05,format" in fc
    fc2, _v, _a = R.build_filtergraph(segs, SRC, LAY, 60,
                                      look="eq=contrast=1.05")
    assert "eq=contrast=1.05,pad" in fc2  # grade before the black bars


def test_filtergraph_no_look_by_default():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, None, 60)
    assert "eq=" not in fc
