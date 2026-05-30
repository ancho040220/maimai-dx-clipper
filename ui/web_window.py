"""QWebEngineView 기반 메인 윈도우."""
from pathlib import Path

# Qt D3D11/DXGI DLL보다 torch/CUDA DLL을 먼저 초기화해야 WinError 1114가 발생하지 않음
# (QApplication 생성 전 단계에서 import해야 효과 있음)
try:
    import torch
    torch.cuda.is_available()
    del torch
except Exception:
    pass
try:
    import cv2
    del cv2
except Exception:
    pass

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QIcon
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWidgets import QMainWindow, QApplication

from ui.bridge import Bridge

_WEB_DIR = Path(__file__).parent / "web"


class WebWindow(QMainWindow):
    def closeEvent(self, event):
        self._bridge.stop_pipeline()
        import os
        os._exit(0)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("maimai DX Rating Clipper")
        self.resize(1600, 900)
        self.setMinimumSize(960, 640)

        self._bridge  = Bridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)

        self._view = QWebEngineView(self)
        page = self._view.page()
        page.setWebChannel(self._channel)

        # Allow local file access (needed for loading react.min.js etc. from same folder)
        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)

        self.setCentralWidget(self._view)
        self._view.load(QUrl.fromLocalFile(str(_WEB_DIR / "index.html")))


def launch_gui():
    import sys
    import ctypes
    from config.settings import PROJECT_DIR
    from PyQt5.QtWidgets import QMessageBox

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("maimai.dx.rating.clipper")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(PROJECT_DIR / "ui" / "logo" / "maimai.ico")))

    win = WebWindow()
    win.show()

    highlights_dir = PROJECT_DIR / "highlights"
    if highlights_dir.exists():
        existing_mp4  = list(highlights_dir.glob("*.mp4"))
        existing_json = list(highlights_dir.glob("*.json"))
    else:
        existing_mp4 = existing_json = []
    analysis_dir = PROJECT_DIR / "highlights" / "result_frames"
    existing_photos = list(analysis_dir.glob("*.jpg")) if analysis_dir.exists() else []

    if existing_mp4 or existing_json or existing_photos:
        lines = []
        if existing_mp4 or existing_json:
            lines.append(f"· 영상 {len(existing_mp4)}개, 정보 파일 {len(existing_json)}개 (highlights/)")
        if existing_photos:
            lines.append(f"· 결과 사진 {len(existing_photos)}장 (result_frames/)")
        dlg = QMessageBox(win)
        dlg.setWindowTitle("이전 세션 파일 초기화")
        dlg.setText(
            "이전 세션 파일이 남아 있습니다.\n"
            + "\n".join(lines)
            + "\n\n삭제할까요?"
        )
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dlg.setDefaultButton(QMessageBox.No)
        dlg.setModal(True)
        if dlg.exec_() == QMessageBox.Yes:
            for f in existing_mp4 + existing_json + existing_photos:
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass

    sys.exit(app.exec_())
