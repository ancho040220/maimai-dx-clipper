"""클립 메타데이터 생성 및 편집·업로드."""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from config.settings import HIGHLIGHT_PRE, HIGHLIGHT_POST, MODE_LABELS
from core.scanner_parallel import fmt_time, yt_timestamp_url
from core.downloader import _ffmpeg_trim
from core.youtube_uploader import YouTubeUploader


YT_TITLE_MAX = 100   # YouTube 제목 한도


def _title_tag(entry: dict) -> str:
    """제목 머리말. 플레이 날짜를 알면 [maimai DX 2026-09-30]."""
    dt = play_datetime(entry)
    return f"[maimai DX {dt.strftime('%Y-%m-%d')}]" if dt else "[maimai DX]"


def _fit_title(head: str, song: str, tail: str) -> str:
    """한도를 넘으면 곡명만 …로 줄인다 (난이도·달성률·레이팅은 유지). 곡명 전체는 설명란에 있다."""
    if len(head) + len(song) + len(tail) <= YT_TITLE_MAX:
        return head + song + tail
    room = max(YT_TITLE_MAX - len(head) - len(tail) - 1, 1)
    return (head + song[:room].rstrip() + "…" + tail)[:YT_TITLE_MAX]


def _title_to_filename(title: str) -> str:
    """YouTube 제목 → Windows 파일명 (공백 유지, | → -, 불가 문자만 제거)."""
    s = title.replace('|', '-')
    s = re.sub(r'[\\/:*?"<>]', '', s)
    return s.strip()


def play_datetime(entry: dict) -> Optional[datetime]:
    """클립의 플레이 일시(로컬 시간). 알 수 없으면 None.

    라이브는 녹화 시각(played_at)을, VOD는 방송 시작 시각 + 영상 내 위치를 쓴다.
    """
    at = entry.get("played_at")
    if at is None and entry.get("stream_start") is not None:
        pos = entry.get("play_timestamp", entry.get("timestamp"))
        if pos is not None:
            at = entry["stream_start"] + pos
    return datetime.fromtimestamp(at) if at is not None else None


def _type_label(entry: dict) -> str:
    """설명란용 채보 종류. 표준/DX를 모르면 비운다."""
    return {"std": " 스탠다드", "dx": " DX"}.get(entry.get("chart_type"), "")


def _date_line(entry: dict) -> str:
    """설명란용 날짜 줄. 시각이 정확하지 않으면 날짜만."""
    dt = play_datetime(entry)
    if dt is None:
        return ""
    fmt = "%Y-%m-%d %H:%M" if entry.get("played_precise", True) else "%Y-%m-%d"
    return f"플레이 일시: {dt.strftime(fmt)}\n"


def clip_filename(entry: dict, title: str, index: int) -> str:
    """파일명: 플레이 날짜가 앞에 붙는다(정렬이 곧 시간순). 날짜를 모르면 기존처럼 뒤에 현재 시각."""
    dt = play_datetime(entry)
    base = _title_to_filename(re.sub(r"\[maimai DX \d{4}-\d{2}-\d{2}\]", "[maimai DX]", title))
    if dt is None:
        return f"{base}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    if entry.get("played_precise", True):
        return f"{dt.strftime('%Y%m%d_%H%M')}_{base}.mp4"
    return f"{dt.strftime('%Y%m%d')}_{index + 1:02d}_{base}.mp4"


def build_clip_metadata(entry: dict) -> Tuple[str, str]:
    """history entry로 YouTube 업로드용 title, description 반환."""
    if entry.get("song_title"):
        chart_const = entry.get("internal_level")
        const_str = f"{chart_const:.1f}" if chart_const is not None else "?"
        fc_badge  = entry.get("fc_type", "")
        dx_badge  = entry.get("dx_type", "")
        badge_str = " ".join(filter(None, [fc_badge, dx_badge]))

        ach = entry.get("achievement")
        ach_str = f"{ach:.4f}%" if ach is not None else ""
        tail = f" {entry.get('difficulty', '')} Lv.{const_str} {ach_str} {entry.get('rank', '')}"
        if badge_str:
            tail += f" {badge_str}"
        tail += f" | {entry['current_rating']} (+{entry['change']})"
        title = _fit_title(f"{_title_tag(entry)} ", entry["song_title"], tail)

        desc_ach_str = f"{ach:.4f}%" if ach is not None else "-"
        description = (
            f"곡명: {entry['song_title']}\n"
            f"난이도: {entry.get('difficulty', '')}{_type_label(entry)} (Lv.{const_str})\n"
            f"달성률: {desc_ach_str}\n"
            f"랭크: {entry.get('rank', '')}"
        )
        if badge_str:
            description += f"\n판정: {badge_str}"
        description += (
            f"\n\n{_date_line(entry)}레이팅: {entry.get('previous_rating', '?')} → "
            f"{entry['current_rating']} (+{entry['change']})\n"
            f"플레이 시작: {entry.get('play_url', '')}\n"
            f"결과 시점: {entry.get('yt_url', '')}"
        )
    else:
        mode_label = MODE_LABELS.get(entry.get("mode", ""), "미확인")
        title = f"{_title_tag(entry)} Rating Up! {entry['current_rating']} (+{entry['change']})"
        description = (
            f"{_date_line(entry)}레이팅 상승: {entry.get('previous_rating', '?')} → "
            f"{entry['current_rating']} (+{entry['change']})\n"
            f"모드: {mode_label}\n"
            f"플레이 시작: {entry.get('play_url', '')}\n"
            f"결과 시점: {entry.get('yt_url', '')}"
        )

    return title, description


