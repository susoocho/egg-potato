from captions import POOL, from_name, pick


def test_caption_from_file_name():
    assert from_name("그네__POV: he did it again.mp4") == ["POV: he did it again"]
    assert from_name("그네__a | b.mp4") == ["a", "b"]
    assert from_name("그네.mp4") == []
    assert pick("그네__a | b.mp4", 3) == ["a", "b", "a"]


def test_pool_picks_differ_per_variant_and_are_stable():
    got = pick("그네.mp4", 5)
    assert len(set(got)) == 5 and all(g in POOL for g in got)
    assert pick("그네.mp4", 5) == got
    assert pick("그네__.mp4", 5) == got  # empty caption text falls back to the pool
