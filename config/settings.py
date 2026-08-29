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
# 레이팅 OCR 배치 크기 — tesseract 프로세스 1회로 처리할 프레임 수.
# 프로세스 생성 비용(장당 77ms)을 배치로 분산한다. 30 이후로는 수익이 급감한다.
RATING_OCR_BATCH = 30

# ── 곡명 식별 (자켓 매칭 + PaddleOCR) ─────────────────────────────────────────
JACKET_CDN_URL         = "https://shama.dxrating.net/images/cover/v2/{}.jpg"
JACKET_FEAT_N          = 16       # 자켓 특징 해상도 (N×N×3). 올릴수록 압축·블러 불일치가 증폭돼 오히려 불리
JACKET_UTAGE_CATEGORY  = "宴会場"  # 우타게는 원곡 자켓을 공유하므로 인덱싱 제외
JACKET_CONFIRM_MIN     = 0.72     # 이 이상이면 등록곡으로 간주 (미만은 미등록 신곡)
JACKET_MARGIN_MIN      = 0.10     # 1등-2등 차가 이 미만이면 OCR로 판별을 넘김
JACKET_CANDIDATE_MIN   = 0.60     # OCR 판별 시 후보로 남길 최소 점수
TITLE_OCR_LANG         = "japan"  # 곡명 OCR 모델 (달성률·난이도는 en 모델 유지)

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
