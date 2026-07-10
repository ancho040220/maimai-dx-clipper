"""
maimai DX Rating Change Scanner — 병렬 처리 버전

YOLO로 인게임 화면을 감지·크롭 → 1000×1000 정규화 → Tesseract OCR로 레이팅 추출.
영상을 N개 구간으로 분할해 각 구간을 별도 프로세스로 동시 처리.
"""

import argparse
import json
import multiprocessing
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from config.settings import (
    PROJECT_DIR,
    RATING_MIN, RATING_MAX, MAX_RATING_CHANGE, MIN_GAP,
    TESSERACT_CMD, TESS_CONFIG,
    MODEL_PATH, YOLO_CONF,
    BAR_WIDTH, LOOKBACK_STEP, TMPL_MATCH_THRESH,
    GAME_ROI,
    KALEID_START_ROI, KALEID_START_TMPL,
    TRACK_ROI, TRACK_TMPL,
    PERFECT_START_ROI, PERFECT_START_TMPL,
    GRADE_START_ROI, GRADE_START_TMPL,
    MODE_LABELS,
    TIMEOUT_WORKER_WATCHDOG,
)


# ── 유틸 ───────────────────────────────────────────────────────────────────────

def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def parse_time(s: str) -> float:
    parts = s.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"시간 형식 오류: '{s}' — MM:SS 또는 HH:MM:SS")


def yt_timestamp_url(url: str, seconds: float) -> str:
    t    = int(seconds)
    base = re.split(r"[?&]t=\d+", url)[0].rstrip("&?")
    sep  = "&" if "?" in base else "?"
    return f"{base}{sep}t={t}"


def _enable_ansi() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


# ── YOLO ───────────────────────────────────────────────────────────────────────

def _init_yolo(device: str = "cuda"):
    try:
        from ultralytics import YOLO
    except ImportError:
        print("❌  ultralytics 가 설치되어 있지 않습니다: pip install ultralytics")
        sys.exit(1)
    import torch
    if device == "cuda" and not torch.cuda.is_available():
        print("⚠️  CUDA 사용 불가 — CPU 로 대체합니다.")
        device = "cpu"
    model = YOLO(MODEL_PATH)
    model.to(device)
    return model


def _detect_game_crop(yolo_model, frame: np.ndarray) -> Optional[np.ndarray]:
    """YOLO로 인게임 화면 감지 → 1000×1000 크롭 반환. 미감지 시 None."""
    results = yolo_model(frame, verbose=False, conf=YOLO_CONF)
    boxes   = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None
    best = int(boxes.conf.argmax())
    x1, y1, x2, y2 = boxes.xyxy[best].cpu().numpy().astype(int)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (1000, 1000), interpolation=cv2.INTER_LINEAR)


def _init_tess():
    """워커 프로세스용 — 설치 확인 없이 빠르게 초기화."""
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    return pytesseract