def _save_clip_meta(out_file: Path, title: str, description: str) -> None:
    """클립 메타데이터를 mp4와 같은 경로에 JSON으로 저장."""
    meta_path = out_file.with_suffix(".json")
    try:
        meta_path.write_text(
            json.dumps({"title": title, "description": description}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"    ⚠️  메타데이터 저장 실패 (재업로드 시 제목 손실): {e}")


def _cut_and_upload_clips(
    history: list,
    url: str,
    output_dir: Path,
    uploader: Optional[YouTubeUploader],
    start_dl_map: dict,
    lookback_map: dict,
    cancel_event=None,
    stream_start: Optional[float] = None,
    stream_start_precise: bool = True,
) -> None:
    """각 항목별 클립 커팅 → 업로드. OCR 결과는 history 항목에 이미 적용되어 있어야 함."""
    n = len(history)
    pending_uploads: list[str] = []
    n_cut       = 0   # 커팅 성공
    n_estimated = 0   # 역추적 실패로 시작 지점을 추정(3분 전)한 클립

    for i, entry in enumerate(history):
        if cancel_event is not None and cancel_event.is_set():
            print("  🛑  중단 요청 — 남은 클립 커팅/업로드를 중단합니다.")
            break
        result_ts  = entry["timestamp"]
        change     = entry.get("change", 0)
        new_rating = entry["current_rating"]
        temp_file  = output_dir / f"_temp_{i}.mp4"

        print(f"\n  [{i+1}/{n}] {new_rating} (+{change}) @ {fmt_time(result_ts)}")

        start_dl = start_dl_map.get(i)
        if start_dl is None or not temp_file.exists():
            print("    ⚠️  다운로드 실패 항목 — 건너뜀")
            continue

        local_result_ts     = result_ts - start_dl
        local_play_ts, mode = lookback_map.get(i, (None, None))
        mode_label          = MODE_LABELS.get(mode, "미확인")
        det_id              = entry.get("_detection_id", f"result_{i+1}")

        if local_play_ts is not None:
            actual_play_ts          = local_play_ts + start_dl
            entry["play_timestamp"] = actual_play_ts
            entry["play_url"]       = yt_timestamp_url(url, actual_play_ts)
            entry["mode"]           = mode
            print(f"    ✓ {mode_label} 시작: {fmt_time(actual_play_ts)}")
            print(f"[DETECT_UPD] {json.dumps({'id': det_id, 'play_t': fmt_time(actual_play_ts), 'mode': mode, 'song_title': entry.get('song_title'), 'difficulty': entry.get('difficulty'), 'achievement': entry.get('achievement'), 'rank': entry.get('rank'), 'internal_level': entry.get('internal_level')}, ensure_ascii=False)}")
        else:
            local_play_ts = max(0.0, local_result_ts - 180)
            n_estimated += 1
            print("    ⚠️  시작 화면 미발견 — 3분 전으로 추정 (부정확할 수 있음)")

        clip_start = max(0.0, local_play_ts - HIGHLIGHT_PRE)
        clip_end   = local_result_ts + HIGHLIGHT_POST
        if stream_start is not None:
            entry["stream_start"]   = stream_start
            entry["played_precise"] = stream_start_precise
        title, desc = build_clip_metadata(entry)
        out_file   = output_dir / clip_filename(entry, title, i)

        if _ffmpeg_trim(temp_file, clip_start, clip_end, out_file):
            n_cut += 1
            print(f"    💾  {out_file.name}")
            size_mb = round(out_file.stat().st_size / 1024 / 1024, 1) if out_file.exists() else 0
            dur_s   = int(clip_end - clip_start)
            dur_fmt = f"{dur_s // 60}:{dur_s % 60:02d}"
            print(f"[HL_ADD] {json.dumps({'id': det_id, 'file': out_file.name, 't': fmt_time(result_ts), 'mode': mode, 'delta': change, 'size': f'{size_mb} MB', 'duration': dur_fmt, 'status': 'queued'}, ensure_ascii=False)}")
        else:
            print("    ⚠️  영상 커팅에 실패했습니다. ffmpeg 설치 상태를 확인하세요.")
            temp_file.unlink(missing_ok=True)
            continue

        temp_file.unlink(missing_ok=True)
        _save_clip_meta(out_file, title, desc)

        if uploader:
            video_id = uploader.upload(out_file, title, desc)
            if video_id:
                try:
                    out_file.unlink(missing_ok=True)
                    out_file.with_suffix(".json").unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                pending_uploads.append(out_file.name)

    # 처리 요약 — 전부 추정 시작 지점이거나 업로드 실패가 있으면 명시적으로 알림 (B-8)
    print(f"\n📋  클립 처리 요약: 커팅 {n_cut}/{n} · 시작지점 추정 {n_estimated} · 업로드 실패 {len(pending_uploads)}")
    if n_cut > 0 and n_estimated == n_cut:
        print("🚨  모든 클립이 시작 지점 '추정'으로 잘렸습니다 — 역추적이 전부 실패했을 수 있습니다. 클립 구간을 확인하세요.")
    if pending_uploads:
        print("🚨  업로드 실패 영상 — highlights/ 폴더에서 수동으로 업로드하세요:")
        for name in pending_uploads:
            print(f"    {name}")
