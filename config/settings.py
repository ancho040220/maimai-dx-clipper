"""전역 설정값 및 상수."""
import json
import os
import shutil
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROJECT_DIR     = Path(__file__).parent.parent
CREDENTIALS_DIR = PROJECT_DIR / "config" / "credentials"
CACHE_DIR       = PROJECT_DIR / "cache"

# ── 인증 파일 경로 ──────────────────────────────────────────────────────────────
CLIENT_SECRET  = CREDENTIALS_DIR / "client_secret.json"
YOUTUBE_TOKEN  = CREDENTIALS_DIR / "youtube_token.json"

# YouTube 다운로드 시 로그인 쿠키를 Firefox에서 직접 읽음
# 다른 브라우저를 쓰려면 환경변수 YTDLP_BROWSER=chrome 등으로 지정
YTDLP_BROWSER = os.environ.get("YTDLP_BROWSER", "firefox")


def ytdlp_cookie_args() -> list:
    """yt-dlp 커맨드라인용 쿠키 인자."""
    return ["--cookies-from-browser", YTDLP_BROWSER]


def ytdlp_cookie_opts() -> dict:
    """yt-dlp Python API(YoutubeDL)용 쿠키 옵션."""
    return {"cookiesfrombrowser": (YTDLP_BROWSER,)}

# ── 레이팅 판별 ────────────────────────────────────────────────────────────────
RATING_MIN        = 0
RATING_MAX        = 17000
MAX_RATING_CHANGE = 340     # 한 판 최대 상승폭 (초과 시 오인식으로 판단)
MIN_GAP           = 20.0    # 레이팅 감지 최소 간격 (초)

# ── OCR ────────────────────────────────────────────────────────────────────────
_tess_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract") or _tess_default
TESS_CONFIG   = "--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789"

# ── CLOVA OCR ──────────────────────────────────────────────────────────────────
def _load_clova_credentials() -> tuple[str, str]:
    path = CREDENTIALS_DIR / "clova_ocr.txt"
    if not path.exists():
        return "", ""
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            pairs[k.strip()] = v.strip()
    return pairs.get("CLOVA_OCR_URL", ""), pairs.get("CLOVA_OCR_SECRET", "")

CLOVA_OCR_URL, CLOVA_OCR_SECRET = _load_clova_credentials()

# ── YOLO ───────────────────────────────────────────────────────────────────────
MODEL_PATH = str(PROJECT_DIR / "best_nano.pt")
YOLO_CONF  = 0.5

# ── 스캐너 ─────────────────────────────────────────────────────────────────────
BAR_WIDTH         = 28
LOOKBACK_STEP     = 0.5    # 역추적 탐색 간격 (초)
TMPL_MATCH_THRESH = 0.7    # 템플릿 매칭 임계값

# ── 1000×1000 기준 ROI 좌표 ────────────────────────────────────────────────────
GAME_ROI = [478, 451, 809, 547]   # 레이팅 숫자 OCR 영역

KALEID_START_ROI  = [302, 145, 691, 208]
KALEID_START_TMPL = str(PROJECT_DIR / "sample" / "kaleid_start")

TRACK_ROI  = [385, 85, 545, 150]
TRACK_TMPL = str(PROJECT_DIR / "sample" / "normal_start")

PERFECT_START_ROI  = [330, 759, 671, 864]
PERFECT_START_TMPL = str(PROJECT_DIR / "sample" / "perfect_start")

GRADE_START_ROI  = [363, 705, 553, 775]
GRADE_START_TMPL = str(PROJECT_DIR / "sample" / "grade_start")

MODE_LABELS = {
    "caleidoscope":      "칼레이도",
    "perfect_challenge": "퍼펙트 챌린지",
    "normal":            "일반",
    "grade":             "단위인정",
}

# ── 라이브 파이프라인 ───────────────────────────────────────────────────────────
BUFFER_MINUTES = 8      # 순환 버퍼 크기 (분)
SCAN_INTERVAL  = 1.0    # 라이브 포워드 스캔 간격 (초)
HIGHLIGHT_PRE  = 5      # 하이라이트 앞 여유 (초)
HIGHLIGHT_POST = 10     # 하이라이트 뒤 여유 (초)
SEGMENT_SECS   = 60     # ffmpeg 세그먼트 단위 (초)

# ── 다운로드 / 매칭 ────────────────────────────────────────────────────────────
DL_WORKERS_MAX       = 5   # 동시 다운로드 최대 워커 수
OCR_CLIP_PRE         = 1.5  # OCR 클립 앞쪽 여유 (초)
OCR_CLIP_POST        = 6.5  # OCR 클립 뒤쪽 여유 (초) — 레이팅 상승 애니메이션 후 결과 화면(~+6s)은 포착하되, 뒤이은 선곡 화면 오선택은 최소화
OCR_CONFIDENCE_MIN     = 0.3  # OCR 결과 최소 신뢰도

# ── 타임아웃 (초) ──────────────────────────────────────────────────────────────
TIMEOUT_OCR_BATCH_BASE    = 30    # OCR 클립 다운로드 기본 타임아웃 (작은 클립이라 짧게 — 매달림 시 조기 종료)
TIMEOUT_OCR_BATCH_PER     = 20    # OCR 클립 다운로드 항목당 추가 타임아웃
TIMEOUT_OCR_EDIT_WAIT     = 3600  # OCR 편집 대기 최대 시간
TIMEOUT_LOOKBACK_PER_ITEM = 180   # 역추적 항목당 타임아웃
TIMEOUT_WORKER_WATCHDOG   = 600   # 워커 watchdog 기준

# ── 사용자 설정 ────────────────────────────────────────────────────────────────
USER_CONFIG_PATH     = PROJECT_DIR / "config" / "user_config.json"
_DEFAULT_USER_CONFIG = {"clipSelect": True, "ocrEdit": True, "autoUpload": True, "songOcr": True}


def load_user_config() -> dict:
    if not USER_CONFIG_PATH.exists():
        return dict(_DEFAULT_USER_CONFIG)
    try:
        return json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_USER_CONFIG)
