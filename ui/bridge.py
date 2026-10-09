"""QWebChannel bridge — Python ↔ JS 양방향 통신."""
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from config.settings import PROJECT_DIR
from core.error_messages import translate_error, log_error
from core.scanner_parallel import fmt_time
from ui.workers import CheckWorker, PipelineWorker

# ── 로그 파싱 패턴 ────────────────────────────────────────────────────────────

_RE_ANSI      = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
_RE_DL_TICK   = re.compile(r'다운로드 중\.+\s*\d+')
_RE_UPL_PCT   = re.compile(r'업로드\s+\d+\s*%')
_RE_WORKER    = re.compile(r'\bW\d+\s*\[')
_RE_STEP      = re.compile(r'^\[(\d+)/(\d+)\]')
_RE_OCR_ITEM  = re.compile(r'\[OCR\]\s*(\d+)s\s*분석\s*중')
_RE_OCR_BATCH = re.compile(r'OCR.*?(\d+)개\s*구간')

# (phase_id, keywords) — 첫 번째 매칭 우선
_PHASE_MAP = [
    ("fetch",    ["스트림 URL", "스트리밍 주소", "영상 정보 수집"]),
    ("download", ["Phase 1+2"]),
    ("ocr",      ["OCR 분석 완료 대기"]),
    ("cut",      ["Phase 3", "클립 커팅"]),
    ("monitor",  ["실시간 레이팅", "ffmpeg 롤링", "라이브 모니터"]),
]


# 로그 레벨 키워드
_LVL_OK   = ["✅", "완료", "업로드 완료"]
_LVL_WARN = ["⚠️", "WARNING", "타임아웃", "끊김", "재연결"]
_LVL_ERR  = ["❌", "🚨", "오류", "실패", "ERROR"]
_LVL_HL   = ["🎯", "레이팅 변동"]

_RE_DATE_PREFIX = re.compile(r"^\d{8}_(?:\d{4}|\d{2})_")


def _read_clip_meta(clip: Path) -> tuple:
    """클립 옆 json 에서 (제목, 설명). 없으면 파일명으로 대신한다."""
    try:
        data  = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8"))
        title = data.get("title", "")
        if title:
            return title, data.get("description", "")
    except Exception:
        pass
    title = _RE_DATE_PREFIX.sub("", clip.stem)[:100] or "[maimai DX]"
    return title, f"재업로드: {clip.name}"


def _remove_clip(clip: Path) -> None:
    """업로드가 끝난 클립과 메타를 지운다 (메인 흐름과 같은 동작)."""
    for f in (clip, clip.with_suffix(".json")):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _pending_clips(folder: Path) -> list:
    """highlights/ 에 남은(업로드 안 된) 클립 목록. 오래된 것부터."""
    out = []
    try:
        clips = sorted((f for f in folder.glob("*.mp4") if not f.name.startswith("_temp")),
                       key=lambda f: f.stat().st_mtime)
        for f in clips:
            st = f.stat()
            out.append({
                "file":  f.name,
                "title": _read_clip_meta(f)[0],
                "size":  round(st.st_size / 1024 / 1024, 1),
                "time":  datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            })
    except Exception:
        pass
    return out


def _detect_level(text: str) -> str:
    if any(k in text for k in _LVL_OK):   return "ok"
    if any(k in text for k in _LVL_WARN): return "warn"
    if any(k in text for k in _LVL_ERR):  return "err"
    if any(k in text for k in _LVL_HL):   return "hl"
    return "info"


# 고화질 다운로드 실패 후 사용자가 선택하기를 기다리는 시간(초). 지나면 낮은 화질로 진행한다.
_QUALITY_ASK_TIMEOUT = 180

