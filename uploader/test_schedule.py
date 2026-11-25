from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from schedule import Short, plan

KST = ZoneInfo("Asia/Seoul")
CFG = {
    "per_day": 3, "min_days_between_variants": 6, "timezone": "Asia/Seoul",
    "publish_start": "07:00", "publish_end": "23:00",
    "scheduler": {"since": "2026-11-26", "first_publish_date": "", "max_days_ahead": 30},
}
NOW = datetime(2026, 11, 27, 22, 0, tzinfo=timezone.utc)  # 28th 07:00 KST


def s(title, up="2026-11-27T10:00:00+00:00", privacy="private", at=None, tags=None):
    return Short(id=title, title=title, uploaded=datetime.fromisoformat(up), privacy=privacy,
                 publish_at=datetime.fromisoformat(at) if at else None, tags=tags or [])


def kst(t):
    return t.astimezone(KST).strftime("%m-%d %H:%M")


def test_fills_days_in_slots():
    shorts = [s(f"영상{i}") for i in range(4)]
    out = plan(shorts, CFG, NOW)
    assert [kst(t) for _, t in out] == ["11-28 15:00", "11-28 23:00", "11-29 07:00", "11-29 15:00"]


def test_variants_rest_six_days():
    shorts = [s("그네"), s("그네 2"), s("아카시로")]
    out = {x.title: kst(t) for x, t in plan(shorts, CFG, NOW)}
    assert out["그네"].startswith("11-28")
    assert out["그네 2"].startswith("12-04")
    assert out["아카시로"].startswith("11-28")


def test_skips_old_kept_and_scheduled():
    shorts = [
        s("옛날", up="2026-11-20T00:00:00+00:00"),
        s("비밀 #보관"),
        s("이미예약", at="2026-11-28T06:00:00+00:00"),  # 28th 15:00 KST, takes a slot
        s("새것"),
    ]
    out = plan(shorts, CFG, NOW)
    assert [(x.title, kst(t)) for x, t in out] == [("새것", "11-28 23:00")]


def test_first_publish_date_waits():
    cfg = {**CFG, "scheduler": {**CFG["scheduler"], "first_publish_date": "2026-12-26"}}
    out = plan([s("그네")], cfg, NOW)
    assert kst(out[0][1]) == "12-26 07:00"


def test_rest_counts_public_videos_by_tag():
    pub = s("그네 🥚🥔 #shorts", privacy="public", up="2026-11-27T00:00:00+00:00", tags=["egp:그네"])
    out = plan([pub, s("그네 2")], CFG, NOW)
    assert kst(out[0][1]).startswith("12-03")
