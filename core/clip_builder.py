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


def _result_tail(entry: dict) -> str:
    """제목 끝의 결과 표기. 레이팅이 오른 판은 레이팅, 신기록 분석 모드는 신기록 개선폭."""
    if entry.get("current_rating") is not None:
        return f" | {entry['current_rating']} (+{entry['change']})"
    if entry.get("first_play"):
        return " | 첫 기록"
    if entry.get("new_record") and entry.get("record_delta") is not None:
        return f" | 신기록 +{entry['record_delta']:.4f}%"
    return ""


def _result_line(entry: dict) -> str:
    """설명란의 결과 줄(줄바꿈 포함). 알 수 없으면 빈 문자열."""
    if entry.get("current_rating") is not None:
        return (f"레이팅: {entry.get('previous_rating', '?')} → "
                f"{entry['current_rating']} (+{entry['change']})\n")
    if entry.get("first_play") and entry.get("achievement") is not None:
        return f"첫 기록: {entry['achievement']:.4f}% (이 채보를 처음 플레이)\n"
    if entry.get("new_record") and entry.get("record_delta") is not None and entry.get("my_best") is not None:
        ach = entry.get("achievement")
        now = f"{ach:.4f}%" if ach is not None else "?"
        note = "" if entry.get("record_exact") is not False else " ※ 달성률과 MY BEST의 차이로 계산한 값"
        return f"신기록: MY BEST {entry['my_best']:.4f}% → {now} (+{entry['record_delta']:.4f}%){note}\n"
    return ""


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
        tail += _result_tail(entry)
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
            f"\n\n{_date_line(entry)}{_result_line(entry)}"
            f"플레이 시작: {entry.get('play_url', '')}\n"
            f"결과 시점: {entry.get('yt_url', '')}"
        )
    elif entry.get("current_rating") is None:
        # 신기록 분석 모드에서 곡명을 못 읽은 판 — 곡 정보 없이 신기록만 알린다
        delta = entry.get("record_delta")
        if entry.get("first_play"):
            title = f"{_title_tag(entry)} 첫 기록"
        else:
            title = f"{_title_tag(entry)} 신기록" + (f" +{delta:.4f}%" if entry.get("new_record") and delta is not None else "")
        description = (
            f"{_date_line(entry)}{_result_line(entry)}"
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


PENDING_MEMO_NAME = "업로드_대기_목록.txt"

_REASON_TEXT = {
    "quotaExceeded":       "YouTube API 일일 할당량을 초과해서 오늘은 업로드할 수 없습니다.",
    "uploadLimitExceeded": "YouTube 계정의 업로드 한도를 초과했습니다.",
    "authFailed":          "인증 오류입니다. 환경 점검에서 재인증이 필요할 수 있습니다.",
    "uploadFailed":        "업로드에 실패했습니다 (네트워크 등).",
}


def refresh_pending_memo(output_dir: Path, reason_code: Optional[str] = None) -> Optional[Path]:
    """highlights/ 에 남은(업로드 안 된) 클립의 제목·설명을 메모장 파일 하나로 정리한다.

    폴더에 실제로 남은 클립을 기준으로 매번 다시 만들기 때문에, 나중에 업로드해서 지워진
    클립은 목록에서 빠지고 이전 실행에서 남은 클립도 함께 보인다. 남은 클립이 없으면 메모를
    지운다. 메모를 만들지 못해도 클립 작업에는 영향이 없다.
    """
    memo = output_dir / PENDING_MEMO_NAME
    try:
        clips = sorted(
            (f for f in output_dir.glob("*.mp4") if not f.name.startswith("_temp")),
            key=lambda f: f.stat().st_mtime,
        )
        if not clips:
            memo.unlink(missing_ok=True)
            return None

        sep = "=" * 70
        lines = [
            f"업로드 대기 클립 {len(clips)}개",
            "",
            "자동 업로드가 되지 않은 클립입니다. YouTube Studio > 만들기 > 동영상 업로드로",
            "올린 뒤, 아래 제목과 설명을 복사해서 붙여넣으세요.",
            "직접 올린 클립은 mp4 파일을 지우면 다음 실행 때 이 목록에서 빠집니다.",
            f"마지막 갱신: {time.strftime('%Y-%m-%d %H:%M')}",
        ]
        if reason_code:
            lines.append(f"원인: {_REASON_TEXT.get(reason_code, reason_code)}")
        for i, clip in enumerate(clips, 1):
            title, desc = "(제목 정보 없음)", ""
            meta = clip.with_suffix(".json")
            if meta.exists():
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    title, desc = data.get("title") or title, data.get("description") or ""
                except Exception:
                    pass
            lines += ["", sep, f"[{i}/{len(clips)}]  파일: {clip.name}", sep,
                      "", "제목:", title, "", "설명:", desc]
        text = "\n".join(lines) + "\n"
        with open(memo, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text)
        return memo
    except Exception as e:
        print(f"    ⚠️  업로드 대기 목록 작성 실패: {e}")
        return None


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
        if entry.get("current_rating") is not None:
            change = entry.get("change", 0)
            label  = f"{entry['current_rating']} (+{change})"
        else:                                     # 신기록 분석 모드 — 레이팅 대신 개선폭
            delta  = entry.get("record_delta")
            if entry.get("first_play"):
                change, label = "첫 기록", "첫 기록"
            else:
                change = f"{delta:.4f}%" if delta is not None else "-"
                label  = f"신기록 +{change}" if entry.get("new_record") else "신기록 여부 미확인"
        temp_file  = output_dir / f"_temp_{i}.mp4"

        print(f"\n  [{i+1}/{n}] {label} @ {fmt_time(result_ts)}")

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
    memo = refresh_pending_memo(output_dir, getattr(uploader, "last_error", None))
    if pending_uploads:
        print("🚨  업로드 실패 영상 — highlights/ 폴더에서 수동으로 업로드하세요:")
        for name in pending_uploads:
            print(f"    {name}")
    if memo:
        print(f"📝  제목·설명 메모: highlights/{memo.name}")
