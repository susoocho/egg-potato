"""Schedule Shorts that were uploaded from the phone as private.

Workflow: on the phone, pick a video in the YouTube app, add a trending sound,
type a short title such as "그네" (or "그네 2" for a variant), and upload it as
PRIVATE. This script then finds those private, unscheduled Shorts and:

  * rewrites the title with a template and adds description and tags,
  * gives each one a publish time, filling up to `per_day` slots a day,
  * keeps variants of the same original at least `min_days_between_variants`
    days apart.

Only private videos uploaded on or after `scheduler.since` are touched, and any
video whose title contains `#보관` is left alone, so older private videos stay
private.

Environment: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, DRY_RUN=1.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from upload import Video, env, load_config, make_title

TAG_PREFIX = "egp:"  # hidden tag that remembers which original a video came from
SKIP_MARK = "#보관"


@dataclass
class Short:
    id: str
    title: str
    uploaded: datetime
    privacy: str
    publish_at: datetime | None
    tags: list[str] = field(default_factory=list)
    published: datetime | None = None  # when it actually went public, if it has

    @property
    def base(self) -> str:
        for t in self.tags:
            if t.startswith(TAG_PREFIX):
                return t[len(TAG_PREFIX):]
        return Video("", self.title + ".mp4", "").base

    @property
    def variant(self) -> int:
        return Video("", self.title + ".mp4", "").variant

    @property
    def go_live(self) -> datetime | None:
        """When this video is or will be public."""
        if self.publish_at:
            return self.publish_at
        if self.privacy == "public":
            return self.published or self.uploaded
        return None


def day_slots(day: date, tz: ZoneInfo, start: str, end: str, n: int) -> list[datetime]:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    a = datetime.combine(day, time(sh, sm), tz)
    b = datetime.combine(day, time(eh, em), tz)
    if n <= 1:
        return [a]
    step = (b - a) / (n - 1)
    return [(a + step * i).replace(second=0, microsecond=0) for i in range(n)]


def is_pending(s: Short, since: datetime) -> bool:
    return (s.privacy == "private" and s.publish_at is None
            and s.uploaded >= since and SKIP_MARK not in s.title)


def plan(shorts: list[Short], cfg: dict, now: datetime) -> list[tuple[Short, datetime]]:
    """Pick a publish time for every pending short. Pure function, easy to test."""
    tz = ZoneInfo(cfg.get("timezone", "Asia/Seoul"))
    sc = cfg.get("scheduler", {})
    per_day = int(cfg["per_day"])
    min_days = int(cfg.get("min_days_between_variants", 0))
    since = datetime.fromisoformat(sc.get("since", "2000-01-01")).replace(tzinfo=tz)
    horizon = int(sc.get("max_days_ahead", 60))
    earliest = now + timedelta(minutes=30)
    first = sc.get("first_publish_date") or ""
    if first:
        earliest = max(earliest, datetime.combine(date.fromisoformat(first), time(0, 0), tz))

    # What is already public or booked: slots taken per day, and go-live days per original.
    taken: dict[date, set[datetime]] = {}
    count: dict[date, int] = {}
    live_days: dict[str, list[date]] = {}
    for s in shorts:
        t = s.go_live
        if not t:
            continue
        d = t.astimezone(tz).date()
        live_days.setdefault(s.base, []).append(d)
        if s.publish_at:
            taken.setdefault(d, set()).add(s.publish_at.astimezone(tz).replace(second=0, microsecond=0))
            count[d] = count.get(d, 0) + 1

    def rested(base: str, d: date) -> bool:
        return all(abs((d - x).days) >= min_days for x in live_days.get(base, []))

    pending = sorted((s for s in shorts if is_pending(s, since)),
                     key=lambda s: (s.uploaded, s.variant, s.title))
    out: list[tuple[Short, datetime]] = []
    start_day = earliest.astimezone(tz).date()
    for s in pending:
        for offset in range(horizon):
            d = start_day + timedelta(days=offset)
            if count.get(d, 0) >= per_day or not rested(s.base, d):
                continue
            free = [t for t in day_slots(d, tz, cfg["publish_start"], cfg["publish_end"], per_day)
                    if t >= earliest and t not in taken.get(d, set())]
            if not free:
                continue
            slot = free[0]
            taken.setdefault(d, set()).add(slot)
            count[d] = count.get(d, 0) + 1
            live_days.setdefault(s.base, []).append(d)
            out.append((s, slot))
            break
    return out


# ---------------------------------------------------------------- YouTube API

def parse_time(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def fetch_recent(youtube, limit: int) -> tuple[list[Short], dict]:
    """Recent uploads of the signed-in channel, plus raw resources by id."""
    ch = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while len(ids) < limit:
        res = youtube.playlistItems().list(part="contentDetails", playlistId=uploads,
                                           maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in res.get("items", [])]
        token = res.get("nextPageToken")
        if not token:
            break
    shorts, raw = [], {}
    for i in range(0, len(ids[:limit]), 50):
        res = youtube.videos().list(part="snippet,status", id=",".join(ids[i:i + 50])).execute()
        for v in res.get("items", []):
            sn, st = v["snippet"], v["status"]
            raw[v["id"]] = v
            shorts.append(Short(
                id=v["id"], title=sn.get("title", ""), uploaded=parse_time(sn["publishedAt"]),
                privacy=st.get("privacyStatus", ""), publish_at=parse_time(st.get("publishAt")),
                tags=sn.get("tags", []) or [],
            ))
    return shorts, raw


def apply(youtube, raw: dict, s: Short, when: datetime, cfg: dict) -> None:
    sn = dict(raw["snippet"])
    st = dict(raw["status"])
    base = s.base
    sn["title"] = make_title(Video("", s.title + ".mp4", ""), cfg["title_templates"])
    sn["description"] = cfg.get("description", "")
    sn["tags"] = list(dict.fromkeys(cfg.get("tags", []) + [TAG_PREFIX + base]))
    sn.setdefault("categoryId", cfg.get("category_id", "22"))
    for k in ("thumbnails", "channelTitle", "channelId", "publishedAt", "localized", "liveBroadcastContent"):
        sn.pop(k, None)
    st["privacyStatus"] = "private"
    st["publishAt"] = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for k in ("uploadStatus", "failureReason", "rejectionReason", "madeForKids"):
        st.pop(k, None)
    youtube.videos().update(part="snippet,status",
                            body={"id": s.id, "snippet": sn, "status": st}).execute()


def main() -> int:
    from upload import google_services

    cfg = load_config()
    sc = cfg.get("scheduler", {})
    dry = env("DRY_RUN").lower() in {"1", "true", "yes"}
    if not env("YT_REFRESH_TOKEN"):
        print("아직 연결 설정이 안 됐어요. uploader/README.md 를 봐주세요.")
        return 0
    tz = ZoneInfo(cfg.get("timezone", "Asia/Seoul"))
    _, youtube = google_services()
    try:
        shorts, raw = fetch_recent(youtube, int(sc.get("look_back", 500)))
    except Exception as e:
        if "insufficient" in str(e).lower() or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in str(e):
            print("유튜브 '계정 관리' 권한이 아직 없어요. README의 권한 다시 받기 순서를 따라주세요.")
            return 0
        raise
    since = datetime.fromisoformat(sc.get("since", "2000-01-01")).replace(tzinfo=tz)
    pending = [s for s in shorts if is_pending(s, since)]
    todo = plan(shorts, cfg, datetime.now(timezone.utc))[: int(sc.get("max_per_run", 150))]

    print(f"예약 기다리는 비공개 쇼츠 {len(pending)}개, 이번에 예약할 영상 {len(todo)}개"
          f"{' (연습 모드, 실제로 바꾸지 않음)' if dry else ''}")
    if len(todo) < len(pending):
        print(f"주의: {len(pending) - len(todo)}개는 이번에 자리를 못 잡았어요. 다음 실행 때 다시 시도해요.")
    for s, t in todo:
        print(f"  {t.astimezone(tz).strftime('%m-%d %H:%M')}  {s.title}  →  "
              f"{make_title(Video('', s.title + '.mp4', ''), cfg['title_templates'])}")
    if dry:
        return 0

    failures = 0
    for s, t in todo:
        try:
            apply(youtube, raw[s.id], s, t, cfg)
        except Exception as e:
            failures += 1
            print(f"실패: {s.title}: {e}", file=sys.stderr)
            if "quotaExceeded" in str(e):
                print("오늘 유튜브 한도에 걸려서 멈춰요. 남은 영상은 다음 실행 때 예약돼요.", file=sys.stderr)
                break
    print(f"완료: {len(todo) - failures}개 예약, {failures}개 실패")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
