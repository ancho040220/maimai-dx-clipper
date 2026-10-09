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
    PROJECT_DIR, ytdlp_cookie_args, NO_WINDOW, OCR_CLIP_PRE, OCR_CLIP_POST,
)
from core.error_messages import translate_error
from core.result_extractor import _sharpness
from core.scanner_parallel import _detect_game_crop, _init_yolo, _lookback, _build_candidates, fmt_time


_MAX_DL_ATTEMPTS = 3    # 다운로드 최대 재시도 횟수
_DL_TIMEOUT      = 300  # 다운로드 타임아웃 (초)
_FFMPEG_TIMEOUT  = 120  # ffmpeg 커팅 타임아웃 (초)

# 곡 시작을 찾는 임시 파일은 "영상+소리 합본" 중 최고(유튜브에서는 360p 하나뿐)로 충분하다.
_DEFAULT_FORMAT = "best[height<=1080]/best"
# 최종 클립 파일용: 영상 전용(DASH)과 소리를 따로 받아 합친다. 합본(best)은 360p 라서 쓰지 않는다.
# 마지막에 /best 를 두지 않는 이유: 고화질을 못 받으면 실패로 알려서 낮은 단계로 넘어가야 하기 때문이다.
# H.264(avc1)를 먼저 고른다: 새 방송은 AV1 도 있는데, 기본 선택은 AV1 이라 윈도우 기본 재생기·편집 프로그램에서 안 열릴 수 있다.
_HQ_FORMATS = {
    1080: ("bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
           "/bestvideo[height<=1080]+bestaudio"),
    720:  ("bestvideo[height<=720][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
           "/bestvideo[height<=720]+bestaudio"),
}

_last_error = ""        # 마지막 다운로드 실패 사유(한글) — 화질 경고창에 쓴다


def last_download_error() -> str:
    return _last_error


def _log_failure(output: Path, fmt: str, detail: str) -> None:
    """다운로드 실패 상세(yt-dlp 오류 전문)를 logs/error.log 에 남긴다 — 화면에는 한 줄 사유만 보이므로 원인 추적용."""
    try:
        logs = PROJECT_DIR / "logs"
        logs.mkdir(exist_ok=True)
        with open(logs / "error.log", "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] [download] {output.name}\nformat: {fmt}\n{detail[-2000:]}\n")
    except Exception:
        pass


def _reason(stderr: str) -> str:
    """yt-dlp 오류 출력에서 사용자에게 보여 줄 한 줄 사유."""
    if "Requested format is not available" in stderr:
        return "이 영상에는 요청한 화질의 영상 형식이 없거나 받을 수 없습니다"
    last = stderr.splitlines()[-1] if stderr else ""
    return " ".join(translate_error(last).split())[:160]


def _download_segment(
    start_sec: float, end_sec: float, output: Path, url: str, fmt: Optional[str] = None,
) -> bool:
    """yt-dlp --download-sections로 구간 다운로드. fmt 를 주지 않으면 합본 최고 화질(360p)."""
    global _last_error
    _last_error = ""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--download-sections", f"*{int(start_sec)}-{int(end_sec)}",
        "--socket-timeout", "30",
        "-f", fmt or _DEFAULT_FORMAT,
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
                _last_error = _reason(stderr)
                print(f"  ⚠️  다운로드 오류 ({output.name}): {_last_error}")
            return False

        if timed_out:
            _log_failure(output, fmt or _DEFAULT_FORMAT, f"timeout {_DL_TIMEOUT}s")
            _last_error = f"다운로드가 {_DL_TIMEOUT}초 안에 끝나지 않았습니다"
            print(f"  타임아웃 ({output.name}): {_DL_TIMEOUT}초 초과 — {'재시도' if attempt < _MAX_DL_ATTEMPTS else '건너뜀'}")
            output.unlink(missing_ok=True)
        elif proc.returncode != 0:
            _log_failure(output, fmt or _DEFAULT_FORMAT, stderr or f"exit code {proc.returncode}")
            if stderr:
                _last_error = _reason(stderr)
                print(f"  ⚠️  다운로드 오류 ({output.name}): {_last_error}")
            output.unlink(missing_ok=True)
        elif output.exists():
            return True

        if attempt < _MAX_DL_ATTEMPTS:
            delay = 2.0 * (2.0 ** (attempt - 1))
            print(f"  ⚠️  {output.name} 재시도 {attempt}/{_MAX_DL_ATTEMPTS - 1} ({delay:.0f}초 후)...")
            time.sleep(delay)

    return False


def _probe_height(path: Path) -> Optional[int]:
    """영상 높이(px). ffprobe 가 없거나 읽지 못하면 None."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW,
        )
        return int(r.stdout.strip().splitlines()[0]) if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def _download_clip_hq(start_sec: float, end_sec: float, output: Path, url: str, height: int) -> bool:
    """최종 클립 구간을 height(1080 / 720) 화질로 받는다. 실패하면 False — 사유는 last_download_error()."""
    import math
    output.unlink(missing_ok=True)
    ok = _download_segment(math.floor(start_sec), math.ceil(end_sec), output, url, fmt=_HQ_FORMATS[height])
    if ok:
        got = _probe_height(output)
        print(f"    🎞  {height}p 요청 → 받은 화질 {got}p" if got else f"    🎞  {height}p 요청")
    return ok


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
