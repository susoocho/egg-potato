"""Editing robot: clean up raw phone clips before they go to the YouTube app.

For each video in the Drive folder `편집 전` (inside the upload folder):

  * cut shaky, blurry or dark "garbage" at the start and end
    (picking up / putting down the phone),
  * speed up long still stretches (2x by default),
  * make it a 1080x1920 vertical video,
  * save it to `편집 완료` and move the original to `편집 전/처리함`.

The analysis is plain numpy on small grayscale frames, so it is cheap and the
decisions are printed in the log.

Environment: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, DRIVE_FOLDER_ID,
EDIT_SOURCE (optional "folder/name.mp4" to edit one specific file), DRY_RUN=1.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FPS = 10            # analysis frame rate
W, H = 144, 256     # analysis frame size (9:16)
OUT_W, OUT_H = 1080, 1920


@dataclass
class Settings:
    shake_px: float = 3.0       # camera shift between analysis frames that counts as shaking
    blur_ratio: float = 0.35    # sharpness below this share of the median counts as blurry
    dark_level: float = 18.0    # mean brightness (0-255) below this counts as dark
    stable_run: float = 0.6     # seconds of good frames needed to start/end the clip
    max_trim_share: float = 0.3  # never cut more than this share from each end
    still_diff: float = 1.2     # mean pixel change below this counts as "nothing happening"
    still_min: float = 2.0      # still stretches shorter than this (seconds) stay normal speed
    speed: float = 2.0          # speed for still stretches


# ---------------------------------------------------------------- analysis

def _shift(a: np.ndarray, b: np.ndarray) -> float:
    """Pixel shift between two equal-size patches (FFT phase correlation)."""
    r = a * np.conj(b)
    r /= np.abs(r) + 1e-9
    c = np.abs(np.fft.ifft2(r))
    y, x = np.unravel_index(np.argmax(c), c.shape)
    y = y - c.shape[0] if y > c.shape[0] // 2 else y
    x = x - c.shape[1] if x > c.shape[1] // 2 else x
    return float(np.hypot(x, y))


def frame_metrics(frames: np.ndarray) -> dict[str, np.ndarray]:
    """Per-frame shake (camera shift in px), change, sharpness and brightness.

    Shake is the median shift over a 3x3 grid of tiles, so people moving in one
    part of the frame do not count as the camera moving."""
    f = frames.astype(np.float32)
    n, h, w = f.shape
    bright = f.mean(axis=(1, 2))
    lap = (-4 * f[:, 1:-1, 1:-1] + f[:, :-2, 1:-1] + f[:, 2:, 1:-1]
           + f[:, 1:-1, :-2] + f[:, 1:-1, 2:])
    sharp = lap.var(axis=(1, 2))
    th, tw = h // 3, w // 3
    win = np.outer(np.hanning(th), np.hanning(tw))

    def tiles(img):
        out = []
        for r in range(3):
            for c in range(3):
                t = img[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
                out.append(np.fft.fft2((t - t.mean()) * win))
        return out

    shake = np.zeros(n)
    change = np.zeros(n)
    prev = None
    for i in range(n):
        cur = tiles(f[i])
        if prev is not None:
            shake[i] = float(np.median([_shift(p, c) for p, c in zip(prev, cur)]))
            change[i] = float(np.abs(f[i] - f[i - 1]).mean())
        prev = cur
    if n > 1:
        shake[0], change[0] = shake[1], change[1]
    return {"shake": shake, "change": change, "sharp": sharp, "bright": bright}


def find_trim(m: dict[str, np.ndarray], s: Settings) -> tuple[int, int]:
    """First and last good frame (inclusive) after cutting garbage at both ends."""
    n = len(m["shake"])
    sharp_ref = float(np.median(m["sharp"])) or 1.0
    good = ((m["shake"] < s.shake_px) & (m["sharp"] >= s.blur_ratio * sharp_ref)
            & (m["bright"] >= s.dark_level))
    run = max(1, round(s.stable_run * FPS))
    limit = int(n * s.max_trim_share)
    start = 0
    for i in range(0, limit + 1):
        if i + run <= n and good[i:i + run].all():
            start = i
            break
    end = n - 1
    for j in range(n - 1, n - 1 - limit - 1, -1):
        if j - run + 1 >= 0 and good[j - run + 1:j + 1].all():
            end = j
            break
    if end - start < run:
        return 0, n - 1
    return start, end


def find_still(m: dict[str, np.ndarray], start: int, end: int, s: Settings) -> list[tuple[int, int]]:
    """Stretches (inclusive frame ranges) inside [start, end] where little happens."""
    still = m["change"] < s.still_diff
    out, i = [], start
    need = round(s.still_min * FPS)
    while i <= end:
        if still[i]:
            j = i
            while j + 1 <= end and still[j + 1]:
                j += 1
            if j - i + 1 >= need:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def plan_segments(duration: float, start: int, end: int, still: list[tuple[int, int]],
                  s: Settings) -> list[tuple[float, float, float]]:
    """(from seconds, to seconds, speed) pieces that make up the edit."""
    t0 = start / FPS
    t1 = min(duration, (end + 1) / FPS)
    segs, cur = [], t0
    for a, b in still:
        sa, sb = a / FPS, min(t1, (b + 1) / FPS)
        if sa > cur:
            segs.append((cur, sa, 1.0))
        segs.append((sa, sb, s.speed))
        cur = sb
    if cur < t1:
        segs.append((cur, t1, 1.0))
    return [x for x in segs if x[1] - x[0] > 0.05]


# ---------------------------------------------------------------- ffmpeg

def probe(path: Path) -> tuple[float, bool]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    has_audio = any(st.get("codec_type") == "audio" for st in d.get("streams", []))
    return float(d["format"]["duration"]), has_audio


def read_frames(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W)


def atempo(speed: float) -> str:
    parts, sp = [], speed
    while sp > 2.0:
        parts.append("atempo=2.0")
        sp /= 2.0
    parts.append(f"atempo={sp:.4f}")
    return ",".join(parts)


def render(src: Path, dst: Path, segs: list[tuple[float, float, float]], has_audio: bool) -> None:
    fit = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
           f"crop={OUT_W}:{OUT_H},setsar=1")
    parts, labels = [], []
    for k, (a, b, sp) in enumerate(segs):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{sp},{fit}[v{k}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,{atempo(sp)}[a{k}]")
            labels.append(f"[v{k}][a{k}]")
        else:
            labels.append(f"[v{k}]")
    parts.append(f"{''.join(labels)}concat=n={len(segs)}:v=1:a={1 if has_audio else 0}"
                 + ("[v][a]" if has_audio else "[v]"))
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", ";".join(parts),
           "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)


def edit(src: Path, dst: Path, s: Settings | None = None) -> str:
    """Edit one file. Returns a one-line Korean summary of what was done."""
    s = s or Settings()
    duration, has_audio = probe(src)
    m = frame_metrics(read_frames(src))
    start, end = find_trim(m, s)
    still = find_still(m, start, end, s)
    segs = plan_segments(duration, start, end, still, s)
    render(src, dst, segs, has_audio)
    new_len = sum((b - a) / sp for a, b, sp in segs)
    cut_head, cut_tail = start / FPS, max(0.0, duration - (end + 1) / FPS)
    fast = sum(b - a for a, b, sp in segs if sp != 1.0)
    return (f"{duration:.1f}초 → {new_len:.1f}초 (앞 {cut_head:.1f}초, 뒤 {cut_tail:.1f}초 자름, "
            f"{fast:.1f}초 구간 {s.speed:g}배속)")


# ---------------------------------------------------------------- Drive

def find_child(drive, parent: str, name: str, folder: bool) -> str | None:
    safe = name.replace("'", "\\'")
    q = f"'{parent}' in parents and trashed = false and name = '{safe}'"
    q += " and mimeType " + ("=" if folder else "!=") + " 'application/vnd.google-apps.folder'"
    res = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute()
    return res["files"][0]["id"] if res.get("files") else None


def main() -> int:
    from googleapiclient.http import MediaFileUpload

    from upload import VIDEO_EXT, done_folder, download, env, google_services, list_queue

    dry = env("DRY_RUN").lower() in {"1", "true", "yes"}
    root = env("DRIVE_FOLDER_ID")
    if not (root and env("YT_REFRESH_TOKEN")):
        print("아직 연결 설정이 안 됐어요. uploader/README.md 를 봐주세요.")
        return 0
    drive, _ = google_services()
    inbox = done_folder(drive, root, "편집 전")
    outbox = done_folder(drive, root, "편집 완료")
    used = done_folder(drive, inbox, "처리함")

    source = env("EDIT_SOURCE")
    if source:  # "올린 영상/그네.mp4": edit one file and leave the original where it is
        folder, _, name = source.rpartition("/")
        parent = find_child(drive, root, folder, folder=True) if folder else root
        fid = parent and find_child(drive, parent, name, folder=False)
        if not fid:
            print(f"드라이브에서 {source} 를 못 찾았어요.")
            return 1
        from upload import Video
        todo, move_after = [Video(fid, name, "")], False
    else:
        todo, move_after = list_queue(drive, inbox), True
    todo = [v for v in todo if Path(v.name).suffix.lower() in VIDEO_EXT]
    print(f"편집할 영상 {len(todo)}개{' (연습 모드: 결과를 드라이브에 올리지 않음)' if dry else ''}")

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for v in todo:
            src = Path(tmp) / f"in{Path(v.name).suffix.lower()}"
            out_name = f"{Path(v.name).stem}_편집.mp4"
            dst = Path(tmp) / "out.mp4"
            try:
                download(drive, v.id, src)
                print(f"{v.name}: {edit(src, dst)}")
                if not dry:
                    drive.files().create(
                        body={"name": out_name, "parents": [outbox]},
                        media_body=MediaFileUpload(str(dst), mimetype="video/mp4", resumable=True),
                        fields="id", supportsAllDrives=True,
                    ).execute()
                    if move_after:
                        drive.files().update(fileId=v.id, addParents=used, removeParents=inbox,
                                             supportsAllDrives=True).execute()
                    print(f"  → 편집 완료/{out_name}")
            except Exception as e:
                failures += 1
                print(f"실패: {v.name}: {e}", file=sys.stderr)
            finally:
                src.unlink(missing_ok=True)
                dst.unlink(missing_ok=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