def _preprocess(roi: np.ndarray) -> np.ndarray:
    h, w  = roi.shape[:2]
    large = cv2.resize(roi, (w * 4, h * 4), interpolation=cv2.INTER_CUBIC)
    gray  = cv2.cvtColor(large, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    return cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def _ocr_rating(tess, crop_1000: np.ndarray) -> Optional[int]:
    """1000×1000 크롭에서 GAME_ROI 영역을 OCR해 레이팅 반환. 실패 시 None."""
    x1, y1, x2, y2 = GAME_ROI
    roi = crop_1000[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    text  = tess.image_to_string(_preprocess(roi), config=TESS_CONFIG).strip()
    clean = text.replace(" ", "").replace(",", "").replace("\n", "")
    if clean.isdigit():
        val = int(clean)
        if RATING_MIN <= val <= RATING_MAX:
            return val
    return None


# ── 템플릿 매칭 ────────────────────────────────────────────────────────────────

def _match_template(
    crop_1000: np.ndarray,
    roi_coords: Optional[list],
    templates: list,
    thresh: float = TMPL_MATCH_THRESH,
) -> bool:
    """ROI 영역에서 templates 중 하나라도 thresh 이상이면 True.

    Lab CLAHE로 밝기를 정규화한 뒤 매칭하므로 조명·색온도가 다른 오락실에서도 안정적.
    """
    if not roi_coords or not templates:
        return False
    x1, y1, x2, y2 = roi_coords
    roi = crop_1000[y1:y2, x1:x2]
    if roi.size == 0:
        return False
    for template in templates:
        th, tw = template.shape[:2]
        rh, rw = roi.shape[:2]
        roi_r = cv2.resize(roi, (max(rw, tw), max(rh, th))) if rh < th or rw < tw else roi
        result = cv2.matchTemplate(roi_r, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val >= thresh:
            return True
    return False


def _load_template(folder: str) -> list:
    """폴더 내 모든 PNG 템플릿 이미지 로드."""
    folder_path = Path(folder)
    if not folder_path.exists():
        print(f"⚠️  템플릿 폴더 없음: {folder} — 해당 모드 판별 불가")
        return []
    templates = []
    for p in sorted(folder_path.glob("*.png")):
        img = cv2.imread(str(p))
        if img is not None:
            templates.append(img)
    if not templates:
        print(f"⚠️  템플릿 이미지 없음: {folder} — 해당 모드 판별 불가")
    return templates


# ── 템플릿 후보 빌드 ───────────────────────────────────────────────────────────

_candidates_cache: list = []


def _build_candidates() -> list:
    global _candidates_cache
    if not _candidates_cache:
        _candidates_cache = [
            (KALEID_START_ROI,  _load_template(KALEID_START_TMPL),  TMPL_MATCH_THRESH, "caleidoscope"),
            (PERFECT_START_ROI, _load_template(PERFECT_START_TMPL), TMPL_MATCH_THRESH, "perfect_challenge"),
            (GRADE_START_ROI,   _load_template(GRADE_START_TMPL),   TMPL_MATCH_THRESH, "grade"),
            (TRACK_ROI,         _load_template(TRACK_TMPL),         TMPL_MATCH_THRESH, "normal"),
        ]
    return _candidates_cache


# ── 역추적 ─────────────────────────────────────────────────────────────────────

def _lookback(
    cap, fps: float, result_ts: float, max_lookback: float,
    yolo_model, candidates: list,
) -> Tuple[Optional[float], Optional[str]]:
    """result_ts에서 역방향으로 탐색해 첫 번째 매칭된 시점과 모드명 반환."""
    delta = LOOKBACK_STEP
    while delta <= max_lookback:
        t = result_ts - delta
        delta += LOOKBACK_STEP   # read 실패 시에도 항상 전진 — 손상 프레임 연속 시 무한 루프 방지 (A-16)
        if t < 0:
            break
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ret, frame = cap.read()
        if not ret:
            continue
        crop = _detect_game_crop(yolo_model, frame)
        if crop is not None:
            for roi, tmpl, thresh, mode in candidates:
                if _match_template(crop, roi, tmpl, thresh):
                    return t, mode
    return None, None


# ── 진행 막대 ──────────────────────────────────────────────────────────────────

def _render_bar(wid: int, seg_start: float, seg_end: float, pct: float, done: bool) -> str:
    filled = int(BAR_WIDTH * pct / 100)
    bar    = "█" * filled + "░" * (BAR_WIDTH - filled)
    mark   = " ✓" if done else "  "
    return f"  W{wid} [{fmt_time(seg_start)}~{fmt_time(seg_end)}] [{bar}] {pct:5.1f}%{mark}"


def _progress_display(
    num_workers: int,
    segments: list,
    progress_queue: multiprocessing.Queue,
    stop_event: threading.Event,
    last_activity: list,  # [float] — 워커 마지막 활동 시각 (메인과 공유)
) -> None:
    _enable_ansi()
    states = [{"pct": 0.0, "done": False} for _ in range(num_workers)]
    for i in range(num_workers):
        s, e = segments[i]
        print(_render_bar(i, s, e, 0.0, False))
    sys.stdout.flush()

    def redraw():
        sys.stdout.write(f"\033[{num_workers}A")
        for i in range(num_workers):
            s, e = segments[i]
            sys.stdout.write(f"\033[2K{_render_bar(i, s, e, states[i]['pct'], states[i]['done'])}\n")
        sys.stdout.flush()

    while not stop_event.is_set():
        try:
            msg = progress_queue.get(timeout=0.15)
            last_activity[0] = time.time()  # 워커 활동 확인
            states[msg["wid"]]["pct"]  = msg["pct"]
            states[msg["wid"]]["done"] = msg.get("done", False)
            redraw()
        except Exception:
            pass

    while True:
        try:
            msg = progress_queue.get_nowait()
            states[msg["wid"]]["pct"]  = msg["pct"]
            states[msg["wid"]]["done"] = msg.get("done", False)
        except Exception:
            break
    redraw()
    print()


# ── 스캔 워커 ──────────────────────────────────────────────────────────────────

def _open_and_seek(
    video_path: str, start_sec: float, scan_floor: float, worker_id: int
) -> tuple:
    """VideoCapture 열기 + 시작 위치 seek. 실패 시 (None, start_sec, 30.0) 반환."""
    cap = cv2.VideoCapture()
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 30_000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 15_000)
    cap.open(video_path)
    if not cap.isOpened():
        return None, start_sec, 30.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if video_path.startswith(("http://", "https://")):
        actual_start = _seek_verified(cap, start_sec, scan_floor, worker_id=worker_id)
    else:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_sec * fps))
        actual_start = start_sec
    return cap, actual_start, fps


