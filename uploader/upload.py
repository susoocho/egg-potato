"""Daily Shorts upload robot.

Reads videos from a Google Drive folder, uploads today's batch to YouTube as
scheduled (private + publishAt) videos spread across the day, then moves each
uploaded file into a "done" subfolder so it is never uploaded twice.

File naming: "<original name>_<variant number>.mp4", e.g. "아카시로_1.mp4",
"아카시로_2.mp4". Variants of the same original are spread over different days
(at most `max_per_original_per_day` per day).

Environment:
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN  OAuth credentials
  DRIVE_FOLDER_ID                                   the queue folder
  DRY_RUN=1                                         only print the plan
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
# "아카시로_2", "그네 2", "그네2", "Color trend v2". The base must end in a non-digit
# so "babe 2026" stays one name.
VARIANT_RE = re.compile(r"^(?P<base>.*?\D)[ _\-]*(?:v)?(?P<num>\d{1,3})$", re.IGNORECASE)
# Scopes the refresh token must have been granted (see README). The token
# carries its own scopes, so they are not re-requested on refresh.
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/drive",
]


@dataclass
class Video:
    id: str
    name: str
    created: str  # RFC 3339 from Drive

    @property
    def stem(self) -> str:
        return Path(self.name).stem.strip()

    @property
    def base(self) -> str:
        m = VARIANT_RE.match(self.stem)
        return (m.group("base") if m else self.stem).strip()

    @property
    def variant(self) -> int:
        m = VARIANT_RE.match(self.stem)
        return int(m.group("num")) if m else 1

    @property
    def display_name(self) -> str:
        return re.sub(r"[_]+", " ", self.base).strip()


def load_config() -> dict:
    with open(Path(__file__).with_name("config.json"), encoding="utf-8") as f:
        return json.load(f)


def pick_today(videos: list[Video], per_day: int, max_per_original: int,
               resting: set[str] | None = None) -> list[Video]:
    """Oldest originals first; round-robin so variants of one original spread out.

    `resting` holds originals that went up too recently; their variants wait."""
    resting = resting or set()
    groups: dict[str, list[Video]] = {}
    for v in sorted(videos, key=lambda v: (v.created, v.variant, v.name)):
        if v.base in resting:
            continue
        groups.setdefault(v.base, []).append(v)
    for g in groups.values():
        g.sort(key=lambda v: (v.variant, v.name))
    order = sorted(groups, key=lambda b: groups[b][0].created)

    picked: list[Video] = []
    for round_ in range(max_per_original):
        for base in order:
            if len(picked) >= per_day:
                return picked
            if round_ < len(groups[base]):
                picked.append(groups[base][round_])
    return picked


def resting_originals(uploaded: list[tuple[str, str]], today: date, min_days: int) -> set[str]:
    """Originals (by base name) uploaded fewer than `min_days` days before `today`.

    `uploaded` is (file name, upload date as YYYY-MM-DD)."""
    if min_days <= 0:
        return set()
    out = set()
    for name, day in uploaded:
        try:
            d = date.fromisoformat(day[:10])
        except ValueError:
            continue
        if (today - d).days < min_days:
            out.add(Video("", name, "").base)
    return out


def publish_times(count: int, now: datetime, tz: ZoneInfo, start: str, end: str) -> list[datetime]:
    """Evenly spaced publish times inside today's window (local time), all in the future."""
    if count == 0:
        return []
    local_now = now.astimezone(tz)
    day = local_now.date()
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    win_start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
    win_end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
    earliest = local_now + timedelta(minutes=45)  # leave time for uploads to finish
    if win_start < earliest:
        win_start = earliest
    if win_end <= win_start:  # window already passed: use tomorrow's window
        win_start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz) + timedelta(days=1)
        win_end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz) + timedelta(days=1)
    if count == 1:
        return [win_start]
    step = (win_end - win_start) / (count - 1)
    return [(win_start + step * i).replace(second=0, microsecond=0) for i in range(count)]


def make_title(v: Video, templates: list[str]) -> str:
    t = templates[(v.variant - 1) % len(templates)] if templates else "{name} #shorts"
    return t.format(name=v.display_name)[:100]


# ---------------------------------------------------------------- Google APIs

def env(name: str) -> str:
    """Secret values pasted into GitHub often carry a stray space or newline."""
    return os.environ.get(name, "").strip()


