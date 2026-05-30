"""maimai Rating Clipper 런처 — .exe로 컴파일 시 콘솔 없이 main_gui.py 실행."""
import os
import sys
import shutil
import subprocess


def main():
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        # launcher.py는 setup/ 안에 있으므로 한 단계 위가 프로젝트 루트
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    main_script = os.path.join(base_dir, "main_gui.py")

    # pythonw = 콘솔 창 없는 Python 실행파일
    python = shutil.which("pythonw") or shutil.which("python")

    if not python:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "Python을 찾을 수 없습니다.\nPython이 PATH에 등록되어 있는지 확인하세요.",
            "maimai Rating Clipper",
            0x10,
        )
        return

    if not os.path.exists(main_script):
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"main_gui.py를 찾을 수 없습니다.\n경로: {main_script}",
            "maimai Rating Clipper",
            0x10,
        )
        return

    subprocess.Popen(
        [python, main_script],
        cwd=base_dir,
        creationflags=0x00000008,  # DETACHED_PROCESS — 런처 종료 후에도 계속 실행
    )


if __name__ == "__main__":
    main()
