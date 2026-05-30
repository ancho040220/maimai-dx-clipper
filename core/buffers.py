"""순환 프레임 버퍼 및 ffmpeg 롤링 녹화."""
import subprocess
import threading
from pathlib import Path

from config.settings import NO_WINDOW
from collections import deque
from typing import Optional

import cv2
import numpy as np

from config.settings import SEGMENT_SECS, HIGHLIGHT_PRE, HIGHLIGHT_POST


class CircularFrameBuffer:
    """
    최근 N분간의 1000×1000 크롭을 JPEG 압축하여 메모리에 보관.
    1fps × 6분 × ~50KB/frame ≈ 18MB
    """

    def __init__(self, minutes: int):
        self._buf: deque = deque(maxlen=minutes * 60)  # (wall_time, stream_sec, jpeg_bytes)
        self._lock = threading.Lock()

    def push(self, wall_time: float, stream_sec: float, frame: np.ndarray, quality: int = 85):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            with self._lock:
                self._buf.append((wall_time, stream_sec, buf.tobytes()))

    def snapshot(self) -> list:
        """현재 버퍼 전체의 복사본 반환 [(wall_time, stream_sec, jpeg_bytes), ...]."""
        with self._lock:
            return list(self._buf)

    def get_frame_near(self, wall_time: float) -> Optional[np.ndarray]:
        """wall_time에 가장 가까운 프레임을 디코딩해 반환. 없으면 None."""
        with self._lock:
            buf = list(self._buf)
        if not buf:
            return None
        wt, _, jpeg = min(buf, key=lambda x: abs(x[0] - wall_time))
        if abs(wt - wall_time) > 5.0:
            return None
        arr = np.frombuffer(jpeg, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)


class StreamRecorder:
    """
    ffmpeg로 스트림을 SEGMENT_SECS 단위 .ts 파일로 녹화.
    오래된 세그먼트는 자동 삭제하며 keep_minutes 분량만 유지.
    """

    def __init__(self, stream_url: str, seg_dir: Path, keep_minutes: int):
        self.stream_url = stream_url
        self.seg_dir    = seg_dir
        self.keep_secs  = keep_minutes * 60
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        import shutil
        if self.seg_dir.exists():
            shutil.rmtree(self.seg_dir, ignore_errors=True)
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        seg_pattern = str(self.seg_dir / "seg%06d.ts")
        wrap = int(self.keep_secs / SEGMENT_SECS) + 2
        cmd = [
            "ffmpeg", "-y",
            "-i", self.stream_url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(SEGMENT_SECS),
            "-segment_wrap", str(wrap),
            "-reset_timestamps", "1",
            seg_pattern,
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc.wait()
        import shutil
        shutil.rmtree(self.seg_dir, ignore_errors=True)

    def cut_highlight(self, play_wall: float, result_wall: float, output: Path) -> bool:
        """play_wall ~ result_wall + HIGHLIGHT_POST 구간을 output으로 무손실 커팅."""
        segs = sorted(self.seg_dir.glob("seg*.ts"), key=lambda f: f.stat().st_mtime)
        if not segs:
            return False

        start_clip = play_wall - HIGHLIGHT_PRE
        end_clip   = result_wall + HIGHLIGHT_POST

        n   = len(segs)
        EPS = 0.5
        if n >= 2:
            ref_end = segs[-2].stat().st_mtime
            ref_idx = n - 2
        else:
            ref_end = segs[-1].stat().st_mtime
            ref_idx = n - 1

        concat_list = []
        first_seg_start = None
        for i, seg in enumerate(segs):
            seg_end   = ref_end + (i - ref_idx) * SEGMENT_SECS
            seg_start = seg_end - SEGMENT_SECS
            if seg_start <= end_clip + EPS and seg_end >= start_clip - EPS:
                concat_list.append(str(seg))
                if first_seg_start is None:
                    first_seg_start = seg_start

        if not concat_list or first_seg_start is None:
            return False

        concat_txt = self.seg_dir / "_concat.txt"
        concat_txt.write_text("\n".join(f"file '{s}'" for s in concat_list), encoding="utf-8")

        ss_offset = max(0.0, start_clip - first_seg_start)
        to_offset = end_clip - first_seg_start

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-ss", str(ss_offset),
            "-to", str(to_offset),
            "-c", "copy",
            str(output),
        ]
        ret = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW).returncode
        concat_txt.unlink(missing_ok=True)
        return ret == 0
