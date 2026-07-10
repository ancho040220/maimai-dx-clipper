"""클립 메타데이터 생성 및 편집·업로드."""
import json
import re
import time
from pathlib import Path
from typing import Optional, Tuple

from config.settings import HIGHLIGHT_PRE, HIGHLIGHT_POST, MODE_LABELS
from core.scanner_parallel import fmt_time, yt_timestamp_url
from core.downloader import _ffmpeg_trim
from core.youtube_uploader import YouTubeUploader


def _title_to_filename(title: str) -> str:
    """YouTube 제목 → Windows 파일명 (공백 유지, | → -, 불가 문자만 제거)."""
    s = title.replace('|', '-')
    s = re.sub(r'[\\/:*?"<>]', '', s)
    return s.strip()


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
        title = (
            f"[maimai DX] {entry['song_title']} "
            f"{entry.get('difficulty', '')} Lv.{const_str} {ach_str} {entry.get('rank', '')}"
        )
        if badge_str:
            title += f" {badge_str}"
        title += f" | {entry['current_rating']} (+{entry['change']})"

        desc_ach_str = f"{ach:.4f}%" if ach is not None else "-"
        description = (
            f"곡명: {entry['song_title']}\n"
            f"난이도: {entry.get('difficulty', '')} (Lv.{const_str})\n"
            f"달성률: {desc_ach_str}\n"
            f"랭크: {entry.get('rank', '')}"
        )
        if badge_str:
            description += f"\n판정: {badge_str}"
        description += (
            f"\n\n레이팅: {entry.get('previous_rating', '?')} → "
            f"{entry['current_rating']} (+{entry['change']})\n"
            f"플레이 시작: {entry.get('play_url', '')}\n"
            f"결과 시점: {entry.get('yt_url', '')}"
        )
    else:
        mode_label = MODE_LABELS.get(entry.get("mode", ""), "미확인")
        title = f"[maimai DX] Rating Up! {entry['current_rating']} (+{entry['change']})"
        description = (
            f"레이팅 상승: {entry.get('previous_rating', '?')} → "
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
) -> None:
    """각 항목별 클립 커팅 → 업로드. OCR 결과는 history 항목에 이미 적용되어 있어야 함."""
    n = len(history)
    pending_uploads: list[str] = []

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

        if local_play_ts is not None:
            actual_play_ts          = local_play_ts + start_dl
            entry["play_timestamp"] = actual_play_ts
            entry["play_url"]       = yt_timestamp_url(url, actual_play_ts)
            entry["mode"]           = mode
            print(f"    ✓ {mode_label} 시작: {fmt_time(actual_play_ts)}")
            det_id = entry.get("_detection_id", f"result_{i+1}")
            print(f"[DETECT_UPD] {json.dumps({'id': det_id, 'play_t': fmt_time(actual_play_ts), 'mode': mode, 'song_title': entry.get('song_title'), 'difficulty': entry.get('difficulty'), 'achievement': entry.get('achievement'), 'rank': entry.get('rank'), 'internal_level': entry.get('internal_level')}, ensure_ascii=False)}")
        else:
            local_play_ts = max(0.0, local_result_ts - 180)
            print("    ⚠️  시작 화면 미발견 — 3분 전으로 대체")

        clip_start = max(0.0, local_play_ts - HIGHLIGHT_PRE)
        clip_end   = local_result_ts + HIGHLIGHT_POST
        ts_tag     = time.strftime("%Y%m%d_%H%M%S")
        title, desc = build_clip_metadata(entry)
        out_file   = output_dir / f"{_title_to_filename(title)}_{ts_tag}.mp4"

        if _ffmpeg_trim(temp_file, clip_start, clip_end, out_file):
            print(f"    💾  {out_file.name}")
            size_mb = round(out_file.stat().st_size / 1024 / 1024, 1) if out_file.exists() else 0
            dur_s   = int(clip_end - clip_start)
            dur_fmt = f"{dur_s // 60}:{dur_s % 60:02d}"
            print(f"[HL_ADD] {json.dumps({'file': out_file.name, 't': fmt_time(result_ts), 'mode': mode, 'delta': change, 'size': f'{size_mb} MB', 'duration': dur_fmt, 'status': 'queued'}, ensure_ascii=False)}")
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

    if pending_uploads:
        print("\n🚨  업로드 실패 영상 — highlights/ 폴더에서 수동으로 업로드하세요:")
        for name in pending_uploads:
            print(f"    {name}")
