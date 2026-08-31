"""배포용 zip 생성.

    python setup/release.py

config/version.py 의 APP_VERSION 을 읽어 dist/maimai-dx-clipper-v{버전}.zip 을 만든다.
포함/제외를 코드로 고정해 손으로 압축할 때 생기는 누락·유출을 막는다.

exe(maimai_clipper.exe)는 git에 없으므로, 프로젝트 루트에 있으면 함께 담고
없으면 경고만 남긴다. 빌드는 다음 명령으로 한다:

    pyinstaller --onefile --noconsole --name maimai_clipper setup/launcher.py
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config.version import APP_VERSION          # noqa: E402

# 배포에 반드시 들어가야 하는 것
INCLUDE_DIRS  = ["core", "config", "data", "ui", "sample", "setup"]
INCLUDE_FILES = ["main_gui.py", "best_nano.onnx", "README.md", "LICENSE"]
OPTIONAL_FILES = ["maimai_clipper.exe"]

# 들어가면 안 되는 것 — 인증 정보, 캐시, 사용자 산출물, 개발 부산물
EXCLUDE_PARTS = {
    "__pycache__", ".git", ".claude", "credentials",
    "cache", "logs", "highlights", "dist", "build",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".npz", ".part"}
# 7일 TTL 캐시라 배포 시점엔 이미 낡는다. 없으면 첫 실행에 자동으로 받는다.
EXCLUDE_NAMES  = {"song_db_cache.json", "mai_youtube_url.txt"}


def _keep(path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    return path.suffix.lower() not in EXCLUDE_SUFFIX


def main() -> int:
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"maimai-dx-clipper-v{APP_VERSION}.zip"

    missing = [f for f in INCLUDE_FILES if not (ROOT / f).exists()]
    if missing:
        print(f"[ERROR] 필수 파일 없음: {', '.join(missing)}")
        return 1

    added = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in INCLUDE_FILES + OPTIONAL_FILES:
            src = ROOT / name
            if src.exists():
                z.write(src, name); added += 1
            elif name in OPTIONAL_FILES:
                print(f"[WARN] {name} 없음 — 런처 없이 패키징합니다. "
                      f"pyinstaller 로 먼저 빌드하세요.")
        for d in INCLUDE_DIRS:
            base = ROOT / d
            if not base.exists():
                print(f"[WARN] 폴더 없음: {d}")
                continue
            for f in sorted(base.rglob("*")):
                if f.is_file() and _keep(f.relative_to(ROOT)):
                    z.write(f, str(f.relative_to(ROOT))); added += 1

    size_mb = out.stat().st_size / 1024 ** 2
    print(f"[OK] {out.name}  —  {added}개 파일, {size_mb:.1f} MB")
    print(f"     {out}")

    # 인증 정보가 섞여 들어가지 않았는지 확인
    with zipfile.ZipFile(out) as z:
        leaked = [n for n in z.namelist()
                  if "credentials" in n or n.endswith(("client_secret.json", "youtube_token.json"))]
    if leaked:
        print(f"[ERROR] 인증 파일이 포함됐습니다: {leaked}")
        return 1
    print("[OK] 인증 정보 유출 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