def _seek_verified(
    cap: cv2.VideoCapture,
    target_sec: float,
    scan_floor: float,
    tol_sec: float = 30.0,
    max_retries: int = 5,
    worker_id: int = -1,
) -> float:
    """스트리밍 URL seek 오버슈트 검증 및 backoff.

    cap.read() 후 CAP_PROP_POS_MSEC(PTS 기반)으로 실제 착지 위치를 확인하고,
    목표보다 tol_sec 초 이상 뒤에 착지했으면 seek_pos를 앞으로 당겨 재시도한다.
    로컬 파일에는 호출하지 않는다.
    """
    wid_tag     = f"W{worker_id}" if worker_id >= 0 else "seek"
    seek_pos    = target_sec
    prev_actual: Optional[float] = None
    actual_sec  = target_sec  # ret=False 등 비정상 시 fallback

    for attempt in range(max_retries):
        cap.set(cv2.CAP_PROP_POS_MSEC, seek_pos * 1000.0)
        ret, _ = cap.read()
        if not ret:
            break
        actual_sec = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

        if actual_sec <= target_sec + tol_sec:
            return actual_sec  # 목표 ±tol_sec 이내 — 성공

        # ── Stuck 감지: 이전 착지와 같은 위치면 강제 -5분 ────────────────────
        if prev_actual is not None and abs(actual_sec - prev_actual) < 1.0:
            new_seek = max(scan_floor, seek_pos - 300.0)
        else:
            overshoot = actual_sec - seek_pos
            new_seek  = max(scan_floor, seek_pos - overshoot * 1.5)

        print(
            f"  [{wid_tag}] seek 오버슈트: 목표 {fmt_time(target_sec)} → "
            f"착지 {fmt_time(actual_sec)}, backoff → {fmt_time(new_seek)} "
            f"({attempt + 1}회차)"
        )
        seek_pos    = new_seek
        prev_actual = actual_sec

    return actual_sec


