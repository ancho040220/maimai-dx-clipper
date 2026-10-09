"""VOD / 라이브 모드 판별 및 전체 작업 흐름 총괄."""
import json
import multiprocessing
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import yt_dlp as ytdlp

from config.settings import (
    PROJECT_DIR, ytdlp_cookie_args, ytdlp_cookie_opts,
    HIGHLIGHT_POST, MIN_GAP, MAX_RATING_CHANGE,
    DL_WORKERS_MAX, NO_WINDOW, OCR_CONFIDENCE_MIN,
    TIMEOUT_OCR_BATCH_BASE, TIMEOUT_OCR_BATCH_PER,
    TIMEOUT_OCR_EDIT_WAIT, TIMEOUT_LOOKBACK_PER_ITEM,
)
from core.scanner_parallel import (
    _init_yolo, _print_results, _save_results,
    fmt_time, scan_parallel, yt_timestamp_url,
)
from core.result_extractor import achievement_to_rank, extract_from_frames, find_record, read_track_number
from core.result_screen import find_track_gaps
from core.error_messages import translate_error, log_error
from core.retry import with_retry
from core.downloader import _download_segment, _segment_lookback_worker, _grab_all_result_frames
from core.clip_builder import _cut_and_upload_clips


# ── URL / 상태 유틸 ────────────────────────────────────────────────────────────

