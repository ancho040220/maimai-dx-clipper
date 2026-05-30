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
    OCR_FRAME_SAMPLE_COUNT, TIMEOUT_OCR_BATCH_BASE, TIMEOUT_OCR_BATCH_PER,
)
from core.error_messages import translate_error
from core.result_extractor import _sharpness
from core.scanner_parallel import _detect_game_crop, _init_yolo, _lookback, _build_candidates


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
    tmp_dir: Path,
) -> dict[float, tuple[list[np.ndarray], Optional[float]]]:
    """yt-dlp 1회 호출로 배치 다운로드, 완료된 클립부터 즉시 OCR 분석."""
    if not result_timestamps:
        return {}

    offsets = [round(-OCR_CLIP_PRE + i * 0.1, 1) for i in range(OCR_FRAME_SAMPLE_COUNT)]

    def _sec_to_hms(s: float) -> str:
        s = int(s)
        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

    clip_starts = {ts: max(0.0, ts - OCR_CLIP_PRE) for ts in result_timestamps}
    cs_to_ts    = {cs: ts for ts, cs in clip_starts.items()}

    sections_args = []
    for ts in result_timestamps:
        cs, ce = clip_starts[ts], ts + OCR_CLIP_POST
        sections_args += ["--download-sections", f"*{_sec_to_hms(cs)}-{_sec_to_hms(ce)}"]

    out_tmpl = str(tmp_dir / "_ocr_%(section_start)s.%(ext)s")

    print(f"  OCR 클립 다운로드 + 즉시 분석 ({len(result_timestamps)}개 구간)...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "yt_dlp",
         *sections_args,
         "-f", "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]",
         "--no-playlist", "--no-warnings",
         "-N", "16",
         *ytdlp_cookie_args(),
         "-o", out_tmpl, url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )

    def _analyze_clip(clip_path: Path, ts: float) -> tuple[list[np.ndarray], Optional[float]]:
        cs        = clip_starts[ts]
        base_t    = ts - cs
        frame_data: list[tuple[np.ndarray, float]] = []
        cap = cv2.VideoCapture(str(clip_path))
        if cap.isOpened():
            for offset in offsets:
                t = base_t + offset
                if t < 0:
                    continue
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ret, frame = cap.read()
                if ret and frame is not None:
                    crop = _detect_game_crop(yolo_model, frame)
                    if crop is not None:
                        frame_data.append((crop, ts + offset))
            cap.release()
        clip_path.unlink(missing_ok=True)
        if not frame_data:
            return [], None
        best_crop, best_vt = max(frame_data, key=lambda x: _sharpness(x[0]))
        return [best_crop], best_vt

    def _process_available_clips(processed: set, results: dict) -> None:
        """완료된 클립 파일을 찾아 즉시 분석."""
        for p in tmp_dir.glob("_ocr_*.*"):
            if p in processed or p.suffix == ".part":
                continue
            if Path(str(p) + ".part").exists() or p.stat().st_size == 0:
                continue
            processed.add(p)
            try:
                sec = float(p.stem.split("_ocr_", 1)[1])
            except (IndexError, ValueError):
                continue
            closest_cs = min(cs_to_ts, key=lambda s: abs(s - sec))
            if abs(closest_cs - sec) > 5:
                continue
            ts = cs_to_ts[closest_cs]
            if ts in results:
                continue
            print(f"  [OCR] {int(ts)}s 분석 중...")
            results[ts] = _analyze_clip(p, ts)  # (frames, exact_vt)

    results:   dict[float, list[np.ndarray]] = {}
    processed: set = set()
    deadline = time.time() + TIMEOUT_OCR_BATCH_BASE + TIMEOUT_OCR_BATCH_PER * len(result_timestamps)

    while time.time() < deadline:
        _process_available_clips(processed, results)
        if len(results) >= len(result_timestamps):
            break
        if proc.poll() is not None:
            _process_available_clips(processed, results)
            break
        time.sleep(0.3)

    if proc.poll() is None:
        proc.kill()
        proc.wait()

    for ts in result_timestamps:
        if ts not in results:
            results[ts] = ([], None)

    return results
