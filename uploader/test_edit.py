import numpy as np

from edit import FPS, Settings, find_still, find_trim, plan_segments


def metrics(shake, change=None, n=None):
    n = len(shake)
    return {"shake": np.array(shake, float),
            "change": np.array(change if change is not None else [5.0] * n, float),
            "sharp": np.full(n, 500.0), "bright": np.full(n, 120.0)}


def test_trims_shaky_ends():
    shake = [20] * 15 + [0] * 70 + [15] * 10  # 1.5s shaky start, 1s shaky end
    assert find_trim(metrics(shake), Settings()) == (15, 84)


def test_never_trims_more_than_limit():
    shake = [20] * 80 + [0] * 20  # mostly shaky: keep it rather than cut 80%
    start, end = find_trim(metrics(shake), Settings())
    assert start <= 30 and end == 99


def test_dark_end_is_cut():
    m = metrics([0] * 100)
    m["bright"][90:] = 2
    assert find_trim(m, Settings())[1] == 89


def test_still_stretch_sped_up():
    change = [5] * 30 + [0.2] * 30 + [5] * 10 + [0.2] * 10 + [5] * 20  # 3s still, 1s still
    m = metrics([0] * 100, change)
    still = find_still(m, 0, 99, Settings())
    assert still == [(30, 59)]  # the 1s pause stays at normal speed
    segs = plan_segments(10.0, 0, 99, still, Settings())
    assert segs == [(0.0, 3.0, 1.0), (3.0, 6.0, 2.0), (6.0, 10.0, 1.0)]
    assert sum((b - a) / sp for a, b, sp in segs) == 8.5
