"""Background QThread workers for the pipeline GUI."""
import ctypes
import io
import json
import sys
import threading
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal


class _StdioRedirect(io.TextIOBase):
    """Captures print() output and forwards non-blank lines to a signal."""

    def __init__(self, emit_fn):
        super().__init__()
        self._emit = emit_fn
        self._buf = ""

    def write(self, text: str) -> int:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._emit(line.rstrip())
        return len(text)

    def flush(self):
        if self._buf.strip():
            self._emit(self._buf.rstrip())
            self._buf = ""


class CheckWorker(QThread):
    """Fetches video metadata without blocking the GUI."""
    result = pyqtSignal(str)   # JSON string (not dict — safer for cross-thread signals)
    failed = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            from core.pipeline import check_video_status
            self.result.emit(json.dumps(check_video_status(self._url), ensure_ascii=False))
        except Exception as exc:
            self.failed.emit(str(exc))


class PipelineWorker(QThread):
    """Runs a callable in a background thread, forwarding stdout to *log* signal."""
    log    = pyqtSignal(str)
    done   = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._thread_id: Optional[int] = None

    def run(self):
        self._thread_id = threading.current_thread().ident
        old_out, old_err = sys.stdout, sys.stderr
        redirect = _StdioRedirect(self.log.emit)
        sys.stdout = sys.stderr = redirect
        try:
            self._fn(*self._args, **self._kwargs)
            self.done.emit()
        except KeyboardInterrupt:
            pass  # stopped by user via stop()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    def stop(self, cleanup_fn=None):
        """Inject KeyboardInterrupt into the running thread (works while in Python bytecode)."""
        if self._thread_id is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(self._thread_id),
                ctypes.py_object(KeyboardInterrupt),
            )
            # cv2.read() 등 C 확장에서 블로킹 중일 경우 5초 후 강제 종료 후 정리
            from PyQt5.QtCore import QTimer
            def _force_stop():
                if self.isRunning():
                    self.terminate()
                if cleanup_fn:
                    try:
                        cleanup_fn()
                    except Exception:
                        pass
            QTimer.singleShot(5000, _force_stop)