def check_video_status(url: str) -> dict:
    """URL의 라이브 여부 및 메타데이터를 반환."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": False,
        "skip_download": True,
        "no_check_formats": True,
        "ignore_no_formats_error": True,
        "no_playlist": True,
        **ytdlp_cookie_opts(),
    }

    def _fetch():
        with ytdlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                log_error(e, context="check_video_status")
                raise   # 원본 예외 유지 → is_retryable가 원문/타입으로 재시도 여부 판별
        if info is None:
            raise RuntimeError("yt-dlp 메타데이터 조회 실패: 정보를 가져오지 못했습니다.")
        return info

    try:
        info = with_retry(_fetch, max_attempts=3, label="URL 상태 확인")
    except Exception as e:
        raise RuntimeError(translate_error(e)) from e   # 사용자용 번역은 최상위에서 한 번만
    return {
        "is_live":    info.get("is_live", False),
        "title":      info.get("title", ""),
        "channel":    info.get("uploader", ""),
        "channel_id": info.get("channel_id", ""),   # 본인 영상인지 대조하는 데 사용
        "duration":   info.get("duration"),
    }


def fetch_stream_start(url: str):
    """방송 시작 시각(epoch)과 정확 여부. 알 수 없으면 (None, False).

    라이브 VOD는 release_timestamp(실제 시작 시각)가 있어 분 단위로 맞는다.
    일반 업로드 영상은 날짜(upload_date)만 알 수 있어 정확 여부를 False로 돌려준다.
    날짜 표기는 부가 기능이라 실패해도 클립 작업을 막지 않는다.
    """
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "no_check_formats": True, "ignore_no_formats_error": True,
                "no_playlist": True, **ytdlp_cookie_opts()}
        with ytdlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        ts = info.get("release_timestamp")
        if ts:
            return float(ts), True
        d = info.get("upload_date")          # YYYYMMDD
        if d:
            import datetime as _dt
            return _dt.datetime.strptime(d, "%Y%m%d").timestamp(), False
    except Exception as e:
        print(f"  ⚠️  방송 날짜 조회 실패 (날짜 표기 생략): {e}")
    return None, False


def get_stream_url(url: str) -> str:
    """yt-dlp로 direct 스트림 URL만 추출 (다운로드 없음)."""
    cmd = [
        sys.executable, "-m", "yt_dlp", "--get-url",
        "-f", "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/bestvideo/best[height<=1080]/best",
        "--no-playlist",
        *ytdlp_cookie_args(),
        url,
    ]

    def _fetch():
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
        lines = result.stdout.strip().splitlines()
        if not lines:
            stderr = result.stderr
            if "Sign in to confirm" in stderr or "Login required" in stderr:
                raise RuntimeError(
                    "YouTube가 봇으로 인식하고 있습니다.\n"
                    "Firefox에서 YouTube에 로그인한 뒤 다시 시도하세요."
                )
            detail = stderr.strip().splitlines()
            raise RuntimeError(f"스트림 URL 추출 실패: {detail[-1] if detail else '출력 없음'}")
        return lines[0]

    return with_retry(_fetch, max_attempts=3, base_delay=3.0, label="스트림 URL 추출")


# ── VOD 스트리밍 분석 ────────────────────────────────────────────────────────

def analyze_vod_stream(
    url: str,
    start_sec: float = 0.0,
    end_sec: Optional[float] = None,
    initial_rating: Optional[int] = None,
    num_workers: int = 0,
    output_file: Optional[str] = None,
    cancel_event=None,
    record_mode: bool = False,
    output_dir: Optional[Path] = None,
) -> list:
    """VOD를 스트림 URL로 직접 포워드 스캔.

    record_mode=True 면 레이팅 숫자 대신 결과 화면을 찾아, 신기록을 세운 판만 돌려준다.
    """
    print("🔗  스트림 URL 추출 중...")
    stream_url = get_stream_url(url)
    print("    OK\n")

    n_workers = num_workers if num_workers > 0 else 4

    if record_mode:
        out = Path(output_dir) if output_dir is not None else PROJECT_DIR / "highlights"
        plays = scan_parallel(
            video_url=url,
            video_path=stream_url,
            start_sec=start_sec,
            end_sec=end_sec,
            initial_rating=None,
            num_workers=n_workers,
            output_file=None,
            cancel_event=cancel_event,
            detect_mode="results",
            frames_dir=out / "_scan_frames",
        )
        if cancel_event is not None and cancel_event.is_set():
            return []
        return _analyze_record_plays(plays, out)

    return scan_parallel(
        video_url=url,
        video_path=stream_url,
        start_sec=start_sec,
        end_sec=end_sec,
        initial_rating=initial_rating,
        num_workers=n_workers,
        output_file=output_file,
        cancel_event=cancel_event,
    )


# ── 신기록 분석 ───────────────────────────────────────────────────────────────

def _analyze_record_plays_local(plays: list, output_dir: Path) -> tuple:
    """결과 화면 크롭으로 곡·달성률·신기록을 읽어 신기록이 아닌 판을 뺀다 (이 프로세스에서 직접).

    스캔 때 저장해 둔 크롭을 쓰므로 영상을 다시 받지 않는다. 신기록 여부를 판독하지 못한 판은
    (곡명을 못 읽은 판 포함) 지우지 않고 남겨서 사용자가 목록에서 직접 고르게 한다.
    PaddleOCR을 쓰므로 앱 프로세스가 아니라 `_analyze_record_plays`가 띄우는 별도 프로세스에서 돌린다.
    """
    import cv2

    from data.song_db import load_song_db
    from core.result_extractor import _sharpness

    n = len(plays)
    print(f"\n🔎  결과 화면 {n}판 — 곡·신기록 분석 중...")
    song_titles, raw_songs = load_song_db(region="intl")
    result_frames_dir = output_dir / "result_frames"
    result_frames_dir.mkdir(parents=True, exist_ok=True)

    kept: list = []
    tracks: list = []          # 신기록이 아닌 판까지 포함해 모든 판의 TRACK 번호 — 빠진 판 확인용
    n_not = n_unknown = 0
    for i, entry in enumerate(plays):
        print(f"[OCR_PROG] {json.dumps({'done': i, 'total': n}, ensure_ascii=False)}")
        frames = [f for f in (cv2.imread(p) for p in entry.get("result_frames", [])) if f is not None]
        tracks.append(read_track_number(frames) if frames else None)
        ocr_result = None
        if frames:
            try:
                ocr_result = extract_from_frames(
                    frames, song_titles, raw_songs, fps=10, skip_sec=0.0,
                    video_ts=entry["timestamp"], with_record=True,
                )
            except Exception as e:
                print(f"  ⚠️  결과 화면 분석 실패 ({fmt_time(entry['timestamp'])}): {e}")

        if ocr_result is not None:
            record = (ocr_result.achievement, ocr_result.my_best, ocr_result.best_delta, ocr_result.new_record,
                      ocr_result.record_exact, ocr_result.record_frame)
        elif frames:
            # 곡을 못 읽었어도 신기록 여부는 따로 판정한다 — 신기록이 아닌 판까지 목록에 남기지 않기 위해
            found  = find_record(frames)
            record = ((found.achievement, found.my_best, found.delta, found.new_record, found.exact, found.frame_index)
                      if found is not None else (None, None, None, None, None, None))
        else:
            record = (None, None, None, None, None, None)
        new_record = record[3]

        if new_record is False:
            n_not += 1
            who = ocr_result.title if ocr_result is not None else "곡 미인식"
            print(f"  ⏭  [{fmt_time(entry['timestamp'])}] 신기록 아님 — {who} {record[0]:.4f}%")
            continue

        if ocr_result is not None:
            _apply_ocr_to_entry(entry, ocr_result)
        entry["new_record"] = new_record
        if new_record and record[1] is not None and record[1] <= 0.0:
            entry["first_play"] = True            # MY BEST 0.0000% — 처음 친 채보라 개선폭이 달성률 그대로다
        elif new_record:
            entry["my_best"]      = record[1]
            entry["record_delta"] = record[2]
            entry["record_exact"] = record[4]
        if record[0] is not None:
            # 곡 읽기가 고른 프레임은 큰 숫자를 못 읽어 MY BEST 값을 달성률로 내는 경우가 있다 — 판 단위 판독을 우선한다
            entry["achievement"] = record[0]
            entry["rank"]        = achievement_to_rank(record[0])
        if new_record is None:
            n_unknown += 1
            print(f"  ❓  [{fmt_time(entry['timestamp'])}] 신기록 여부를 읽지 못했습니다 — 목록에 남겨 둡니다")
        entry["_prefilled"] = True
        kept.append(entry)
        if frames:
            try:
                # 목록 썸네일: 숫자를 읽는 데 쓴 프레임 (못 읽었으면 가장 선명한 프레임)
                idx   = record[5]
                thumb = frames[idx] if idx is not None and 0 <= idx < len(frames) else max(frames, key=_sharpness)
                cv2.imwrite(str(result_frames_dir / f"result_{len(kept)}.jpg"), thumb)
            except Exception:
                pass
    print(f"[OCR_PROG] {json.dumps({'done': n, 'total': n}, ensure_ascii=False)}")
    n_rec = sum(1 for e in kept if e.get("new_record"))
    print(f"\n📈  결과 화면 {n}판 → 신기록 {n_rec}판 · 판독 못 한 판 {n_unknown}판 · 신기록 아님 {n_not}판 제외")
    gaps = find_track_gaps([(e["timestamp"], tr) for e, tr in zip(plays, tracks)])
    print(f"  TRACK 번호를 읽은 판 {sum(1 for tr in tracks if tr)}/{n}")
    return kept, gaps


class _QueueWriter:
    """자식 프로세스의 print를 큐로 보내 앱 로그에 나오게 한다."""

    def __init__(self, q):
        self._q = q

    def write(self, s):
        if s:
            self._q.put(("log", s))
        return len(s)

    def flush(self):
        pass


def _record_worker(plays: list, output_dir: str, q) -> None:
    sys.stdout = sys.stderr = _QueueWriter(q)
    try:
        q.put(("result", _analyze_record_plays_local(plays, Path(output_dir))))   # (kept, gaps)
    except BaseException as e:      # 자식 프로세스는 어떤 실패든 부모에게 알려야 한다
        q.put(("error", f"{e.__class__.__name__}: {e}"))


def _emit_record_detections(kept: list) -> None:
    """스캔 결과 목록에 올릴 [DETECT] 줄을 낸다. id는 목록 순서(result_1…)."""
    for k, entry in enumerate(kept, 1):
        payload = {
            "id": f"result_{k}", "t": fmt_time(entry["timestamp"]), "play_t": None,
            "before": None, "after": None, "delta": None, "rec": True,
            "new_record":   entry.get("new_record"),
            "first_play":   bool(entry.get("first_play")),
            "my_best":      entry.get("my_best"),
            "record_delta": entry.get("record_delta"),
            "record_exact": entry.get("record_exact"),
            "song_title":     entry.get("song_title"),
            "difficulty":     entry.get("difficulty"),
            "achievement":    entry.get("achievement"),
            "rank":           entry.get("rank"),
            "internal_level": entry.get("internal_level"),
            "chart_type":     entry.get("chart_type"),
        }
        print(f"[DETECT] {json.dumps(payload, ensure_ascii=False)}")


def _emit_track_gaps(gaps: list) -> None:
    """TRACK 번호가 건너뛴 곳을 로그와 스캔 결과 화면(상단 안내)에 알린다."""
    for g in gaps:
        msg = f"TRACK {g['prev']} → {g['next']}: 사이에서 판을 놓쳤을 수 있습니다 ({fmt_time(g['t0'])} ~ {fmt_time(g['t1'])})"
        print(f"  ⚠️  {msg}")
        print(f"[SCAN_WARN] {json.dumps({'t0': fmt_time(g['t0']), 't1': fmt_time(g['t1']), 'prev': g['prev'], 'next': g['next']}, ensure_ascii=False)}")
    if gaps:
        print(f"⚠️  빠진 판 의심 {len(gaps)}곳 — 위 구간의 영상을 확인하세요")


def _analyze_record_plays(plays: list, output_dir: Path, cancel_event=None) -> list:
    """신기록 분석을 별도 프로세스에서 돌리고 결과를 목록(DETECT)으로 낸다.

    PaddleOCR을 앱 프로세스의 작업 스레드에서 직접 돌렸더니 분석이 끝나고 스레드가 정리되는 시점에
    앱이 강제 종료됐다(pythonw, ntdll 0xC000071C). 스캔 워커처럼 프로세스를 나누면 OCR 쪽 문제가
    앱까지 번지지 않는다. 분석 프로세스가 실패하면 판 전체를 '판독 못 함'으로 목록에 남긴다.
    """
    import queue as _queue

    if not plays:
        print("ℹ️  결과 화면이 감지되지 않았습니다.")
        return []

    ctx  = multiprocessing.get_context("spawn")
    q    = ctx.Queue()
    proc = ctx.Process(target=_record_worker, args=(plays, str(output_dir), q), daemon=True)
    proc.start()

    kept, gaps, error = None, [], None
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                return []
            try:
                kind, value = q.get(timeout=0.5)
            except _queue.Empty:
                if proc.is_alive():
                    continue
                try:                                   # 마지막 메시지가 아직 큐에 남았을 수 있다
                    kind, value = q.get(timeout=1.0)
                except _queue.Empty:
                    error = "분석 프로세스가 결과 없이 종료되었습니다"
                    break
            if kind == "log":
                print(value, end="")
            elif kind == "result":
                kept, gaps = value
                break
            elif kind == "error":
                error = value
                break
    finally:
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()

    if kept is None:
        print(f"  ⚠️  신기록 분석 실패 — {error}. 감지된 {len(plays)}판을 모두 목록에 남깁니다.")
        kept = []
        for entry in plays:
            entry["new_record"] = None
            kept.append(entry)
    _emit_record_detections(kept)
    _emit_track_gaps(gaps)
    return kept


# ── OCR 페이즈 ────────────────────────────────────────────────────────────────

def _run_ocr_phase(
    history: list,
    url: str,
    output_dir: Path,
) -> tuple:
    """YOLO + song DB 초기화 → 결과 화면 프레임 배치 다운로드.

    Returns (ocr_yolo, song_titles, raw_songs, ocr_frames_map).
    실패 시 (None, [], [], {}) 반환.
    """
    try:
        # 스캔 때 이미 곡을 읽어 둔 항목(신기록 분석 모드)은 영상을 다시 받지 않는다
        all_ts = [e["timestamp"] for e in history if not e.get("_prefilled")]
        if not all_ts:
            return None, [], [], {}
        ocr_yolo = _init_yolo("cuda")
        from data.song_db import load_song_db
        song_titles, raw_songs = load_song_db(region="intl")
        ocr_frames_map: dict[float, list] = {}
        if ocr_yolo and song_titles:
            ocr_frames_map = _grab_all_result_frames(ocr_yolo, url, all_ts, output_dir)
        return ocr_yolo, song_titles, raw_songs, ocr_frames_map
    except Exception as e:
        print(f"  ⚠️  OCR 초기화 실패 — 곡명 추출 생략: {e}")
        return None, [], [], {}


# ── OCR 결과 처리 ─────────────────────────────────────────────────────────────

def _build_ocr_payload(
    history: list,
    ocr_yolo,
    song_titles: list,
    raw_songs: list,
    ocr_frames_map: dict,
    result_frames_dir: Path,
    payload_holder: list,
) -> None:
    """OCR 결과를 payload_holder에 채우고 best frame을 result_frames/ 에 저장."""
    import cv2

    result_frames_dir.mkdir(parents=True, exist_ok=True)
    n = len(history)
    for i, entry in enumerate(history):
        det_id    = entry.get("_detection_id", f"result_{i+1}")
        result_ts = entry["timestamp"]

        print(f"[OCR_PROG] {json.dumps({'done': i, 'total': n}, ensure_ascii=False)}")

        if entry.get("_prefilled"):
            payload_holder.append({
                "id":             det_id,
                "timestamp":      result_ts,
                "before":         entry.get("previous_rating", 0),
                "after":          entry.get("current_rating", 0),
                "change":         entry.get("change", 0),
                "song_title":     entry.get("song_title"),
                "difficulty":     entry.get("difficulty"),
                "achievement":    entry.get("achievement"),
                "rank":           entry.get("rank"),
                "internal_level": entry.get("internal_level"),
                "chart_type":     entry.get("chart_type"),
                "confidence":     entry.get("ocr_confidence", 0.0),
                "new_record":     entry.get("new_record"),
                "first_play":     bool(entry.get("first_play")),
                "record_delta":   entry.get("record_delta"),
                "record_exact":   entry.get("record_exact"),
            })
            continue

        ocr_frames, ocr_exact_ts = ocr_frames_map.get(result_ts, ([], None))

        ocr_result = None
        if ocr_yolo is not None and song_titles and ocr_frames:
            try:
                ocr_result = extract_from_frames(
                    ocr_frames, song_titles, raw_songs, fps=10, skip_sec=0.0,
                    video_ts=ocr_exact_ts,
                )
            except Exception as e:
                print(f"  ⚠️  OCR 곡 추출 실패 ({det_id}): {e}")

        if ocr_frames:
            try:
                cv2.imwrite(str(result_frames_dir / f"{det_id}.jpg"), ocr_frames[0])
            except Exception:
                pass

        good = ocr_result is not None and ocr_result.confidence > OCR_CONFIDENCE_MIN
        payload_holder.append({
            "id":             det_id,
            "timestamp":      result_ts,
            "before":         entry.get("previous_rating", 0),
            "after":          entry.get("current_rating", 0),
            "change":         entry.get("change", 0),
            "song_title":     ocr_result.title          if good else None,
            "difficulty":     ocr_result.difficulty     if good else None,
            "achievement":    ocr_result.achievement    if good else None,
            "rank":           ocr_result.rank           if good else None,
            "internal_level": ocr_result.internal_level if good else None,
            "chart_type":     ocr_result.chart_type     if good else None,
            "confidence":     ocr_result.confidence     if ocr_result else 0.0,
        })
    print(f"[OCR_PROG] {json.dumps({'done': n, 'total': n}, ensure_ascii=False)}")


def _auto_apply_ocr(payload: list, history: list) -> None:
    """OCR payload를 history 항목에 직접 적용."""
    id_to_entry = {e.get("_detection_id", f"result_{i+1}"): e for i, e in enumerate(history)}
    for item in payload:
        entry = id_to_entry.get(item["id"])
        if entry is None:
            continue
        if item.get("song_title"):
            entry["song_title"]     = item["song_title"]
            entry["difficulty"]     = item.get("difficulty")
            entry["achievement"]    = item.get("achievement")
            entry["rank"]           = item.get("rank")
            entry["internal_level"] = item.get("internal_level")
            entry["chart_type"]     = item.get("chart_type")


def _apply_ocr_to_entry(entry: dict, ocr_result) -> None:
    """SongResult를 history entry에 직접 적용 (신뢰도 검사 포함)."""
    if ocr_result is None or ocr_result.confidence <= OCR_CONFIDENCE_MIN:
        return
    entry["song_title"]     = ocr_result.title
    entry["difficulty"]     = ocr_result.difficulty
    entry["achievement"]    = ocr_result.achievement
    entry["rank"]           = ocr_result.rank
    entry["internal_level"] = ocr_result.internal_level
    entry["chart_type"]     = ocr_result.chart_type
    entry["ocr_confidence"] = ocr_result.confidence


def _run_download_and_lookback(
    history: list,
    url: str,
    output_dir: Path,
    max_lookback: float,
    num_lb_workers: int,
    cancel_event=None,
) -> tuple:
    """Phase 1+2: 병렬 다운로드 후 완료 즉시 역추적. (start_dl_map, lookback_map) 반환."""
    n          = len(history)
    DL_WORKERS = max(1, min(n, DL_WORKERS_MAX))
    print(f"\n▶ Phase 1+2 — 다운로드({DL_WORKERS}개 동시) + 완료 즉시 역추적({num_lb_workers}개 워커)")

    start_dl_map   = {}
    lookback_map   = {}
    lookback_async = {}

    ctx     = multiprocessing.get_context("spawn")
    lb_pool = ctx.Pool(num_lb_workers)
    try:
        with ThreadPoolExecutor(max_workers=DL_WORKERS) as dl_exec:
            dl_futures = {}
            for i, entry in enumerate(history):
                result_ts = entry["timestamp"]
                start_dl  = max(0.0, result_ts - max_lookback)
                end_dl    = result_ts + HIGHLIGHT_POST
                temp_file = output_dir / f"_temp_{i}.mp4"
                print(f"  [{i+1}/{n}] ↓ [{fmt_time(start_dl)} ~ {fmt_time(end_dl)}] 다운로드 시작")
                future = dl_exec.submit(_download_segment, start_dl, end_dl, temp_file, url)
                dl_futures[future] = (i, start_dl, result_ts, temp_file)

            dl_done = 0
            print(f"[DL_PROG] {json.dumps({'done': 0, 'total': n}, ensure_ascii=False)}")
            for future in as_completed(dl_futures):
                if cancel_event is not None and cancel_event.is_set():
                    break
                i, start_dl, result_ts, temp_file = dl_futures[future]
                ok = future.result()
                dl_done += 1
                if ok:
                    local_result_ts = result_ts - start_dl
                    print(f"  [{dl_done}/{n}] 다운로드 완료 → 역추적 제출")
                    print(f"[DL_PROG] {json.dumps({'done': dl_done, 'total': n}, ensure_ascii=False)}")
                    ar = lb_pool.apply_async(
                        _segment_lookback_worker,
                        ((i, str(temp_file), local_result_ts, max_lookback),)
                    )
                    lookback_async[i] = ar
                    start_dl_map[i]   = start_dl
                else:
                    print(f"  [{dl_done}/{n}] 다운로드 실패 — 건너뜀")
                    print(f"[DL_PROG] {json.dumps({'done': dl_done, 'total': n}, ensure_ascii=False)}")
                    start_dl_map[i] = None

        print("\n  역추적 완료 대기 중...")
        lb_done   = 0
        lb_total  = len(lookback_async)
        remaining = dict(lookback_async)   # idx -> AsyncResult
        # dict 순서로 항목마다 get(timeout=180)을 직렬 대기하면 앞선 항목이 매달릴 때
        # 이미 끝난 뒤 항목들이 굶으므로(최악 항목수×180s), 전체 데드라인 안에서 완료된 것부터 수거 (A-26)
        overall_deadline = time.time() + TIMEOUT_LOOKBACK_PER_ITEM + 30
        while remaining and time.time() < overall_deadline:
            if cancel_event is not None and cancel_event.is_set():
                break
            for idx in list(remaining):
                ar = remaining[idx]
                if not ar.ready():
                    continue
                del remaining[idx]
                lb_done += 1
                try:
                    ridx, local_play_ts, mode = ar.get(timeout=1)
                    lookback_map[ridx] = (local_play_ts, mode)
                    print(f"  [{lb_done}/{lb_total}] 역추적 완료")
                    # 시작 시간이 확정됐으므로 스캔결과 카드에 즉시 반영
                    if local_play_ts is not None:
                        actual_play_ts = local_play_ts + (start_dl_map.get(ridx) or 0.0)
                        det_id = history[ridx].get("_detection_id", f"result_{ridx+1}")
                        print(f"[DETECT_UPD] {json.dumps({'id': det_id, 'play_t': fmt_time(actual_play_ts), 'mode': mode}, ensure_ascii=False)}")
                except Exception as e:
                    print(f"  [{lb_done}/{lb_total}] 역추적 실패 ({e.__class__.__name__}) — 시작 지점 대체")
                    lookback_map[idx] = (None, None)
            if remaining:
                time.sleep(0.2)
        # 데드라인/취소로 남은 항목은 시작 지점 대체 처리
        for idx in remaining:
            lb_done += 1
            print(f"  [{lb_done}/{lb_total}] 역추적 미완료(타임아웃) — 시작 지점 대체")
            lookback_map[idx] = (None, None)
    finally:
        lb_pool.terminate()
        lb_pool.join()

    return start_dl_map, lookback_map


# ── VOD 전체 처리 ─────────────────────────────────────────────────────────────

def process_vod_entries(
    history: list,
    url: str,
    output_dir: Path,
    uploader=None,
    max_lookback: float = 360.0,
    ocr_event=None,
    confirm_event=None,
    confirmed_history_ref=None,
    skip_ocr_edit: bool = False,
    ocr_payload_holder=None,
    skip_ocr: bool = False,
    cancel_event=None,
    quality_ask=None,
) -> None:
    """① OCR + ② 병렬 다운로드/역추적 (동시 실행)  ③ OCR 확인 대기  ④ 클립 커팅 + 업로드."""

    # 역추적은 spawn 프로세스에서 실행 — GPU 유무에 따라 워커 배분이 달라진다
    from core.gpu import has_nvidia
    cuda_ok = has_nvidia()

    output_dir.mkdir(parents=True, exist_ok=True)
    result_frames_dir = output_dir / "result_frames"
    n = len(history)

    _payload = ocr_payload_holder if ocr_payload_holder is not None else []

    # ── Thread A: OCR (다운로드와 병렬 실행) ─────────────────────────────────
    def _ocr_thread_fn():
        # 예외로 daemon 스레드가 조용히 죽으면 ocr_event 미set → join이 타임아웃까지 소진되고
        # 빈 payload로 진행되므로, 무슨 일이 있어도 ocr_event 를 set 한다 (A-14)
        try:
            if not skip_ocr:
                ocr_yolo, song_titles, raw_songs, ocr_frames_map = _run_ocr_phase(history, url, output_dir)
                _build_ocr_payload(
                    history, ocr_yolo, song_titles, raw_songs,
                    ocr_frames_map, result_frames_dir, _payload,
                )
        except Exception as e:
            print(f"  ⚠️  OCR 처리 중 오류 — 곡명 없이 진행합니다: {e}")
        finally:
            if ocr_event is not None:
                ocr_event.set()

    ocr_th = threading.Thread(target=_ocr_thread_fn, daemon=True, name="ocr-phase")
    ocr_th.start()

    # ── Thread B: Phase 1+2 — 다운로드 + 역추적 ─────────────────────────────
    num_lb_workers = min(n, 4) if cuda_ok else min(n, multiprocessing.cpu_count())
    start_dl_map, lookback_map = _run_download_and_lookback(
        history, url, output_dir, max_lookback, num_lb_workers, cancel_event=cancel_event,
    )

    if not lookback_map:
        print("  ⚠️  다운로드 성공 항목 없음")
        ocr_th.join(timeout=30)
        if confirm_event is not None:
            confirm_event.set()
        return

    # ── OCR thread 완료 대기 ─────────────────────────────────────────────────
    # join은 다운로드 데드라인(BASE+PER*n)에 더해 곡명 분석 시간(항목당 15s)까지 커버해야
    # 다운로드가 데드라인에 걸린 뒤에도 이미 받은 클립의 OCR 결과가 유실되지 않는다.
    print("\n  OCR 분석 완료 대기 중...")
    ocr_th.join(timeout=TIMEOUT_OCR_BATCH_BASE + (TIMEOUT_OCR_BATCH_PER + 15) * n)

    # ── OCR 확인 처리 ─────────────────────────────────────────────────────────
    if confirm_event is not None:
        if skip_ocr_edit:
            if confirmed_history_ref is not None:
                _auto_apply_ocr(_payload, confirmed_history_ref)
            confirm_event.set()
        else:
            print(f"[OCR_DONE] {json.dumps(_payload, ensure_ascii=False)}")
            confirm_event.wait(timeout=TIMEOUT_OCR_EDIT_WAIT)
    else:
        _auto_apply_ocr(_payload, history)

    # 중단 요청 시 Phase 3(커팅+업로드) 진입 안 함 — confirm_event.wait이 취소로 풀린 경우 포함
    if cancel_event is not None and cancel_event.is_set():
        print("  🛑  중단 요청 — 클립 커팅/업로드를 건너뜁니다.")
        return

    # ── Phase 3 — 클립 커팅 + 업로드 ─────────────────────────────────────────
    final_history = confirmed_history_ref if confirmed_history_ref is not None else history
    print(f"\n▶ Phase 3 — 클립 커팅 + 업로드 ({len(final_history)}개)")
    stream_start, start_precise = fetch_stream_start(url)
    _cut_and_upload_clips(
        final_history, url, output_dir, uploader,
        start_dl_map, lookback_map, cancel_event=cancel_event,
        stream_start=stream_start, stream_start_precise=start_precise,
        quality_ask=quality_ask,
    )


# ── 라이브 Phase 2 ────────────────────────────────────────────────────────────

def process_live_clips(
    history: list,
    output_dir: Path,
    uploader=None,
    ocr_event=None,
    confirm_event=None,
    confirmed_history_ref=None,
    skip_ocr_edit: bool = False,
    ocr_payload_holder=None,
    song_ocr: bool = True,
    cancel_event=None,
) -> None:
    """라이브 Phase 2: Phase 1에서 저장된 클립에 대해 OCR 편집 + 이름 변경 + 업로드.

    클립 다운로드/역추적/커팅은 이미 Phase 1에서 완료됐으므로 수행하지 않는다.
    """
    import cv2
    from core.clip_builder import build_clip_metadata, clip_filename, _save_clip_meta, refresh_pending_memo
    from core.scanner_parallel import fmt_time

    # 곡 정보 추출 — Phase 1에서 저장된 결과 화면 이미지로 분석
    if song_ocr:
        from data.song_db import load_song_db
        from core.result_extractor import extract_from_frames
        from config.settings import OCR_CONFIDENCE_MIN
        song_titles, raw_songs = load_song_db(region="intl")
        result_frames_dir = output_dir / "result_frames"
        print(f"\n🔍  곡 정보 분석 중... ({len(history)}개)")
        for entry in history:
            det_id     = entry.get("_detection_id", "")
            frame_path = result_frames_dir / f"{det_id}.jpg"
            if not frame_path.exists():
                continue
            try:
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    continue
                ocr_result = extract_from_frames(
                    [frame], song_titles, raw_songs, fps=10,
                    video_ts=entry.get("timestamp"),
                )
                _apply_ocr_to_entry(entry, ocr_result)
                if entry.get("song_title"):
                    ach_str = f"{ocr_result.achievement:.4f}%" if ocr_result.achievement is not None else "-"
                    print(f"    🔍  {ocr_result.title} {ocr_result.difficulty} {ocr_result.rank or '?'} {ach_str}")
            except Exception as e:
                print(f"    ⚠️  OCR 실패 ({det_id}): {e}")

    _payload = ocr_payload_holder if ocr_payload_holder is not None else []
    n = len(history)

    # OCR payload 구성
    for i, entry in enumerate(history):
        det_id = entry.get("_detection_id", f"result_{i+1}")
        _payload.append({
            "id":             det_id,
            "timestamp":      entry.get("timestamp", 0),
            "before":         entry.get("previous_rating", 0),
            "after":          entry.get("current_rating", 0),
            "change":         entry.get("change", 0),
            "song_title":     entry.get("song_title"),
            "difficulty":     entry.get("difficulty"),
            "achievement":    entry.get("achievement"),
            "rank":           entry.get("rank"),
            "internal_level": entry.get("internal_level"),
            "chart_type":     entry.get("chart_type"),
            "confidence":     entry.get("ocr_confidence", 0.0),
        })

    # OCR 스레드 완료 신호 (process_vod_entries 와 인터페이스 통일)
    if ocr_event is not None:
        ocr_event.set()

    # OCR 편집 대기 or 자동 적용
    if confirm_event is not None:
        if skip_ocr_edit:
            _auto_apply_ocr(_payload, confirmed_history_ref or history)
            confirm_event.set()
        else:
            print(f"[OCR_DONE] {json.dumps(_payload, ensure_ascii=False)}")
            confirm_event.wait(timeout=TIMEOUT_OCR_EDIT_WAIT)
    else:
        _auto_apply_ocr(_payload, history)

    # 클립 이름 변경 + 메타 저장 + 업로드
    final_history = confirmed_history_ref if confirmed_history_ref is not None else history
    pending_uploads: list = []
    print(f"\n▶ 라이브 Phase 2 — 클립 처리 + 업로드 ({len(final_history)}개)")

    for i, entry in enumerate(final_history):
        if cancel_event is not None and cancel_event.is_set():
            print("  🛑  중단 요청 — 남은 클립 업로드를 중단합니다.")
            break
        clip_path = Path(entry.get("clip_path", ""))
        if not clip_path.exists():
            print(f"    [{i+1}/{n}] ⚠️  클립 파일 없음: {clip_path.name}")
            continue

        title, desc = build_clip_metadata(entry)
        final_file  = output_dir / clip_filename(entry, title, i)

        try:
            clip_path.rename(final_file)
        except Exception:
            final_file = clip_path

        size_mb  = round(final_file.stat().st_size / 1024 / 1024, 1) if final_file.exists() else 0
        change   = entry.get("change", 0)
        mode     = entry.get("mode")
        result_t = entry.get("timestamp", 0)
        det_id   = entry.get("_detection_id", "")
        print(f"    [{i+1}/{n}] 💾  {final_file.name}")
        print(f"[HL_ADD] {json.dumps({'id': det_id, 'file': final_file.name, 't': fmt_time(result_t), 'mode': mode, 'delta': change, 'size': f'{size_mb} MB', 'status': 'queued'}, ensure_ascii=False)}")

        _save_clip_meta(final_file, title, desc)

        if uploader:
            video_id = uploader.upload(final_file, title, desc)
            if video_id:
                try:
                    final_file.unlink(missing_ok=True)
                    final_file.with_suffix(".json").unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                pending_uploads.append(final_file.name)

    memo = refresh_pending_memo(output_dir, getattr(uploader, "last_error", None))
    if pending_uploads:
        print("\n🚨  업로드 실패 영상 — highlights/ 폴더에서 수동으로 업로드하세요:")
        for name in pending_uploads:
            print(f"    {name}")
    if memo:
        print(f"📝  제목·설명 메모: highlights/{memo.name}")
