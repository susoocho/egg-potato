from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from upload import Video, make_title, pick_today, publish_times, resting_originals

KST = ZoneInfo("Asia/Seoul")


def v(name, created="2026-11-29T01:00:00Z"):
    return Video(id=name, name=name, created=created)


def test_variant_parsing():
    assert v("아카시로_3.mp4").base == "아카시로"
    assert v("아카시로_3.mp4").variant == 3
    assert v("Color trend v2.mov").base == "Color trend"
    assert v("wood_face.mp4").base == "wood_face"
    assert v("wood_face.mp4").display_name == "wood face"
    assert v("babe 2026.mp4").base == "babe 2026"


def test_one_variant_per_original_per_day_oldest_first():
    queue = [
        v("B_1.mp4", "2026-11-29T02:00:00Z"), v("B_2.mp4", "2026-11-29T02:00:00Z"),
        v("A_2.mp4", "2026-11-29T01:00:00Z"), v("A_1.mp4", "2026-11-29T01:00:00Z"),
        v("C_1.mp4", "2026-11-29T03:00:00Z"),
    ]
    picked = pick_today(queue, per_day=27, max_per_original=1)
    assert [p.name for p in picked] == ["A_1.mp4", "B_1.mp4", "C_1.mp4"]
    picked = pick_today(queue, per_day=4, max_per_original=2)
    assert [p.name for p in picked] == ["A_1.mp4", "B_1.mp4", "C_1.mp4", "A_2.mp4"]


def test_publish_times_spread_and_future():
    now = datetime(2026, 11, 30, 20, 0, tzinfo=timezone.utc)  # 05:00 KST
    ts = publish_times(27, now, KST, "07:00", "23:30")
    assert len(ts) == 27
    assert ts[0].astimezone(KST).strftime("%H:%M") == "07:00"
    assert ts[-1].astimezone(KST).strftime("%H:%M") == "23:30"
    assert all(a < b for a, b in zip(ts, ts[1:]))


def test_publish_times_late_run_moves_to_tomorrow():
    now = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)  # 00:00 KST next day -> same day window fine
    assert publish_times(3, now, KST, "07:00", "23:30")[0].astimezone(KST).day == 1
    now = datetime(2026, 11, 30, 14, 50, tzinfo=timezone.utc)  # 23:50 KST, window passed
    ts = publish_times(3, now, KST, "07:00", "23:30")
    assert ts[0].astimezone(KST).strftime("%d %H:%M") == "01 07:00"


def test_titles_rotate_by_variant():
    t = ["{name} A", "{name} B"]
    assert make_title(v("아카시로_1.mp4"), t) == "아카시로 A"
    assert make_title(v("아카시로_2.mp4"), t) == "아카시로 B"
    assert make_title(v("아카시로_3.mp4"), t) == "아카시로 A"


def test_resting_originals_wait_min_days():
    uploaded = [("A_1.mp4", "2026-11-25"), ("B_1.mp4", "2026-11-19"), ("C_2.mp4", "2026-11-28")]
    resting = resting_originals(uploaded, date(2026, 11, 30), 6)
    assert resting == {"A", "C"}  # B went up 11 days ago, free again
    assert resting_originals(uploaded, date(2026, 11, 30), 0) == set()


def test_pick_today_skips_resting():
    queue = [v("A_2.mp4", "2026-11-29T01:00:00Z"), v("B_2.mp4", "2026-11-29T02:00:00Z")]
    picked = pick_today(queue, per_day=27, max_per_original=1, resting={"A"})
    assert [p.name for p in picked] == ["B_2.mp4"]


def test_variant_without_space():
    assert v("그네2.mp4").base == "그네"
    assert v("그네2.mp4").variant == 2
    assert v("그네 2.mp4").base == "그네"
    assert v("babe 2026.mp4").base == "babe 2026"