class Bridge(QObject):
    # ── Python → JS 시그널 ────────────────────────────────────────────────────
    status_result     = pyqtSignal(str)   # JSON VideoStatus
    status_error      = pyqtSignal(str)   # error string
    log_line          = pyqtSignal(str)   # JSON {t, lvl, msg}
    detection_added   = pyqtSignal(str)   # JSON Detection
    detection_updated = pyqtSignal(str)   # JSON {id, play_t}
    highlight_added   = pyqtSignal(str)   # JSON Highlight
    highlight_updated = pyqtSignal(str)   # JSON {file, ...updates}
    phase_update      = pyqtSignal(str)   # JSON {phase, step, total, msg}
    scan_finished     = pyqtSignal()
    scan_done         = pyqtSignal(str)   # JSON {count} — 스캔 완료, 클립 생성 대기 중
    quality_warning   = pyqtSignal(str)   # JSON {from, to, clip, reason, ask?, timeout?} — 고화질 다운로드 실패 (ask 면 사용자 선택을 기다림)
    quality_resolved  = pyqtSignal(str)   # JSON {choice, auto} — 선택이 끝남(시간 초과 포함) → 경고창을 닫는다
    scan_warning      = pyqtSignal(str)   # JSON {t0, t1, prev, next} — TRACK 번호가 건너뛴 구간 (판을 놓쳤을 수 있음)
    pipeline_error    = pyqtSignal(str)
    pipeline_stopped  = pyqtSignal()
    env_check_result  = pyqtSignal(str)   # JSON list[dict]
    ocr_ready         = pyqtSignal(str)   # JSON list[OcrItem] — OCR 편집 화면 표시 요청
    app_update_result = pyqtSignal(str)   # JSON {current, latest, url, newer}
    pending_changed   = pyqtSignal()      # 업로드 대기 클립 목록이 바뀜 — JS 가 다시 읽는다

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker        = None
        self._check_worker  = None
        self._env_worker    = None
        self._retry_workers = []
        self._failed_files  = []
        self._phase_current   = "idle"
        self._phase_step      = 0
        self._phase_total     = 0
        self._scan_start_time = 0.0
        self._worker_progress = {}   # start_clipping 경로(스캔 없이 클립만)에서도 참조되므로 여기서 초기화
        self._dl_done         = 0
        self._dl_total        = 0
        self._ocr_done        = 0
        self._ocr_total       = 0
        self._last_history    = []
        self._last_url        = ""
        self._song_titles: list = []
        self._raw_songs:   list = []
        self._song_db_loaded    = False
        self._auto_upload     = True
        self._ocr_event          = None
        self._confirm_event      = None
        self._cancel_event       = None
        self._confirmed_history  = []
        self._ocr_payload_holder = []
        self._live_monitor       = None
        self._live_phase         = None   # "collecting" | None

    # ── JS → Python 슬롯 ──────────────────────────────────────────────────────

    @pyqtSlot(str)
    def check_status(self, url: str):
        if self._check_worker and self._check_worker.isRunning():
            self._check_worker.result.disconnect()
            self._check_worker.failed.disconnect()
            self._check_worker.terminate()
        self._check_worker = CheckWorker(url)
        self._check_worker.result.connect(self.status_result.emit)
        self._check_worker.failed.connect(self.status_error.emit)
        self._check_worker.start()

    @pyqtSlot(str)
    def start_pipeline(self, config_json: str):
        if self._worker and self._worker.isRunning():
            self._worker.stop()

        cfg         = json.loads(config_json)
        url         = cfg["url"]
        rating      = int(cfg.get("startRating", 0))
        t_start     = cfg.get("tStart", "")
        t_end       = cfg.get("tEnd", "")
        auto_upload = cfg.get("autoUpload", True)
        n_workers   = int(cfg.get("workers", 0))
        is_live     = cfg.get("isLive", False)
        buffer_min  = int(cfg.get("buffer", 6))
        self._auto_upload = auto_upload
        song_ocr    = cfg.get("songOcr", True)
        record_mode = bool(cfg.get("recordMode", False)) and not is_live   # 신기록 분석 모드는 VOD 전용

        from config.settings import CACHE_DIR
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / "mai_youtube_url.txt").write_text(url, encoding="utf-8")
        (PROJECT_DIR / "highlights").mkdir(exist_ok=True)

        self._cancel_event    = threading.Event()
        self._failed_files    = []
        self._phase_current   = "fetch"
        self._phase_step      = 0
        self._phase_total     = 0
        self._worker_progress = {}
        self._dl_done         = 0
        self._dl_total        = 0
        self._ocr_done        = 0
        self._ocr_total       = 0
        self._scan_start_time = 0.0
        self._last_history    = []
        self._last_url        = ""
        self._emit_phase("파이프라인 시작...")

        from core.youtube_uploader import YouTubeUploader
        from core.pipeline import analyze_vod_stream, process_vod_entries
        from core.live_monitor import LiveMonitor
        from core.scanner_parallel import parse_time

        output_dir = PROJECT_DIR / "highlights"
        uploader   = YouTubeUploader() if auto_upload else None

        def _parse_time_opt(text):
            t = text.strip()
            if not t:
                return None
            try:
                return parse_time(t)
            except Exception:
                return None

        if is_live:
            def _run_live():
                monitor = LiveMonitor(
                    url=url, output_dir=output_dir, uploader=uploader,
                    buffer_minutes=buffer_min, initial_rating=rating,
                    skip_ocr=not song_ocr,
                )
                self._live_monitor = monitor
                self._live_phase   = "collecting"

                history = monitor.start()  # stop() 호출 시 history 반환

                self._live_phase   = None
                self._live_monitor = None

                if not history:
                    print("ℹ️  감지된 레이팅 변동 없음 — 클립 추출 단계를 건너뜁니다.")
                    return

                self._last_history = history
                self._last_url     = url
                print(f"[SCAN_DONE] {json.dumps({'count': len(history)}, ensure_ascii=False)}")
        else:
            start_sec = _parse_time_opt(t_start)
            end_sec   = _parse_time_opt(t_end)

            def _run_vod():
                history = analyze_vod_stream(
                    url=url, start_sec=start_sec or 0.0, end_sec=end_sec,
                    initial_rating=rating, num_workers=n_workers,
                    output_file=None, cancel_event=self._cancel_event,
                    record_mode=record_mode, output_dir=output_dir,
                )
                if not history:
                    print("ℹ️  감지된 신기록 없음 — 클립 추출 단계를 건너뜁니다." if record_mode
                          else "ℹ️  감지된 레이팅 변동 없음 — 클립 추출 단계를 건너뜁니다.")
                    return
                self._last_history = history
                self._last_url     = url
                print(f"[SCAN_DONE] {json.dumps({'count': len(history)}, ensure_ascii=False)}")

        self._worker = PipelineWorker(_run_live if is_live else _run_vod)
        self._worker.log.connect(self._on_log)
        self._worker.done.connect(self.scan_finished.emit)
        self._worker.failed.connect(self._on_pipeline_error)
        self._worker.start()

    @pyqtSlot()
    def stop_pipeline(self):
        if self._live_phase == "collecting":
            # Phase 1: 수집만 중단, 워커는 살려서 Phase 2 (클립 선택) 로 전환
            if self._live_monitor is not None:
                self._live_monitor.stop()
            return  # pipeline_stopped 미발생 — UI 는 scan_done 신호로 전환됨

        # Phase 2 또는 VOD: 완전 중단
        if self._cancel_event:
            self._cancel_event.set()   # 스캔 루프에 협조적 취소 신호 → 워커 프로세스 정리
        if self._confirm_event:
            self._confirm_event.set()
        if self._live_monitor is not None:
            self._live_monitor.stop()
            self._live_monitor = None
        if self._worker and self._worker.isRunning():
            # 취소로 워커가 정상 리턴하며 done→scan_finished("done")을 쏘면
            # pipeline_stopped("idle")를 덮어써 "완료"로 오인됨 → done 연결 해제
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
            self._worker.stop(cleanup_fn=self._cleanup_temp_files)
        self.pipeline_stopped.emit()

    @pyqtSlot(str)
    def retry_upload(self, file_name: str):
        file_path = PROJECT_DIR / "highlights" / file_name
        if not file_path.exists():
            return

        from core.youtube_uploader import YouTubeUploader
        uploader = YouTubeUploader()

        def _do_retry():
            title, desc = _read_clip_meta(file_path)
            if uploader.upload(file_path, title, desc):
                _remove_clip(file_path)
            # 목록은 폴더에 남은 클립 기준이라, 업로드가 끝났든 실패했든 다시 만든다
            from core.clip_builder import refresh_pending_memo
            refresh_pending_memo(file_path.parent, getattr(uploader, "last_error", None))
            self.pending_changed.emit()

        self._retry_workers = [w for w in self._retry_workers if w.isRunning()]
        worker = PipelineWorker(_do_retry)
        worker.log.connect(self._on_log)
        self._retry_workers.append(worker)
        worker.start()

    @pyqtSlot(result=str)
    def list_pending_uploads(self) -> str:
        """이전 실행에서 업로드하지 못하고 highlights/ 에 남은 클립 (JSON 배열)."""
        return json.dumps(_pending_clips(PROJECT_DIR / "highlights"), ensure_ascii=False)

    @pyqtSlot(str)
    def upload_pending(self, files_json: str):
        """남은 클립을 업로더 하나로 차례대로 올린다. 한도에 걸리면 나머지는 시도 없이 실패로 둔다."""
        if self._worker and self._worker.isRunning():
            self._emit_log("warn", "⚠️ 분석이 진행 중입니다. 끝난 뒤에 업로드하세요.")
            return
        try:
            names = [n for n in json.loads(files_json) if isinstance(n, str)]
        except Exception:
            return
        if not names:
            return

        from core.youtube_uploader import YouTubeUploader
        folder = PROJECT_DIR / "highlights"

        def _run():
            uploader = YouTubeUploader()
            try:
                for name in names:
                    clip = folder / Path(name).name          # 폴더 밖 경로는 쓰지 않는다
                    if clip.suffix.lower() != ".mp4" or not clip.exists():
                        continue
                    title, desc = _read_clip_meta(clip)
                    if uploader.upload(clip, title, desc):
                        _remove_clip(clip)
            finally:
                from core.clip_builder import refresh_pending_memo
                refresh_pending_memo(folder, getattr(uploader, "last_error", None))
                self.pending_changed.emit()

        worker = PipelineWorker(_run)
        worker.log.connect(self._on_log)
        self._retry_workers = [w for w in self._retry_workers if w.isRunning()]
        self._retry_workers.append(worker)
        worker.start()

    @pyqtSlot()
    def retry_all_failed(self):
        for f in list(self._failed_files):
            self.retry_upload(f)
        self._failed_files = []

    @pyqtSlot(str)
    def start_clipping(self, selected_ids_json: str):
        """GUI에서 선택된 detection id 목록을 받아 해당 history 항목만 process_vod_entries()에 전달."""
        if self._worker and self._worker.isRunning():
            return

        payload      = json.loads(selected_ids_json)
        if isinstance(payload, list):
            selected_ids = set(payload)
            song_ocr     = True
        else:
            selected_ids = set(payload.get("ids", []))
            song_ocr     = bool(payload.get("songOcr", True))

        selected_history = []
        for i, entry in enumerate(self._last_history):
            did = f"result_{i+1}"
            if did in selected_ids:
                entry = dict(entry)
                entry["_detection_id"] = did
                selected_history.append(entry)

        if not selected_history:
            self._emit_log("warn", "선택된 항목이 없습니다.")
            return

        from config.settings import load_user_config
        user_cfg      = load_user_config()
        skip_ocr_edit = not user_cfg.get("ocrEdit", True)

        from core.youtube_uploader import YouTubeUploader
        from core.pipeline import process_vod_entries, process_live_clips
        from core.clip_builder import QualityAsker

        output_dir = PROJECT_DIR / "highlights"
        uploader   = YouTubeUploader() if self._auto_upload else None
        url        = self._last_url

        self._ocr_event          = threading.Event()
        self._confirm_event      = threading.Event()
        self._cancel_event       = threading.Event()
        self._quality_asker      = QualityAsker(self._cancel_event, _QUALITY_ASK_TIMEOUT)
        self._confirmed_history  = selected_history
        self._ocr_payload_holder = []

        ocr_event             = self._ocr_event
        confirm_event         = self._confirm_event
        cancel_event          = self._cancel_event
        confirmed_history_ref = selected_history
        ocr_payload_holder    = self._ocr_payload_holder

        # 라이브 항목은 clip_path 가 있음 (Phase 1에서 저장됨)
        is_live_mode = any(e.get("clip_path") for e in selected_history)

        quality_asker = self._quality_asker

        def _run_clipping():
            print(f"\n🔎  {len(selected_history)}건 선택 — 클립 추출 시작...")
            if is_live_mode:
                process_live_clips(
                    history=selected_history,
                    output_dir=output_dir,
                    uploader=uploader,
                    ocr_event=ocr_event,
                    confirm_event=confirm_event,
                    confirmed_history_ref=confirmed_history_ref,
                    skip_ocr_edit=skip_ocr_edit,
                    ocr_payload_holder=ocr_payload_holder,
                    song_ocr=user_cfg.get("songOcr", True),
                    cancel_event=cancel_event,
                )
            else:
                process_vod_entries(
                    history=selected_history,
                    url=url,
                    output_dir=output_dir,
                    uploader=uploader,
                    ocr_event=ocr_event,
                    confirm_event=confirm_event,
                    confirmed_history_ref=confirmed_history_ref,
                    skip_ocr_edit=skip_ocr_edit,
                    ocr_payload_holder=ocr_payload_holder,
                    skip_ocr=not song_ocr,
                    cancel_event=cancel_event,
                    quality_ask=quality_asker,
                )

        self._worker = PipelineWorker(_run_clipping)
        self._worker.log.connect(self._on_log)
        self._worker.done.connect(self.scan_finished.emit)
        self._worker.failed.connect(self._on_pipeline_error)
        self._worker.start()

    @pyqtSlot(str)
    def answer_quality(self, choice: str):
        """JS 화질 경고창에서 '다시 시도'(retry) / '낮은 화질로 진행'(lower)을 눌렀을 때."""
        asker = getattr(self, "_quality_asker", None)
        if asker is not None:
            asker.answer(choice)

    @pyqtSlot(str)
    def confirm_ocr(self, edited_json: str):
        """JS에서 OCR 편집 완료 후 호출 — 편집 데이터를 history에 적용하고 파이프라인 재개."""
        try:
            edited = json.loads(edited_json)
        except Exception:
            # 편집 데이터 파싱 실패 — confirm_event.wait에서 1시간 블로킹되지 않도록 미편집 상태로 재개
            if self._confirm_event:
                self._confirm_event.set()
            return

        id_to_entry = {
            e.get("_detection_id", f"result_{i+1}"): e
            for i, e in enumerate(self._confirmed_history)
        }
        for item in edited:
            entry = id_to_entry.get(item.get("id"))
            if entry is None:
                continue
            entry["song_title"]     = item.get("song_title") or None
            entry["difficulty"]     = item.get("difficulty") or None
            entry["achievement"]    = item.get("achievement")
            entry["rank"]           = item.get("rank") or None
            entry["internal_level"] = item.get("internal_level")

        if self._confirm_event:
            self._confirm_event.set()

    def _ensure_song_db(self):
        if not self._song_db_loaded:
            from data.song_db import load_song_db
            self._song_titles, self._raw_songs = load_song_db()
            self._song_db_loaded = True

    @pyqtSlot(str, result=str)
    def search_songs(self, query: str) -> str:
        """query를 포함하는 곡명 최대 10개를 JSON 배열로 반환."""
        self._ensure_song_db()
        q = query.strip().lower()
        if not q:
            return "[]"
        matches = [t for t in self._song_titles if q in t.lower()][:10]
        return json.dumps(matches, ensure_ascii=False)

    @pyqtSlot(str, str, str, result=str)
    def lookup_internal_level(self, title: str, difficulty: str, chart_type: str) -> str:
        """곡명+난이도(+표준/DX)로 내부 레벨 조회. 없으면 빈 문자열 반환."""
        self._ensure_song_db()
        from data.song_db import get_internal_level
        level = get_internal_level(self._raw_songs, title, difficulty, chart_type=chart_type or None)
        return str(level) if level is not None else ""

    @pyqtSlot(str)
    def open_result_frame(self, det_id: str):
        """결과 프레임 이미지를 OS 기본 뷰어로 열기."""
        import os
        frame_path = PROJECT_DIR / "highlights" / "result_frames" / f"{det_id}.jpg"
        if frame_path.exists():
            os.startfile(str(frame_path))

    @pyqtSlot(str)
    def open_clip(self, file_name: str):
        """생성된 하이라이트 클립을 OS 기본 플레이어로 열기 (없으면 무시 — 업로드 후 삭제된 경우)."""
        import os
        clip_path = PROJECT_DIR / "highlights" / file_name
        if clip_path.exists():
            os.startfile(str(clip_path))

    @pyqtSlot(str, result=str)
    def get_result_frame_url(self, det_id: str) -> str:
        """결과 프레임 파일의 로컬 file:// URL 반환."""
        from PyQt5.QtCore import QUrl
        frame_path = PROJECT_DIR / "highlights" / "result_frames" / f"{det_id}.jpg"
        if frame_path.exists():
            return QUrl.fromLocalFile(str(frame_path)).toString()
        return ""

    @pyqtSlot(str)
    def save_settings(self, settings_json: str):
        """사용자 설정을 user_config.json에 저장."""
        try:
            from config.settings import USER_CONFIG_PATH
            data = json.loads(settings_json)
            USER_CONFIG_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self._emit_log("warn", f"설정 저장 실패: {e}")

    @pyqtSlot(result=str)
    def load_settings(self) -> str:
        """저장된 사용자 설정을 JSON으로 반환."""
        from config.settings import load_user_config
        return json.dumps(load_user_config(), ensure_ascii=False)

    @pyqtSlot()
    def run_env_check(self):
        """환경 점검 요청 — JS에서 호출."""
        from core.env_check import check_environment

        def _run():
            results = check_environment()
            self.env_check_result.emit(json.dumps(results, ensure_ascii=False))

        w = PipelineWorker(_run)
        w.start()
        self._env_worker = w

    @pyqtSlot()
    def reauthenticate_youtube(self):
        """YouTube OAuth 재인증 — 브라우저 창을 열고 완료 후 환경 점검 재실행."""
        from core.env_check import check_environment

        def _run():
            try:
                self._emit_log("info", "🔑 YouTube 재인증 중... 브라우저에서 인증을 완료하세요.")
                from core.youtube_uploader import YouTubeUploader
                YouTubeUploader().authenticate()
                self._emit_log("ok", "✅ YouTube 재인증 완료")
            except Exception as e:
                self._emit_log("err", f"❌ YouTube 재인증 실패: {e}")
            finally:
                results = check_environment()
                self.env_check_result.emit(json.dumps(results, ensure_ascii=False))

        w = PipelineWorker(_run)
        w.start()
        self._retry_workers.append(w)

    @pyqtSlot()
    def update_jacket_index(self):
        """자켓 인덱스 갱신 — 곡 DB에 새로 추가된 곡의 자켓만 내려받는다."""
        from core.env_check import check_environment

        def _run():
            try:
                from core import jacket_index
                from data.song_db import load_song_db
                _titles, raw = load_song_db("intl")
                n = jacket_index.pending(raw)
                if n == 0:
                    self._emit_log("ok", "✅ 자켓 인덱스가 이미 최신입니다.")
                else:
                    self._emit_log("info", f"⬇️  새 곡 자켓 {n}개 다운로드 중...")
                    hashes, _feats = jacket_index.ensure_index(raw, quiet=True)
                    self._emit_log("ok", f"✅ 자켓 인덱스 갱신 완료 (총 {len(hashes)}곡)")
            except Exception as e:
                self._emit_log("err", f"❌ 자켓 인덱스 갱신 실패: {e}")
            finally:
                results = check_environment()
                self.env_check_result.emit(json.dumps(results, ensure_ascii=False))

        w = PipelineWorker(_run)
        w.start()
        self._retry_workers.append(w)

    @pyqtSlot()
    def check_app_update(self):
        """GitHub 최신 릴리스를 조회해 새 버전이 있으면 알린다. 실패하면 아무 일도 없다."""
        def _run():
            from core.app_update import check_latest
            info = check_latest()
            if info and info.get("newer"):
                self.app_update_result.emit(json.dumps(info, ensure_ascii=False))

        w = PipelineWorker(_run)
        w.start()
        self._retry_workers.append(w)

    @pyqtSlot(str)
    def open_release_page(self, url: str):
        """릴리스 페이지를 기본 브라우저로 연다. 이 저장소 주소만 연다."""
        from core.app_update import is_release_url
        if is_release_url(url):
            import webbrowser
            webbrowser.open(url)

    @pyqtSlot(result=str)
    def get_version(self) -> str:
        from config.version import APP_VERSION
        return APP_VERSION

    @pyqtSlot()
    def update_ytdlp(self):
        """yt-dlp를 최신 버전으로 갱신 — YouTube 변경으로 다운로드가 막힐 때 필요."""
        from core.env_check import check_environment

        def _run():
            try:
                self._emit_log("info", "⬆️  yt-dlp 업데이트 중...")
                kwargs = {"capture_output": True, "text": True, "timeout": 300}
                if sys.platform == "win32":
                    kwargs["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
                r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], **kwargs)
                if r.returncode == 0:
                    self._emit_log("ok", "✅ yt-dlp 업데이트 완료 — 프로그램을 다시 시작하면 적용됩니다.")
                else:
                    tail = (r.stderr or r.stdout or "").strip().splitlines()
                    self._emit_log("err", f"❌ yt-dlp 업데이트 실패: {tail[-1] if tail else '알 수 없는 오류'}")
            except Exception as e:
                self._emit_log("err", f"❌ yt-dlp 업데이트 실패: {e}")
            finally:
                results = check_environment()
                self.env_check_result.emit(json.dumps(results, ensure_ascii=False))

        w = PipelineWorker(_run)
        w.start()
        self._retry_workers.append(w)

    @pyqtSlot()
    def quit_app(self):
        from PyQt5.QtWidgets import QApplication
        QApplication.quit()

    @pyqtSlot()
    def open_highlights_folder(self):
        folder = PROJECT_DIR / "highlights"
        folder.mkdir(exist_ok=True)
        subprocess.Popen(["explorer", str(folder.resolve())])

    @pyqtSlot()
    def open_analysis_folder(self):
        folder = PROJECT_DIR / "highlights" / "result_frames"
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(folder.resolve())])

    @pyqtSlot(result=str)
    def get_last_url(self) -> str:
        from config.settings import CACHE_DIR
        p = CACHE_DIR / "mai_youtube_url.txt"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _emit_log(self, lvl: str, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_line.emit(json.dumps({"t": now, "lvl": lvl, "msg": msg}, ensure_ascii=False))

    def _emit_phase(self, msg: str):
        self.phase_update.emit(json.dumps({
            "phase": self._phase_current,
            "step":  self._phase_step,
            "total": self._phase_total,
            "msg":   msg,
        }, ensure_ascii=False))

    def _calc_eta(self, avg_pct: float) -> str:
        """평균 진행률과 경과 시간으로 남은 시간 문자열 반환."""
        if avg_pct < 5 or self._scan_start_time <= 0:
            return ""
        elapsed   = time.time() - self._scan_start_time
        if elapsed < 30:
            return ""
        total_est = elapsed / (avg_pct / 100.0)
        remaining = total_est - elapsed
        if remaining <= 0:
            return ""
        if remaining < 60:
            return f"  (약 {int(remaining)}초 남음)"
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        return f"  (약 {mins}분 {secs:02d}초 남음)"

    def _cleanup_temp_files(self):
        try:
            for pattern in ("_temp_*.mp4", "_hq_*.mp4"):
                for f in (PROJECT_DIR / "highlights").glob(pattern):
                    f.unlink(missing_ok=True)
        except Exception:
            pass

    def _on_pipeline_error(self, msg: str):
        try:
            logs_dir = PROJECT_DIR / "logs"
            logs_dir.mkdir(exist_ok=True)
            with open(logs_dir / "error.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [pipeline]\n{msg}\n")
        except Exception:
            pass
        korean = translate_error(msg)
        self._emit_log("err", korean)
        self.pipeline_error.emit(korean)

    def _on_log(self, line: str):
        # 잘못되거나 잘린 IPC 라인 하나가 시그널 슬롯에서 예외로 터져 워커를 죽이지 않도록 방어
        try:
            self._dispatch_log(line)
        except Exception as exc:
            log_error(exc, context="_on_log")

    def _dispatch_log(self, line: str):
        # ── IPC 구조화 메시지 — 로그에 표시하지 않음 ──────────────────────────
        if line.startswith("[DETECT]"):
            self.detection_added.emit(line[8:].strip())
            return
        if line.startswith("[DETECT_UPD]"):
            self.detection_updated.emit(line[12:].strip())
            return
        if line.startswith("[HL_ADD]"):
            self.highlight_added.emit(line[8:].strip())
            return
        if line.startswith("[HL_UPD]"):
            data = json.loads(line[8:].strip())
            if data.get("status") == "failed":
                self._failed_files.append(data.get("file", ""))
            self.highlight_updated.emit(json.dumps(data, ensure_ascii=False))
            return
        if line.startswith("[QUALITY_RESOLVED]"):
            self.quality_resolved.emit(line[18:].strip())
            return
        if line.startswith("[QUALITY_WARN]"):
            self.quality_warning.emit(line[14:].strip())
            return
        if line.startswith("[SCAN_WARN]"):
            self.scan_warning.emit(line[11:].strip())
            return
        if line.startswith("[SCAN_DONE]"):
            self.scan_done.emit(line[11:].strip())
            return
        if line.startswith("[OCR_DONE]"):
            self.ocr_ready.emit(line[10:].strip())
            return
        if line.startswith("[OCR_PROG]"):
            data = json.loads(line[10:].strip())
            self._ocr_done  = data["done"]
            self._ocr_total = data["total"]
            self._phase_current = "ocr"
            self._phase_step    = self._ocr_done
            self._phase_total   = self._ocr_total
            cnt = f"{self._ocr_done}/{self._ocr_total}" if self._ocr_total else str(self._ocr_done)
            self._emit_phase(f"곡 정보 분석 중... {cnt} 완료")
            return
        if line.startswith("[DL_PROG]"):
            data = json.loads(line[9:].strip())
            self._dl_done  = data["done"]
            self._dl_total = data["total"]
            self._phase_current = "download"
            self._phase_step    = self._dl_done
            self._phase_total   = self._dl_total
            self._emit_phase(f"다운로드 중... {self._dl_done}/{self._dl_total} 완료")
            return

        # ANSI 제거
        clean = _RE_ANSI.sub("", line).strip()
        if not clean:
            return

        # ── OCR 배치 크기 감지 ────────────────────────────────────────────────
        m_batch = _RE_OCR_BATCH.search(clean)
        if m_batch:
            self._ocr_total = int(m_batch.group(1))
            self._ocr_done  = 0

        # ── OCR 개별 아이템 처리 ──────────────────────────────────────────────
        m_ocr = _RE_OCR_ITEM.search(clean)
        if m_ocr:
            self._ocr_done += 1
            ts_str  = fmt_time(int(m_ocr.group(1)))
            cnt_str = f"{self._ocr_done}/{self._ocr_total}" if self._ocr_total else f"{self._ocr_done}"
            dl_done = self._dl_total > 0 and self._dl_done >= self._dl_total
            if dl_done:
                self._phase_current = "ocr"
                self._phase_step    = self._ocr_done
                self._phase_total   = self._ocr_total
                self._emit_phase(f"OCR 분석 중... {cnt_str} 완료  (영상 {ts_str})")
            else:
                self._emit_phase(
                    f"다운로드 {self._dl_done}/{self._dl_total} 완료  |  OCR {cnt_str} 분석 중 (영상 {ts_str})"
                )
            return

        # ── Phase 전환 감지 ───────────────────────────────────────────────────
        for phase, keywords in _PHASE_MAP:
            if any(kw in clean for kw in keywords):
                if phase != self._phase_current:
                    self._phase_current = phase
                    self._phase_step    = 0
                    self._phase_total   = 0
                break

        # ── 워커 진행 막대 → 전체 평균 % 계산 후 phase bar 전용 ───────────────
        if _RE_WORKER.search(clean):
            if self._phase_current == "fetch":
                self._phase_current   = "scan"
                self._phase_step      = 0
                self._phase_total     = 0
                self._worker_progress = {}
                self._scan_start_time = time.time()
            m_id  = re.search(r'\bW(\d+)\s*\[', clean)
            m_pct = re.search(r'(\d+(?:\.\d+)?)\s*%', clean)
            if m_id and m_pct:
                self._worker_progress[int(m_id.group(1))] = float(m_pct.group(1))
            avg = (sum(self._worker_progress.values()) / len(self._worker_progress)
                   if self._worker_progress else 0.0)
            if avg <= 0.0:
                # 모든 워커가 아직 첫 프레임 전 — spawn·모델 로드·구간 탐색 단계
                self._emit_phase("워커 초기화 중... (모델 로드·구간 탐색)")
            else:
                eta_str = self._calc_eta(avg)
                self._emit_phase(f"스캔 중... {avg:.1f}%{eta_str}")
            return

        # ── 반복성 진행 틱 → phase bar 전용 ─────────────────────────────────
        if _RE_DL_TICK.search(clean) or _RE_UPL_PCT.search(clean):
            if _RE_DL_TICK.search(clean) and self._dl_total:
                self._emit_phase(f"다운로드 중... {self._dl_done}/{self._dl_total} 완료")
            else:
                self._emit_phase(clean)
            return

        # ── 스텝 카운터 갱신 [i/n] ───────────────────────────────────────────
        m = _RE_STEP.search(clean)
        if m:
            self._phase_step  = int(m.group(1))
            self._phase_total = int(m.group(2))
            if "다운로드 완료" in clean:
                self._dl_done  = int(m.group(1))
                self._dl_total = int(m.group(2))
            elif "다운로드 시작" in clean:
                self._dl_total = int(m.group(2))

        # ── 일반 로그 → phase bar + 로그 패널 ────────────────────────────────
        self._emit_phase(clean)
        self._emit_log(_detect_level(clean), clean)
