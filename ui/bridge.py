"""QWebChannel bridge — Python ↔ JS 양방향 통신."""
import json
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from config.settings import PROJECT_DIR
from core.error_messages import translate_error
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


def _detect_level(text: str) -> str:
    if any(k in text for k in _LVL_OK):   return "ok"
    if any(k in text for k in _LVL_WARN): return "warn"
    if any(k in text for k in _LVL_ERR):  return "err"
    if any(k in text for k in _LVL_HL):   return "hl"
    return "info"


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
    pipeline_error    = pyqtSignal(str)
    pipeline_stopped  = pyqtSignal()
    env_check_result  = pyqtSignal(str)   # JSON list[dict]
    ocr_ready         = pyqtSignal(str)   # JSON list[OcrItem] — OCR 편집 화면 표시 요청

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
        self._last_history    = []
        self._last_url        = ""
        self._song_titles: list = []
        self._raw_songs:   list = []
        self._song_db_loaded    = False
        self._auto_upload     = True
        self._ocr_event          = None
        self._confirm_event      = None
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

        from config.settings import CACHE_DIR
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / "mai_youtube_url.txt").write_text(url, encoding="utf-8")
        (PROJECT_DIR / "highlights").mkdir(exist_ok=True)

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
                    output_file=None,
                )
                if not history:
                    print("ℹ️  감지된 레이팅 변동 없음 — 클립 추출 단계를 건너뜁니다.")
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
        if self._confirm_event:
            self._confirm_event.set()
        if self._live_monitor is not None:
            self._live_monitor.stop()
            self._live_monitor = None
        if self._worker and self._worker.isRunning():
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
            meta_path = file_path.with_suffix(".json")
            if meta_path.exists():
                try:
                    meta  = json.loads(meta_path.read_text(encoding="utf-8"))
                    title = meta.get("title", "")
                    desc  = meta.get("description", "")
                except Exception:
                    title = ""
                    desc  = ""
            else:
                title = ""
                desc  = ""

            if not title:
                parts  = file_path.stem.split("_")
                rating = parts[-1] if parts else "?"
                title  = f"[maimai DX] Rating Up! {rating}"
                desc   = f"재업로드: {file_name}"

            video_id = uploader.upload(file_path, title, desc)
            if video_id:
                try:
                    meta_path.unlink(missing_ok=True)
                except OSError:
                    pass

        self._retry_workers = [w for w in self._retry_workers if w.isRunning()]
        worker = PipelineWorker(_do_retry)
        worker.log.connect(self._on_log)
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

        output_dir = PROJECT_DIR / "highlights"
        uploader   = YouTubeUploader() if self._auto_upload else None
        url        = self._last_url

        self._ocr_event          = threading.Event()
        self._confirm_event      = threading.Event()
        self._confirmed_history  = selected_history
        self._ocr_payload_holder = []

        ocr_event             = self._ocr_event
        confirm_event         = self._confirm_event
        confirmed_history_ref = selected_history
        ocr_payload_holder    = self._ocr_payload_holder

        # 라이브 항목은 clip_path 가 있음 (Phase 1에서 저장됨)
        is_live_mode = any(e.get("clip_path") for e in selected_history)

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
                )

        self._worker = PipelineWorker(_run_clipping)
        self._worker.log.connect(self._on_log)
        self._worker.done.connect(self.scan_finished.emit)
        self._worker.failed.connect(self._on_pipeline_error)
        self._worker.start()

    @pyqtSlot(str)
    def confirm_ocr(self, edited_json: str):
        """JS에서 OCR 편집 완료 후 호출 — 편집 데이터를 history에 적용하고 파이프라인 재개."""
        try:
            edited = json.loads(edited_json)
        except Exception:
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
        matches = [t for t in self._song_titles if q in t.lower()][:5]
        return json.dumps(matches, ensure_ascii=False)

    @pyqtSlot(str, str, result=str)
    def lookup_internal_level(self, title: str, difficulty: str) -> str:
        """곡명+난이도로 내부 레벨 조회. 없으면 빈 문자열 반환."""
        self._ensure_song_db()
        from data.song_db import get_internal_level
        level = get_internal_level(self._raw_songs, title, difficulty)
        return str(level) if level is not None else ""

    @pyqtSlot(str)
    def open_result_frame(self, det_id: str):
        """결과 프레임 이미지를 OS 기본 뷰어로 열기."""
        import os
        frame_path = PROJECT_DIR / "highlights" / "result_frames" / f"{det_id}.jpg"
        if frame_path.exists():
            os.startfile(str(frame_path))

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
            for f in (PROJECT_DIR / "highlights").glob("_temp_*.mp4"):
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
            self._emit_phase(f"CLOVA OCR 분석 중... {cnt} 완료")
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
            if self._worker_progress:
                avg = sum(self._worker_progress.values()) / len(self._worker_progress)
                eta_str = self._calc_eta(avg)
                self._emit_phase(f"스캔 중... {avg:.1f}%{eta_str}")
            else:
                self._emit_phase("스캔 중...")
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