def google_services():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=env("YT_REFRESH_TOKEN"),
        client_id=env("YT_CLIENT_ID"),
        client_secret=env("YT_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return drive, youtube


def list_queue(drive, folder_id: str) -> list[Video]:
    out, token = [], None
    q = f"'{folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    while True:
        res = drive.files().list(
            q=q, fields="nextPageToken, files(id, name, createdTime)", pageSize=1000,
            pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        for f in res.get("files", []):
            if Path(f["name"]).suffix.lower() in VIDEO_EXT:
                out.append(Video(f["id"], f["name"], f["createdTime"]))
        token = res.get("nextPageToken")
        if not token:
            return out


def list_uploaded(drive, done_id: str, tz: ZoneInfo) -> list[tuple[str, str]]:
    """(name, local upload date) for files already moved to the done folder."""
    out, token = [], None
    q = f"'{done_id}' in parents and trashed = false"
    while True:
        res = drive.files().list(
            q=q, fields="nextPageToken, files(name, modifiedTime, appProperties)", pageSize=1000,
            pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        for f in res.get("files", []):
            day = (f.get("appProperties") or {}).get("uploaded")
            if not day:
                day = datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")).astimezone(tz).date().isoformat()
            out.append((f["name"], day))
        token = res.get("nextPageToken")
        if not token:
            return out


def done_folder(drive, parent_id: str, name: str) -> str:
    safe = name.replace("'", "\\'")
    q = (f"'{parent_id}' in parents and trashed = false and name = '{safe}' "
         "and mimeType = 'application/vnd.google-apps.folder'")
    res = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute()
    if res.get("files"):
        return res["files"][0]["id"]
    f = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ).execute()
    return f["id"]


def download(drive, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()


def upload(youtube, path: Path, title: str, cfg: dict, publish_at: datetime) -> str:
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": title,
            "description": cfg.get("description", ""),
            "tags": cfg.get("tags", []),
            "categoryId": cfg.get("category_id", "22"),
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(path), chunksize=16 * 1024 * 1024, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def move(drive, file_id: str, from_id: str, to_id: str, video_id: str, day: str) -> None:
    drive.files().update(
        fileId=file_id, addParents=to_id, removeParents=from_id,
        body={"description": f"https://youtube.com/shorts/{video_id}",
              "appProperties": {"uploaded": day}},
        supportsAllDrives=True,
    ).execute()


def main() -> int:
    cfg = load_config()
    dry = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    folder_id = env("DRIVE_FOLDER_ID")
    if not (folder_id and env("YT_REFRESH_TOKEN")):
        print("아직 연결 설정이 안 됐어요. uploader/README.md 의 처음 연결 순서를 따라주세요.")
        return 0
    tz = ZoneInfo(cfg.get("timezone", "Asia/Seoul"))

    drive, youtube = google_services()
    queue = list_queue(drive, folder_id)
    done_id = done_folder(drive, folder_id, cfg.get("done_folder_name", "올린 영상"))
    local_today = datetime.now(tz).date()
    min_days = int(cfg.get("min_days_between_variants", 0))
    resting = resting_originals(list_uploaded(drive, done_id, tz), local_today, min_days)
    today = pick_today(queue, int(cfg["per_day"]), int(cfg["max_per_original_per_day"]), resting)
    times = publish_times(len(today), datetime.now(timezone.utc), tz,
                          cfg["publish_start"], cfg["publish_end"])

    print(f"대기 중인 영상 {len(queue)}개, 오늘 올릴 영상 {len(today)}개"
          f"{' (연습 모드, 실제로 올리지 않음)' if dry else ''}")
    if resting:
        print(f"최근 {min_days}일 안에 올라간 원본 {len(resting)}개는 쉬는 중이라 이번엔 건너뛰어요.")
    if len(today) < int(cfg["per_day"]):
        print(f"주의: 오늘 목표 {cfg['per_day']}개보다 {int(cfg['per_day']) - len(today)}개 부족해요. "
              "드라이브 폴더에 영상을 더 넣어주세요.")
    for v, t in zip(today, times):
        print(f"  {t.strftime('%m-%d %H:%M')}  {make_title(v, cfg['title_templates'])}   ← {v.name}")
    if dry or not today:
        return 0

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for v, t in zip(today, times):
            path = Path(tmp) / f"{v.id}{Path(v.name).suffix.lower()}"
            try:
                download(drive, v.id, path)
                vid = upload(youtube, path, make_title(v, cfg["title_templates"]), cfg, t)
                move(drive, v.id, folder_id, done_id, vid, t.astimezone(tz).date().isoformat())
                print(f"올림: {v.name} → https://youtube.com/shorts/{vid} ({t.strftime('%H:%M')} 공개)")
            except Exception as e:  # keep going with the rest of the batch
                failures += 1
                print(f"실패: {v.name}: {e}", file=sys.stderr)
                if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
                    print("오늘 업로드 한도에 걸려서 멈춰요. 남은 영상은 내일 올라가요.", file=sys.stderr)
                    break
            finally:
                path.unlink(missing_ok=True)
    print(f"완료: {len(today) - failures}개 성공, {failures}개 실패")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
