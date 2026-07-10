"""실시간 라이브 스트림 모니터링 — Phase 1 (수집) 전용.

Phase 1: 감지 → 클리핑 → 선택적 OCR → history 누적
Phase 2: bridge.py 가 process_live_clips() 를 통해 업로드 처리
"""
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from config.settings import (
    BUFFER_MINUTES, MIN_GAP, MAX_RATING_CHANGE, MODE_LABELS,
    NO_WINDOW, SEGMENT_SECS,
)
from core.buffers import CircularFrameBuffer, StreamRecorder
from core.scanner_parallel import (
    _detect_game_crop, _init_yolo, _init_tess,
    _ocr_rating, _build_candidates, _lookback,
    fmt_time, yt_timestamp_url,
)
from core.result_extractor import _sharpness


def _seg_venc_args() -> list:
    """세그먼트 concat 재인코딩 시 사용할 비디오 코덱 인자 반환.
    키프레임을 1초마다 강제해 OpenCV seek 정확도를 높인다.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return ["-c:v", "h264_nvenc", "-g", "30", "-c:a", "copy"]
    except Exception:
        pass
    return ["-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "copy"]


class LiveMonitor:
    """실시간 라이브 스트림 감시 → 레이팅 변동 감지 → 버퍼 역추적 → 하이라이트 저장.

    start() 는 블로킹으로 실행되다가 stop() 이 호출되면 history 를 반환한다.
    업로드/OCR 편집은 Phase 2 (process_live_clips) 에서 처리한다.
    """

    def __init__(
        self,
        url: str,
        output_dir: Path,
        uploader=None,
        buffer_minutes: int = BUFFER_MINUTES,
        initial_rating: Optional[int] = None,
        skip_ocr: bool = False,
    ):
        self.url            = url
        self.output_dir     = output_dir
        self.uploader       = uploader      # Phase 2 에서 사용 (여기서는 보관만)
        self.initial_rating = initial_rating
        self.skip_ocr       = skip_ocr
        self.buffer         = CircularFrameBuffer(minutes=buffer_minutes)
        self.yolo           = None
        self.tess           = None
        self.recorder: Optional[StreamRecorder] = None
        self._ffmpeg_pipe: Optional[subprocess.Popen] = None
        self._stream_start_wall = 0.0
        self._stream_url     = ""
        self._reconnect_count = 0
        self._stop_event     = threading.Event()
        self._history: list  = []
        self._history_lock   = threading.Lock()
        self._rating_threads: list = []
        self._clip_sem       = threading.Semaphore(2)  # 동시 클리핑(역추적+ffmpeg) 제한 — 실시간 감지 굶김 방지 (A-25)
        self._detect_count   = 0           # monitor_loop 단일 스레드에서만 쓰임

    def start(self) -> list:
        """라이브 모니터링 시작 (블로킹). stop() 호출 시 누적된 history 반환."""
        from core.pipeline import get_stream_url
        print("🔗  스트림 URL 추출 중...")
        self._stream_url = get_stream_url(self.url)

        print("🤖  모델 초기화 중...")
        self.yolo = _init_yolo("cuda")
        self.tess = _init_tess()

        seg_dir = self.output_dir / "_segments"
        self.recorder = StreamRecorder(self._stream_url, seg_dir, keep_minutes=BUFFER_MINUTES)
        self.recorder.start()
        print("🎥  ffmpeg 롤링 녹화 시작\n")
        print("🔍  실시간 레이팅 모니터링 시작...\n")

        self._stream_start_wall = time.time()

        try:
            self._monitor_loop()
        except KeyboardInterrupt:
            print("\n⏹  모니터링 중단")
        finally:
            # 녹화 프로세스만 먼저 멈추고(새 세그먼트 중단), 세그먼트 삭제는 진행 중인
            # 클리핑이 끝난 뒤로 미룬다 — 방금 삭제된 세그먼트로 커팅해 실패하는 것 방지 (A-18)
            rec = self.recorder
            if rec and rec._proc and rec._proc.poll() is None:
                try:
                    rec._proc.terminate()
                    rec._proc.wait()
                except Exception:
                    pass
            print(f"\n⏳  진행 중인 클리핑 완료 대기 중... ({len(self._rating_threads)}건)")
            for t in list(self._rating_threads):
                t.join(timeout=60)
            if rec:
                rec.stop()

        return self._history

    def stop(self):
        """외부에서 모니터링 중단 요청. ffmpeg 종료 + 루프 탈출 신호."""
        self._stop_event.set()
        for proc in (self._ffmpeg_pipe, self.recorder._proc if self.recorder else None):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

    def _get_stream_resolution(self) -> Tuple[int, int]:
        """ffprobe로 스트림 해상도 조회. 실패 시 1920×1080 반환."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-select_streams", "v:0", self._stream_url],
                capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30,
            )
            streams = json.loads(result.stdout).get("streams", [])
            if streams:
                return int(streams[0]["width"]), int(streams[0]["height"])
        except Exception:
            pass
        return 1920, 1080

    def _start_ffmpeg_pipe(self) -> subprocess.Popen:
        """FFmpeg 1fps rawvideo 파이프 프로세스 시작."""
        try:
            import torch
            hwaccel = ["-hwaccel", "cuda"] if torch.cuda.is_available() else []
        except Exception:
            hwaccel = []
        return subprocess.Popen(
            [
                "ffmpeg", "-y", *hwaccel,
                "-i", self._stream_url,
                "-vf", "fps=1",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )

    def _monitor_loop(self):
        from core.pipeline import get_stream_url

        print("📐  스트림 해상도 확인 중...")
        width, height = self._get_stream_resolution()
        frame_size = width * height * 3
        print(f"    {width}×{height}\n")

        last_result_time = -MIN_GAP - 1.0
        pending: Optional[Tuple[float, int]] = None
        last_confirmed: Optional[int] = self.initial_rating
        proc = self._start_ffmpeg_pipe()
        self._ffmpeg_pipe = proc

        try:
            while True:
                raw = proc.stdout.read(frame_size)

                if len(raw) < frame_size:
                    proc.terminate()
                    proc.wait()
                    if self._stop_event.is_set():
                        break  # 사용자가 수집 중단 요청 → graceful exit
                    self._reconnect_count += 1
                    print(f"⚠️  스트림 끊김 — 재연결... ({self._reconnect_count}회)")
                    if self._reconnect_count >= 15:
                        raise RuntimeError("연속 15회 재연결 실패 — 스트림 연결 불가로 모니터링 중단")
                    time.sleep(1)
                    try:
                        self._stream_url = get_stream_url(self.url)
                        width, height = self._get_stream_resolution()
                        frame_size = width * height * 3
                    except Exception as e:
                        print(f"    재연결 실패: {e}")
                    proc = self._start_ffmpeg_pipe()
                    self._ffmpeg_pipe = proc   # 재연결된 새 파이프를 stop()이 종료할 수 있도록 갱신
                    continue

                self._reconnect_count = 0
                frame = np.frombuffer(raw, np.uint8).reshape((height, width, 3)).copy()

                wall_now   = time.time()
                stream_sec = wall_now - self._stream_start_wall

                try:
                    crop = _detect_game_crop(self.yolo, frame)

                    if crop is not None:
                        self.buffer.push(wall_now, stream_sec, crop)

                    if stream_sec - last_result_time < MIN_GAP:
                        pending = None
                    else:
                        current_rating = _ocr_rating(self.tess, crop) if crop is not None else None

                        if pending is not None:
                            prev_ts, prev_val = pending
                            pending = None
                            confirmed = current_rating if current_rating is not None else prev_val

                            if last_confirmed is not None and confirmed != last_confirmed:
                                change = confirmed - last_confirmed
                                if 0 < change <= MAX_RATING_CHANGE:
                                    self._detect_count += 1
                                    det_seq = self._detect_count
                                    det_id  = f"result_{det_seq}"
                                    print(f"\n🎯  레이팅 변동 감지: {last_confirmed} → {confirmed} (+{change})  [{fmt_time(stream_sec)}] — 역추적 분석 중...")
                                    # 즉시 감지 카드 emit (play_t는 역추적 후 DETECT_UPD로 갱신)
                                    print(f"[DETECT] {json.dumps({'id': det_id, 't': fmt_time(stream_sec), 'play_t': None, 'before': last_confirmed, 'after': confirmed, 'delta': change}, ensure_ascii=False)}")
                                    t = threading.Thread(
                                        target=self._clip_worker,
                                        kwargs=dict(
                                            det_id=det_id,
                                            result_ts_wall=wall_now,
                                            result_stream_sec=stream_sec,
                                            prev_rating=last_confirmed,
                                            new_rating=confirmed,
                                            change=change,
                                        ),
                                        daemon=True,
                                    )
                                    self._rating_threads = [x for x in self._rating_threads if x.is_alive()]
                                    self._rating_threads.append(t)
                                    t.start()
                                    last_result_time = stream_sec
                            if last_confirmed is None or 0 <= confirmed - last_confirmed <= MAX_RATING_CHANGE:
                                last_confirmed = confirmed

                        elif current_rating is not None:
                            if (last_confirmed is None
                                    or (last_confirmed <= current_rating
                                        and current_rating - last_confirmed < MAX_RATING_CHANGE)):
                                pending = (stream_sec, current_rating)

                except Exception as e:
                    print(f"⚠️  프레임 처리 오류 — 건너뜀: {e.__class__.__name__}: {e}")

        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()

    def _clip_worker(self, **kwargs):
        # 동시 클리핑 수를 세마포어로 제한해 실시간 감지 루프가 굶지 않게 한다 (A-25)
        with self._clip_sem:
            self._on_rating_change(**kwargs)

    def _on_rating_change(
        self,
        det_id: str,
        result_ts_wall: float,
        result_stream_sec: float,
        prev_rating: int,
        new_rating: int,
        change: int,
    ):
        play_stream_sec, mode = self._lookback_from_segments(result_ts_wall, result_stream_sec)

        mode_label = MODE_LABELS.get(mode, "미확인")
        if play_stream_sec is not None:
            print(f"    {mode_label} 시작: {fmt_time(play_stream_sec)}")
            print(f"[DETECT_UPD] {json.dumps({'id': det_id, 'play_t': fmt_time(play_stream_sec), 'mode': mode}, ensure_ascii=False)}")
        else:
            print("    시작 화면을 찾지 못했습니다.")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts_tag   = time.strftime("%Y%m%d_%H%M%S", time.localtime(result_ts_wall))
        out_file = self.output_dir / f"_temp_live_{ts_tag}.mp4"

        play_wall = (
            result_ts_wall - result_stream_sec + play_stream_sec
            if play_stream_sec is not None
            else result_ts_wall - 120
        )
        result_wall_for_clip = (
            play_wall + (result_stream_sec - play_stream_sec)
            if play_stream_sec is not None
            else result_ts_wall
        )
        ok = self.recorder.cut_highlight(play_wall, result_wall_for_clip, out_file)

        if not ok:
            print("    ⚠️  하이라이트 커팅 실패")
            return

        size_mb = round(out_file.stat().st_size / 1024 / 1024, 1) if out_file.exists() else 0
        print(f"    💾  클립 저장: {out_file.name}  ({size_mb} MB)")

        entry = {
            "_detection_id":   det_id,
            "current_rating":  new_rating,
            "change":          change,
            "previous_rating": prev_rating,
            "play_url":  yt_timestamp_url(self.url, play_stream_sec) if play_stream_sec is not None else "",
            "yt_url":    yt_timestamp_url(self.url, result_stream_sec),
            "mode":      mode,
            "timestamp": result_stream_sec,
            "clip_path": str(out_file),
        }

        # 결과 화면 프레임 추출 + 이미지 저장 — CLOVA OCR은 Phase 2(클립 선택 후)에서 실행
        if not self.skip_ocr:
            try:
                ocr_frame = self.buffer.get_frame_near(result_ts_wall)
                if ocr_frame is not None:
                    result_frames_dir = self.output_dir / "result_frames"
                    result_frames_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(result_frames_dir / f"{det_id}.jpg"), ocr_frame)
                    print(f"    📸  결과 화면 저장: {det_id}.jpg")
                else:
                    print(f"    ⚠️  결과 화면 프레임 없음 (버퍼 미기록)")
            except Exception as e:
                print(f"    ⚠️  결과 화면 추출 실패: {e}")

        with self._history_lock:
            self._history.append(entry)

    def _concat_segments(
        self, start_wall: float, end_wall: float, tag_prefix: str, tag: int
    ) -> Tuple[Optional[Path], Optional[float]]:
        """start_wall ~ end_wall 구간을 포함하는 세그먼트를 concat해 임시 mp4 반환.

        반환: (tmp_file, first_seg_start) 또는 실패 시 (None, None).
        """
        segs = sorted(self.recorder.seg_dir.glob("seg*.ts"), key=lambda f: f.stat().st_mtime)
        if not segs:
            return None, None

        EPS = 0.5
        n   = len(segs)
        # 마지막 세그먼트는 현재 기록 중이라 mtime ≈ 현재시각 (끝 시각이 아님).
        # 완료된 직전 세그먼트(segs[-2])의 mtime을 기준으로 삼아야 오프셋 오차를 피할 수 있음.
        if n >= 2:
            ref_end = segs[-2].stat().st_mtime
            ref_idx = n - 2
        else:
            ref_end = segs[-1].stat().st_mtime
            ref_idx = n - 1

        concat_list     = []
        first_seg_start = None
        for i, seg in enumerate(segs):
            seg_end   = ref_end + (i - ref_idx) * SEGMENT_SECS
            seg_start = seg_end - SEGMENT_SECS
            if seg_start <= end_wall + EPS and seg_end >= start_wall - EPS:
                concat_list.append(str(seg))
                if first_seg_start is None:
                    first_seg_start = seg_start

        if not concat_list or first_seg_start is None:
            return None, None

        concat_txt = self.recorder.seg_dir / f"_concat_{tag_prefix}_{tag}.txt"
        tmp_file   = self.recorder.seg_dir / f"_{tag_prefix}_tmp_{tag}.mp4"
        concat_txt.write_text(
            "\n".join(f"file '{s}'" for s in concat_list), encoding="utf-8"
        )
        ret = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_txt), *_seg_venc_args(), str(tmp_file)],
            capture_output=True, creationflags=NO_WINDOW,
        ).returncode
        concat_txt.unlink(missing_ok=True)

        if ret != 0 or not tmp_file.exists():
            return None, None

        return tmp_file, first_seg_start

    def _lookback_from_segments(
        self, result_ts_wall: float, result_stream_sec: float
    ) -> Tuple[Optional[float], Optional[str]]:
        """StreamRecorder 세그먼트 파일에서 직접 역추적 (VOD 방식과 동일)."""
        max_lookback = min(result_stream_sec, 360.0)
        tag          = int(result_ts_wall)

        tmp_file, first_seg_start = self._concat_segments(
            result_ts_wall - max_lookback, result_ts_wall, "lb", tag
        )
        if tmp_file is None:
            return None, None

        cap = cv2.VideoCapture(str(tmp_file))
        if not cap.isOpened():
            tmp_file.unlink(missing_ok=True)
            return None, None

        fps             = cap.get(cv2.CAP_PROP_FPS) or 30.0
        local_result_ts = result_ts_wall - first_seg_start
        candidates      = _build_candidates()
        local_play_ts, mode = _lookback(cap, fps, local_result_ts, max_lookback, self.yolo, candidates)
        cap.release()
        tmp_file.unlink(missing_ok=True)

        if local_play_ts is None:
            return None, None

        play_stream_sec = (first_seg_start + local_play_ts) - self._stream_start_wall
        return play_stream_sec, mode

