"""VOD 구간 다운로드 및 OCR 프레임 추출."""
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config.settings import (
    ytdlp_cookie_args, NO_WINDOW, OCR_CLIP_PRE, OCR_CLIP_POST,
)
from core.error_messages import translate_error
from core.result_extractor import _sharpness
from core.scanner_parallel import _detect_game_crop, _init_yolo, _lookback, _build_candidates, fmt_time


_MAX_DL_ATTEMPTS = 3    # 다운로드 최대 재시도 횟수
_DL_TIMEOUT      = 300  # 다운로드 타임아웃 (초)
_FFMPEG_TIMEOUT  = 120  # ffmpeg 커팅 타임아웃 (초)


def _download_segment(
    start_sec: float, end_sec: float, output: Path, url: str,
) -> bool:
    """yt-dlp --download-sections로 구간 다운로드."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--download-sections", f"*{int(start_sec)}-{int(end_sec)}",
        "--socket-timeout", "30",
        "-f", "best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-warnings",
        "-N", "4",
        "-o", str(output),
        *ytdlp_cookie_args(),
        url,
    ]

    for attempt in range(1, _MAX_DL_ATTEMPTS + 1):
        t0   = time.time()
        stop = threading.Event()

        def _print_progress(stop=stop, t0=t0):
            while not stop.wait(5):
                print(f"\r  [{output.name}] 다운로드 중... {int(time.time()-t0)}초", end="", flush=True)

        threading.Thread(target=_print_progress, daemon=True).start()

        proc      = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=NO_WINDOW,
        )
        timed_out = False
        try:
            _, stderr_bytes = proc.communicate(timeout=_DL_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr_bytes = proc.communicate()
            timed_out = True

        stop.set()
        print("\r" + " " * 60 + "\r", end="", flush=True)

        stderr = stderr_bytes.decode("utf-8", errors="ignore").strip()

        if "Sign in to confirm" in stderr:
            if stderr:
                print(f"  ⚠️  다운로드 오류 ({output.name}): {translate_error(stderr.splitlines()[-1])}")
            return False

        if timed_out:
            print(f"  타임아웃 ({output.name}): {_DL_TIMEOUT}초 초과 — {'재시도' if attempt < _MAX_DL_ATTEMPTS else '건너뜀'}")
            output.unlink(missing_ok=True)
        elif proc.returncode != 0:
            if stderr:
                print(f"  ⚠️  다운로드 오류 ({output.name}): {translate_error(stderr.splitlines()[-1])}")
            output.unlink(missing_ok=True)
        elif output.exists():
            return True

        if attempt < _MAX_DL_ATTEMPTS:
            delay = 2.0 * (2.0 ** (attempt - 1))
            print(f"  ⚠️  {output.name} 재시도 {attempt}/{_MAX_DL_ATTEMPTS - 1} ({delay:.0f}초 후)...")
            time.sleep(delay)

    return False


def _ffmpeg_trim(src: Path, start: float, end: float, output: Path) -> bool:
    """ffmpeg로 로컬 파일에서 구간 무손실 커팅."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-to", str(end),
        "-i", str(src),
        "-c", "copy",
        str(output),
    ]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT, creationflags=NO_WINDOW).returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⚠️  ffmpeg 타임아웃 ({_FFMPEG_TIMEOUT}s) — {src.name} 커팅 실패")
        output.unlink(missing_ok=True)
        return False


def _segment_lookback_worker(task: tuple) -> tuple:
    """멀티프로세싱 역추적 워커 (spawn 호환, module-level 필수)."""
    import cv2
    from core.scanner_parallel import _init_yolo, _lookback, _build_candidates

    idx, temp_file_str, local_result_ts, max_lookback = task

    yolo       = _init_yolo("cuda")
    candidates = _build_candidates()

    cap = cv2.VideoCapture(temp_file_str)
    if not cap.isOpened():
        return idx, None, None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    local_play_ts, mode = _lookback(cap, fps, local_result_ts, max_lookback, yolo, candidates)
    if local_play_ts is None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        local_play_ts, mode = _lookback(cap, fps, local_result_ts, max_lookback, yolo, candidates)
    cap.release()

    return idx, local_play_ts, mode


def _grab_all_result_frames(
    yolo_model,
    url: str,
    result_timestamps: list[float],
    tmp_dir: Path = None,
) -> dict[float, tuple[list[np.ndarray], Optional[float]]]:
    """스트림 URL을 직접 열어 각 결과 타임스탬프 주변 프레임을 grab.

    스캔과 동일한 cv2 스트리밍 방식 — yt-dlp --download-sections 의 DASH 조각
    다운로드가 YouTube 쓰로틀링에 매달리는 문제를 회피하며 1080p를 유지한다.
    (tmp_dir 인자는 하위 호환용, 사용하지 않음.)
    """
    if not result_timestamps:
        return {}

    from core.pipeline import get_stream_url   # 순환 import 회피 — 호출 시점 로드

    results: dict[float, tuple[list[np.ndarray], Optional[float]]] = {
        ts: ([], None) for ts in result_timestamps
    }

    try:
        stream_url = get_stream_url(url)
    except Exception as e:
        print(f"  ⚠️  OCR 스트림 URL 추출 실패 — 곡명 인식 생략: {e}")
        return results

    print(f"  OCR 결과 화면 스트리밍 분석 ({len(result_timestamps)}개 구간)...")

    cap = cv2.VideoCapture()
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 30_000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 15_000)
    cap.open(stream_url)
    if not cap.isOpened():
        print("  ⚠️  OCR 스트림 열기 실패 — 곡명 인식 생략")
        return results

    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    sample_step = max(1, int(fps * 0.2))   # ~0.2s 간격 샘플
    # seek 이 창보다 앞선 키프레임에 착지할 수 있으므로 여유분 포함해 grab 상한 설정
    max_grabs   = int(fps * (OCR_CLIP_PRE + OCR_CLIP_POST + 30)) + 200

    # 타임스탬프 오름차순으로 seek — 스트림은 앞으로 이동이 안정적
    for ts in sorted(result_timestamps):
        start_t = max(0.0, ts - OCR_CLIP_PRE)
        end_t   = ts + OCR_CLIP_POST
        cap.set(cv2.CAP_PROP_POS_MSEC, start_t * 1000.0)

        frame_data: list[tuple[np.ndarray, float]] = []
        idx = 0
        for _ in range(max_grabs):
            if not cap.grab():
                break
            cur = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if cur > end_t:
                break
            if cur < start_t:          # 창 이전(키프레임 착지 여유분) 프레임은 건너뜀
                continue
            if idx % sample_step == 0:
                ret, frame = cap.retrieve()
                if ret and frame is not None:
                    crop = _detect_game_crop(yolo_model, frame)
                    if crop is not None:
                        frame_data.append((crop, cur))
            idx += 1

        if frame_data:
            best_crop, best_vt = max(frame_data, key=lambda x: _sharpness(x[0]))
            results[ts] = ([best_crop], best_vt)
            print(f"  [OCR] {fmt_time(ts)} 분석 완료 ({len(frame_data)}프레임)")
        else:
            print(f"  [OCR] {fmt_time(ts)} 게임 화면 미검출")

    cap.release()
    return results
