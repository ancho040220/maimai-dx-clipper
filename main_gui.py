#!/usr/bin/env python3
"""
maimai DX Live & Auto-Upload Pipeline — 진입점

사용법:
    python main_gui.py
    python main_gui.py --url <YouTube URL>
    python main_gui.py --url <YouTube URL> --no-upload
    python main_gui.py --url <YouTube URL> --start 10:00 --end 2:00:00
"""

import argparse
import sys
import time
from pathlib import Path

from config.settings import PROJECT_DIR, RATING_MIN, RATING_MAX
from core.pipeline import check_video_status, analyze_vod_stream, process_vod_entries
from core.live_monitor import LiveMonitor
from core.scanner_parallel import fmt_time, parse_time
from core.youtube_uploader import YouTubeUploader


def _ask_time(label: str, default=None):
    hint = " (예: 1:10:00, 10:30"
    if default is not None:
        hint += f", 엔터 시 {fmt_time(default)}"
    hint += ", 엔터로 생략 가능)" if default is None else ")"
    raw = input(f"{label}{hint}: ").strip()
    if raw == "":
        return default
    try:
        return parse_time(raw)
    except argparse.ArgumentTypeError as e:
        print(f"  ⚠️  {e} — 생략합니다.")
        return default


def _prompt_rating(prompt: str):
    raw = input(prompt).strip()
    if raw.isdigit() and RATING_MIN <= int(raw) <= RATING_MAX:
        return int(raw)
    return None


def main():
    parser = argparse.ArgumentParser(description="maimai DX Live & Auto-Upload Pipeline")
    parser.add_argument("--url",       default=None)
    parser.add_argument("--output",    default="highlights")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--buffer",    type=int, default=10)
    parser.add_argument("--start",     type=parse_time, default=None, metavar="TIME")
    parser.add_argument("--end",       type=parse_time, default=None, metavar="TIME")
    parser.add_argument("--workers",   type=int, default=0)
    parser.add_argument("--result",    metavar="FILE", default=None)
    args = parser.parse_args()

    print("=" * 60)
    print("  maimai DX Live & Auto-Upload Pipeline")
    print("=" * 60)
    print()
    _t_start = time.time()

    # URL 입력
    from config.settings import CACHE_DIR
    CACHE_DIR.mkdir(exist_ok=True)
    url_record  = CACHE_DIR / "mai_youtube_url.txt"
    saved_url   = url_record.read_text(encoding="utf-8").strip() if url_record.exists() else ""
    default_url = args.url or saved_url

    hint      = f" (엔터 시 마지막 사용: {default_url})" if default_url else ""
    url_input = input(f"YouTube URL{hint}: ").strip()
    video_url = url_input if url_input else default_url

    if not video_url or not video_url.startswith("http"):
        print("❌  올바른 YouTube URL이 아닙니다.")
        sys.exit(1)

    url_record.write_text(video_url, encoding="utf-8")

    # 라이브 / VOD 판별
    print("\n📡  URL 상태 확인 중...")
    status = check_video_status(video_url)
    print(f"    제목:  {status['title']}")
    print(f"    채널:  {status['channel']}")
    print(f"    상태:  {'🔴 라이브' if status['is_live'] else '📼 VOD'}\n")

    output_dir = Path(args.output)

    # YouTube 업로드 인증
    uploader = None
    if not args.no_upload:
        uploader = YouTubeUploader()
        try:
            uploader.authenticate()
            print("🔑  YouTube 인증 완료\n")
        except (FileNotFoundError, RuntimeError) as e:
            print(f"⚠️  YouTube 인증 실패 (업로드 생략):\n    {e}\n")
            uploader = None

    # 시작 레이팅
    rating = _prompt_rating(f"시작 레이팅을 입력하세요 ({RATING_MIN}~{RATING_MAX}): ")
    if rating is None:
        print("❌  시작 레이팅을 입력해야 합니다.")
        sys.exit(1)

    if status["is_live"]:
        print()
        print(f"  URL:    {video_url}")
        print(f"  출력:   {output_dir}/")
        print(f"  버퍼:   {args.buffer}분")
        print("=" * 60 + "\n")

        monitor = LiveMonitor(
            url=video_url,
            output_dir=output_dir,
            uploader=uploader,
            buffer_minutes=args.buffer,
            initial_rating=rating,
        )
        monitor.start()

    else:
        start_sec = _ask_time("시작 시간", default=args.start)
        end_sec   = _ask_time("종료 시간", default=args.end)

        print()
        print(f"  URL:    {video_url}")
        print(f"  구간:   {fmt_time(start_sec or 0.0)} ~ {fmt_time(end_sec) if end_sec else '끝'}")
        print(f"  출력:   {args.result or '저장 안 함'}")
        print("=" * 60 + "\n")

        history = analyze_vod_stream(
            url=video_url,
            start_sec=start_sec or 0.0,
            end_sec=end_sec,
            initial_rating=rating,
            num_workers=args.workers,
            output_file=args.result,
        )

        if history:
            print("\n🔎  역추적 및 업로드 중...\n")
            process_vod_entries(
                history=history,
                url=video_url,
                output_dir=output_dir,
                uploader=uploader,
            )

        elapsed = int(time.time() - _t_start)
        h, m, s = elapsed // 3600, elapsed % 3600 // 60, elapsed % 60
        print(f"\n총 소요 시간: {h:02d}:{m:02d}:{s:02d}")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        from ui.web_window import launch_gui
        launch_gui()
    else:
        main()