def _worker(args: dict) -> dict:
    """단일 구간 스캔 워커. YOLO로 인게임 화면 감지 → OCR → 레이팅 변동 수집."""
    worker_id      = args["worker_id"]
    video_path     = args["video_path"]
    start_sec      = args["start_sec"]
    end_sec        = args["end_sec"]
    scan_floor     = args.get("scan_floor", 0.0)
    initial_rating = args.get("initial_rating")
    progress_queue = args.get("progress_queue")
    result_queue   = args.get("result_queue")

    def _ping():
        if progress_queue is not None:
            try:
                progress_queue.put_nowait({"wid": worker_id, "pct": 0.0, "done": False, "elapsed": 0.0})
            except Exception:
                pass

    _ping()                      # 프로세스 시작 — watchdog 오발동 방지
    tess       = _init_tess()
    _ping()                      # Tesseract 완료
    yolo_model = _init_yolo("cuda")
    _ping()                      # YOLO 완료

    cap, actual_start, fps = _open_and_seek(video_path, start_sec, scan_floor, worker_id)
    if cap is None:
        print(f"  [W{worker_id}] 영상 열기 실패 — 건너뜀")
        result = {"worker_id": worker_id, "readings": [], "frames_checked": 0}
        if result_queue is not None:
            result_queue.put(result)
        return result

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration     = total_frames / fps
    _end         = min(end_sec if end_sec is not None else duration, duration)
    step         = max(1, int(fps))
    end_frame    = int(_end * fps)
    start_frame  = int(actual_start * fps)
    frame_num   = start_frame

    seg_duration    = _end - actual_start
    last_report_pct = -1.0
    readings: list                              = []
    pending_rating: Optional[Tuple[float, int]] = None
    last_result_time                            = actual_start - MIN_GAP - 1
    last_confirmed                              = initial_rating
    frames_checked                              = 0

    _worker_start = time.time()

    def _report_progress(pct: float, done: bool = False):
        if progress_queue is not None:
            progress_queue.put({
                "wid":     worker_id,
                "pct":     pct,
                "done":    done,
                "elapsed": time.time() - _worker_start,
            })

    _report_progress(0.0)

    _is_stream = video_path.startswith(("http://", "https://"))
    _reconnect_count = 0

    while frame_num < end_frame:
        ret = cap.grab()
        if not ret:
            if not _is_stream:
                break
            # 세그먼트 끝 60초 이내 grab 실패 → 재연결 없이 즉시 종료
            # 영상 종단부에서 재연결을 반복하면 cap.open/seek 블로킹으로 수분 지연 발생
            if end_frame - frame_num <= step * 60:
                break
            # 스트리밍 URL: 재연결 시도 (네트워크 전환 대응)
            _reconnect_count += 1
            if _reconnect_count > 3:
                print(f"  [W{worker_id}] 연속 3회 재연결 실패 — 중단")
                break
            current_sec = frame_num / fps
            print(f"  [W{worker_id}] 스트림 끊김 @ {fmt_time(current_sec)} — 5초 후 재연결... ({_reconnect_count}/3)")
            time.sleep(5)
            cap.release()
            # _open_and_seek가 반환하는 검증된 착지 위치 사용 — seek 직후 POS_MSEC는
            # 첫 grab 전까지 stale이라 잘못된 frame_num을 만들 수 있음 (A-4)
            cap, reconnect_sec, _ = _open_and_seek(video_path, current_sec, scan_floor, worker_id)
            if cap is None:
                break
            frame_num = int(reconnect_sec * fps)
            continue
        _reconnect_count = 0
        if (frame_num - start_frame) % step == 0:
            ret2, frame = cap.retrieve()
            if ret2:
                current_sec    = frame_num / fps
                frames_checked += 1

                pct = (current_sec - actual_start) / seg_duration * 100 if seg_duration > 0 else 0
                if pct - last_report_pct >= 1.0:
                    _report_progress(pct)
                    last_report_pct = pct

                if current_sec - last_result_time < MIN_GAP:
                    pending_rating = None
                else:
                    crop           = _detect_game_crop(yolo_model, frame)
                    current_rating = _ocr_rating(tess, crop) if crop is not None else None

                    if pending_rating is not None:
                        prev_ts, prev_val = pending_rating
                        pending_rating    = None
                        confirmed         = current_rating if current_rating is not None else prev_val
                        readings.append((prev_ts, confirmed))
                        last_result_time  = prev_ts
                        last_confirmed    = confirmed
                    elif current_rating is not None:
                        if (last_confirmed is None
                                or (last_confirmed <= current_rating
                                    and current_rating - last_confirmed < MAX_RATING_CHANGE)):
                            pending_rating = (current_sec, current_rating)

        frame_num += 1

    cap.release()

    if pending_rating is not None:
        readings.append(pending_rating)

    _report_progress(100.0, done=True)

    result = {"worker_id": worker_id, "readings": readings, "frames_checked": frames_checked}
    if result_queue is not None:
        result_queue.put(result)
    return result


# ── 결과 병합 ──────────────────────────────────────────────────────────────────

def merge_and_detect(
    all_results: list,
    initial_rating: Optional[int],
    video_url: str,
) -> Tuple[list, int]:
    all_readings: list = []
    total_frames = 0
    for result in all_results:
        all_readings.extend(result["readings"])
        total_frames += result["frames_checked"]

    all_readings.sort(key=lambda x: x[0])

    deduped: list = []
    last_ts = -999.0
    for ts, val in all_readings:
        if ts - last_ts >= 1.0:
            deduped.append((ts, val))
            last_ts = ts

    history: list    = []
    last_rating      = initial_rating
    last_result_time = -MIN_GAP

    for ts, val in deduped:
        if ts - last_result_time < MIN_GAP:
            continue
        if last_rating is None:
            last_rating = val
            last_result_time = ts
            continue
        if val < last_rating or (val - last_rating) >= MAX_RATING_CHANGE:
            continue
        if val == last_rating:
            last_result_time = ts
            continue

        change = val - last_rating
        history.append({
            "timestamp":       ts,
            "previous_rating": last_rating,
            "current_rating":  val,
            "change":          change,
            "yt_url":          yt_timestamp_url(video_url, ts),
        })
        print(f"✅  [{fmt_time(ts)}] {last_rating} → {val} (+{change})")
        print(f"[DETECT] {json.dumps({'id': f'result_{len(history)}', 't': fmt_time(ts), 'play_t': None, 'before': last_rating, 'after': val, 'delta': change}, ensure_ascii=False)}")
        last_result_time = ts
        last_rating      = val

    return history, total_frames


# ── 워커 수 자동 결정 ─────────────────────────────────────────────────────────

def _auto_workers() -> int:
    """CPU · RAM · VRAM 기반으로 적정 워커 수를 자동 결정."""
    import psutil, sys

    torch = sys.modules.get("torch")
    if torch is None:
        try:
            import torch
        except Exception:
            torch = None

    cuda = torch is not None and torch.cuda.is_available()
    cores = multiprocessing.cpu_count()

    # RAM: 가용 메모리에서 4GB 여유 확보, 워커 1개당 약 2GB 소비
    avail_gb  = psutil.virtual_memory().available / 1024 ** 3
    usable_gb = max(0.0, avail_gb - 4.0)
    by_ram    = max(1, int(usable_gb // 2))

    if cuda:
        # CUDA: YOLO는 GPU 처리 → CPU는 디코딩 + Tesseract만
        by_cpu  = max(1, cores - 1)
        by_vram = by_cpu
        vram_gb = 0.0
        try:
            vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            by_vram = max(1, int(vram_gb - 2))
        except Exception:
            pass
        # VRAM 기반 상한: 소비자급(8GB 미만)=4, 중급(8~16GB)=6, 고급(16GB+)=8
        vram_cap = 4 if vram_gb < 8 else 6 if vram_gb < 16 else 8
        result = min(by_cpu, by_ram, by_vram, vram_cap)
        print(f"    [AUTO] CUDA 모드 — CPU={by_cpu} RAM={by_ram} VRAM={by_vram} cap={vram_cap} → {result}")
    else:
        # CPU: YOLO + Tesseract 모두 CPU → 워커당 약 4코어 소비
        by_cpu = max(1, cores // 2)
        # 코어 수 기반 상한: 8코어 미만=2, 8~15코어=4, 16코어+=6
        cpu_cap = 2 if cores < 8 else 4 if cores < 16 else 6
        result = min(by_cpu, by_ram, cpu_cap)
        print(f"    [AUTO] CPU 모드  — CPU={by_cpu} RAM={by_ram} cap={cpu_cap} → {result}")
    return result


# ── 병렬 스캔 ──────────────────────────────────────────────────────────────────

# _seek_verified tol_sec(30s)만큼 앞당겨 세그먼트 경계 blind spot 방지
_SEG_OVERLAP = 30.0


def scan_parallel(
    video_url: str,
    video_path: str,
    start_sec: float,
    end_sec: Optional[float],
    initial_rating: Optional[int],
    num_workers: int,
    output_file: Optional[str] = None,
    cancel_event=None,
) -> list:
    print(f"📍  YOLO → 1000×1000 → OCR ROI {GAME_ROI}\n")

    if num_workers <= 0:
        num_workers = _auto_workers()

    cap          = cv2.VideoCapture(video_path)
    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    duration      = total_frames / fps
    _end          = min(end_sec if end_sec is not None else duration, duration)
    scan_duration = _end - start_sec

    seg_len  = scan_duration / num_workers
    # 겹친 구간의 중복 감지는 merge_and_detect에서 1초 단위 dedup으로 처리됨
    segments = [
        (max(start_sec, start_sec + i * seg_len - _SEG_OVERLAP) if i > 0 else start_sec,
         start_sec + (i + 1) * seg_len if i < num_workers - 1 else _end)
        for i in range(num_workers)
    ]

    print(f"⚡  병렬 처리: {num_workers}개 워커\n")

    progress_queue = multiprocessing.Queue()
    result_queue   = multiprocessing.Queue()

    worker_args = [
        {"worker_id": i, "video_path": video_path, "start_sec": s, "end_sec": e,
         "initial_rating": initial_rating, "progress_queue": progress_queue,
         "result_queue": result_queue, "scan_floor": start_sec}
        for i, (s, e) in enumerate(segments)
    ]

    scan_start    = time.time()
    last_activity = [scan_start]  # display_thread와 공유 — 워커 마지막 활동 시각

    stop_event     = threading.Event()
    display_thread = threading.Thread(
        target=_progress_display,
        args=(num_workers, segments, progress_queue, stop_event, last_activity),
        daemon=True,
    )
    display_thread.start()

    processes = [multiprocessing.Process(target=_worker, args=(wa,), daemon=True) for wa in worker_args]
    for p in processes:
        p.start()

    raw_results = []
    pending     = num_workers
    cancelled   = False

    try:
        while pending > 0:
            # 사용자 중단 요청 — 워커 프로세스는 finally에서 정리됨
            if cancel_event is not None and cancel_event.is_set():
                print("🛑  사용자 중단 요청 — 스캔을 종료합니다.")
                cancelled = True
                break
            # 모든 프로세스가 종료됐으면 남은 결과를 드레인 후 빠져나감
            if all(not p.is_alive() for p in processes):
                while True:
                    try:
                        raw_results.append(result_queue.get_nowait())
                        pending -= 1
                    except Exception:
                        break
                break
            # 워커 활동 기반 watchdog — 어떤 워커도 10분간 progress를 보내지 않으면 freeze로 판단
            if time.time() - last_activity[0] > TIMEOUT_WORKER_WATCHDOG:
                print(f"⚠️  워커 활동 없음 ({TIMEOUT_WORKER_WATCHDOG}초 초과) — 강제 중단합니다.")
                break
            try:
                raw_results.append(result_queue.get(timeout=0.1))
                pending -= 1
            except Exception:
                pass
    finally:
        # 중단 요청이면 즉시 종료. cancelled 플래그뿐 아니라 cancel_event 도 확인 —
        # stop() 이 주입한 KeyboardInterrupt 가 루프의 cancel 체크보다 먼저 터지면
        # cancelled 가 False 로 남아 느린 join 경로를 타면서 워커가 계속 진행되기 때문.
        stop_requested = cancelled or (cancel_event is not None and cancel_event.is_set())
        for p in processes:
            if stop_requested:
                if p.is_alive():
                    p.terminate()
            else:
                p.join(timeout=10)
                if p.is_alive():
                    p.terminate()
        stop_event.set()
        display_thread.join()

    if cancelled or (cancel_event is not None and cancel_event.is_set()):
        return []

    raw_results = sorted(raw_results, key=lambda x: x["worker_id"])

    print("📊  병합 중...\n")
    history, total_checked = merge_and_detect(raw_results, initial_rating, video_url)

    m, s = divmod(int(time.time() - scan_start), 60)
    print(f"\n📈  완료: {total_checked:,}프레임 스캔 | {len(history)}회 레이팅 감지 | 소요 {m}분 {s}초")

    _print_results(history)

    if output_file:
        _save_results(history, video_url, output_file)
        print(f"💾  결과 저장: {output_file}")

    return history


# ── 결과 출력 / 저장 ────────────────────────────────────────────────────────────

def _print_results(history: list) -> None:
    print("\n" + "=" * 60)
    print("Rating 변동 내역")
    print("=" * 60)
    if not history:
        print("레이팅 상승이 감지되지 않았습니다.")
        return
    for e in history:
        ts         = fmt_time(e["timestamp"])
        pts        = fmt_time(e["play_timestamp"]) if e.get("play_timestamp") else "-"
        mode_label = MODE_LABELS.get(e.get("mode", ""), "미확인")
        print(f"> - [{ts}] {e.get('previous_rating','?')} -> {e['current_rating']} (+{e.get('change','?')})  플레이: {pts}  모드: {mode_label}")
    print(f"\n총 {len(history)}회 레이팅 상승")


def _save_results(history: list, video_url: str, path: str) -> None:
    lines = ["Rating 변동 내역", f"영상: {video_url}", "=" * 60]
    for e in history:
        ts         = fmt_time(e["timestamp"])
        yt         = e.get("yt_url", "")
        play_yt    = e.get("play_url", "")
        pts        = fmt_time(e["play_timestamp"]) if e.get("play_timestamp") else "-"
        mode_label = MODE_LABELS.get(e.get("mode", ""), "미확인")
        lines.append(
            f"> - [{ts}] {e.get('previous_rating','?')} -> {e['current_rating']} (+{e.get('change','?')})  "
            f"모드: {mode_label}  결과: {yt}  플레이 시작 시간: [{pts}] {play_yt}"
        )
    lines.append(f"\n총 {len(history)}회 레이팅 상승")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
