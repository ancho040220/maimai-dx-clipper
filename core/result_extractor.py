"""maimai DX 결과화면에서 곡명 / 난이도 / 달성률 추출 (1000×1000 크롭 기준)."""
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import cv2
import numpy as np

import base64
import uuid
import requests as _requests

from config.settings import CLOVA_OCR_URL, CLOVA_OCR_SECRET
from data.song_db import get_internal_level

_paddle_ocr = None


def _sharpness(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()

def _get_paddle_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        from paddleocr import PaddleOCR
        _paddle_ocr = PaddleOCR(use_angle_cls=False, lang='en', show_log=False)
    return _paddle_ocr


def _clova_ocr(img: np.ndarray, max_attempts: int = 2) -> str:
    """CLOVA OCR API로 이미지에서 텍스트 추출 (1000×1000 전체 프레임).

    일시적 실패(inferResult=FAILURE / 연결 오류 / HTTP 5xx)는 재시도한다.
    HTTP 400(쿼터 초과 등)은 재시도해도 무의미하므로 즉시 반환.
    """
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    img_b64 = base64.b64encode(buf).decode('utf-8')
    headers = {"X-OCR-SECRET": CLOVA_OCR_SECRET, "Content-Type": "application/json"}

    def _retry_note(attempt):
        return f" — 재시도 {attempt}/{max_attempts - 1}" if attempt < max_attempts else ""

    for attempt in range(1, max_attempts + 1):
        payload = {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": 0,
            "images": [{"format": "jpg", "name": "img", "data": img_b64}],
        }
        try:
            resp = _requests.post(CLOVA_OCR_URL, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                image = resp.json().get("images", [{}])[0]
                if image.get("inferResult") == "SUCCESS":
                    title = image.get("title", {})
                    if title and title.get("inferText"):
                        return title["inferText"]
                    return " ".join(f["inferText"] for f in image.get("fields", []))
                # inferResult=FAILURE 등 — 일시적, 재시도 대상
                print(f"  ⚠️  CLOVA OCR 인식 실패 (inferResult={image.get('inferResult')}){_retry_note(attempt)}")
            elif resp.status_code == 400:
                try:
                    err_code = resp.json().get("code", "")
                except Exception:
                    err_code = ""
                if err_code == "0023":
                    print("❌ CLOVA OCR 월 사용 한도 초과 — 이후 곡명 인식이 불가능합니다.")
                else:
                    print(f"  ⚠️  CLOVA OCR HTTP {resp.status_code}")
                return ""   # 400은 재시도 무의미
            else:
                # 5xx 등 — 재시도 대상
                print(f"  ⚠️  CLOVA OCR HTTP {resp.status_code}{_retry_note(attempt)}")
        except Exception as e:
            print(f"  ⚠️  CLOVA OCR 연결 실패: {e}{_retry_note(attempt)}")

        if attempt < max_attempts:
            time.sleep(1.0)

    return ""



# ── 원문자 변환 테이블 ─────────────────────────────────────────────────────────
_CIRCLED: dict[str, str] = {
    "①": "1",  "②": "2",  "③": "3",  "④": "4",  "⑤": "5",
    "⑥": "6",  "⑦": "7",  "⑧": "8",  "⑨": "9",  "⑩": "10",
    "⑪": "11", "⑫": "12", "⑬": "13", "⑭": "14", "⑮": "15",
    "⑯": "16", "⑰": "17", "⑱": "18", "⑲": "19", "⑳": "20",
}


_DIFF_NAMES    = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER"]

# 달성률 OCR 영역 (1000×1000 크롭 기준)
_ACH_Y1, _ACH_Y2, _ACH_X1, _ACH_X2 = 245, 415, 40, 680
_ACH_MIN, _ACH_MAX = 50.0, 101.5  # 달성률 유효 범위
_LARGE_BOX_H       = 25           # 대형 숫자 박스 높이 임계값
_LARGE_Y_CENTER    = 50           # crop 내 y 위치 임계값 (이 이상이면 대형 파편으로 판정)


def achievement_to_rank(ach: float) -> str:
    """달성률 → maimai DX 랭크 문자열."""
    if ach >= 100.5: return "SSS+"
    if ach >= 100.0: return "SSS"
    if ach >= 99.5:  return "SS+"
    if ach >= 99.0:  return "SS"
    if ach >= 98.0:  return "S+"
    if ach >= 97.0:  return "S"
    if ach >= 94.0:  return "AAA"
    if ach >= 90.0:  return "AA"
    if ach >= 80.0:  return "A"
    if ach >= 75.0:  return "BBB"
    if ach >= 70.0:  return "BB"
    if ach >= 60.0:  return "B"
    if ach >= 50.0:  return "C"
    return "D"


@dataclass
class SongResult:
    title:          str
    difficulty:     str
    internal_level: Optional[float]
    achievement:    Optional[float]
    rank:           str
    confidence:     float


# ── 전처리 ────────────────────────────────────────────────────────────────────

def normalize_ocr(text: str) -> str:
    """원문자 → 숫자 변환 후 OCR 노이즈 문자 제거."""
    for k, v in _CIRCLED.items():
        text = text.replace(k, v)
    # 일본어(한자/히라가나/가타카나/전각), 영숫자, 공백, maimai 특수문자만 남김
    text = re.sub(
        r"[^\w\s　-鿿゠-ヿ＀-￯♪！？・ー～]", "", text
    )
    return text.strip()


# ── 곡명 바 탐지 ──────────────────────────────────────────────────────────────

def find_song_bar(img: np.ndarray) -> Optional[tuple[int, int]]:
    """어두운 파란색 송 바 행 범위 (y1, y2) 반환. 미검출 시 None."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    dark_blue = (
        (hsv[:, :, 0] >= 100) & (hsv[:, :, 0] <= 135) &
        (hsv[:, :, 1] > 60) &
        (hsv[:, :, 2] >= 15) & (hsv[:, :, 2] <= 200)
    ).astype(np.uint8)

    row_counts = dark_blue.sum(axis=1)
    rows = np.where(row_counts > 200)[0]
    if len(rows) == 0:
        return None

    # 연속 구간 중 가장 긴 것 선택
    gaps     = np.where(np.diff(rows) > 3)[0]
    segments = np.split(rows, gaps + 1)
    longest  = max(segments, key=len)
    y1, y2   = int(longest[0]), int(longest[-1] + 1)
    # 너무 얇으면 중심 기준으로 최소 30px 확보
    if y2 - y1 < 30:
        mid = (y1 + y2) // 2
        y1  = max(0, mid - 15)
        y2  = min(img.shape[0], mid + 15)
    return y1, y2


def _find_text_x_start(img: np.ndarray, y1: int, y2: int) -> int:
    """앨범아트 우측 경계 (텍스트 시작 x) 탐색. 미검출 시 220."""
    band = img[y1:y2, :, :]
    hsv  = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    dark_blue = (
        (hsv[:, :, 0] >= 100) & (hsv[:, :, 0] <= 135) &
        (hsv[:, :, 1] > 60) &
        (hsv[:, :, 2] >= 15) & (hsv[:, :, 2] <= 200)
    )
    col_counts = dark_blue.sum(axis=0)
    bar_h      = max(1, y2 - y1)

    # 컬럼 픽셀의 50% 이상이 dark blue인 최초 열 → 텍스트 시작
    for x in range(img.shape[1]):
        if col_counts[x] >= bar_h * 0.5:
            return max(0, x - 5)
    return 220


# ── 난이도 판별 ───────────────────────────────────────────────────────────────

def detect_difficulty(img: np.ndarray, song_bar_y1: int, x_start: int = 10) -> str:
    """난이도 뱃지 영역을 OCR로 판별. 실패 시 HSV 색상 분석으로 fallback."""
    y1   = max(0, song_bar_y1 - 55)
    y2   = min(img.shape[0], song_bar_y1 + 15)
    x1   = max(0, x_start - 10)
    x2   = min(img.shape[1], x_start + 500)
    chip = img[y1:y2, x1:x2]
    if chip.size == 0:
        return "UNKNOWN"

    result = _get_paddle_ocr().ocr(chip, cls=False)
    for line in (result or []):
        for item in (line or []):
            text = item[1][0]
            matched, score = fuzzy_match(text, _DIFF_NAMES)
            if score >= 0.5:
                return matched

    return "UNKNOWN"


# ── OCR ──────────────────────────────────────────────────────────────────────

def ocr_achievement(img: np.ndarray) -> Optional[float]:
    """달성률 OCR — PaddleOCR (1000×1000 기준 y=245:415, x=40:680). 미검출 시 None."""
    region = img[_ACH_Y1:_ACH_Y2, _ACH_X1:_ACH_X2]
    if region.size == 0:
        return None

    result = _get_paddle_ocr().ocr(region, cls=False)
    # (val, y_center, box_h)
    candidates: list[tuple[float, float, float]] = []
    lower_frags: list[tuple[str, float]] = []

    for line in (result or []):
        for item in (line or []):
            box, (text, _) = item
            ys = [pt[1] for pt in box]
            y_center = sum(ys) / len(ys)
            box_h = max(ys) - min(ys)
            # 대형 현재 점수 파편: box_h>25 또는 크롭 하단부
            is_large = box_h > _LARGE_BOX_H or y_center >= _LARGE_Y_CENTER
            m = re.search(r"(\d{1,3}\.\d{4})", text)
            if m:
                try:
                    val = float(m.group(1))
                    if _ACH_MIN <= val <= _ACH_MAX:
                        candidates.append((val, y_center, box_h))
                    elif is_large:
                        digits = re.sub(r"[^0-9]", "", text)
                        if digits:
                            lower_frags.append((digits, y_center))
                except ValueError:
                    pass
            elif is_large:
                digits = re.sub(r"[^0-9]", "", text)
                if digits:
                    lower_frags.append((digits, y_center))

    def _reconstruct(frags: list[tuple[str, float]]) -> Optional[float]:
        if not frags:
            return None
        spare = "".join(d for d, _ in sorted(frags, key=lambda x: x[1]))
        if len(spare) >= 6:
            try:
                val = float(spare[:-4] + "." + spare[-4:])
                if _ACH_MIN <= val <= _ACH_MAX:
                    return val
            except ValueError:
                pass
        return None

    if candidates:
        # box_h<25인 후보만 있으면 = MY BEST만 인식, 현재 점수는 분할됨
        if all(x[2] < 25 for x in candidates):
            reconstructed = _reconstruct(lower_frags)
            if reconstructed is not None:
                return reconstructed
        return max(candidates, key=lambda x: x[1])[0]

    return _reconstruct(lower_frags)


# ── 퍼지 매칭 ─────────────────────────────────────────────────────────────────

def fuzzy_match(text: str, titles: list[str]) -> tuple[str, float]:
    """OCR 결과를 곡 DB와 퍼지 매칭. (best_title, ratio) 반환.

    OCR은 CJK 글자 사이에 공백을 삽입하므로 공백 제거 버전과 원본 중 높은 값을 사용.
    긴 제목의 앞부분만 OCR된 경우 prefix 비교 점수도 활용.
    """
    if not text or not titles:
        return "", 0.0

    text_lower   = text.lower()
    text_nospace = re.sub(r"\s+", "", text_lower)  # 공백 제거 버전

    best_title = ""
    best_score = 0.0
    for title in titles:
        tl         = title.lower()
        tl_nospace = re.sub(r"\s+", "", tl)
        r1 = SequenceMatcher(None, text_lower,   tl).ratio()
        r2 = SequenceMatcher(None, text_nospace, tl_nospace).ratio()
        # 부분 매칭: OCR이 슬라이드 중인 긴 제목의 임의 구간만 인식한 경우
        # OCR 텍스트 길이 창을 제목 전체에 슬라이드해서 가장 높은 ratio를 탐색
        r3 = 0.0
        n  = len(text_nospace)
        if n > 4 and len(tl_nospace) > n:
            step = max(1, n // 4)
            for i in range(0, len(tl_nospace) - n + 1, step):
                r = SequenceMatcher(None, text_nospace, tl_nospace[i:i + n]).ratio()
                if r > r3:
                    r3 = r
        ratio = max(r1, r2, r3 * 0.99)  # 부분 매칭은 완전 매칭에 항상 패배
        if ratio > best_score:
            best_score, best_title = ratio, title

    return best_title, best_score


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────

def extract_from_frames(
    frames_1000: list[np.ndarray],
    titles:      list[str],
    raw_songs:   list[dict],
    fps:         int   = 30,
    skip_sec:    float = 0.3,
    video_ts:    Optional[float] = None,
) -> Optional[SongResult]:
    """가장 선명한 프레임 1장을 CLOVA OCR로 분석 → SongResult 반환. 실패 시 None."""
    if not frames_1000:
        return None

    # find_song_bar를 게이트로 사용하지 않음 — 전체 프레임 선명도로 최적 프레임 선택
    best_frame = max(frames_1000, key=_sharpness)

    # bar 위치 (detect_difficulty 용; 실패 시 고정 fallback)
    bar = find_song_bar(best_frame)
    if bar is not None:
        y1, y2  = bar
        x_start = _find_text_x_start(best_frame, y1, y2)
    else:
        y1, y2, x_start = 165, 200, 150

    text = normalize_ocr(_clova_ocr(best_frame))
    if not text:
        return None

    matched_title, ratio = fuzzy_match(text, titles)
    print(f"  [CLOVA] {matched_title or '미인식'} (정확도 {ratio:.2f})")
    if not matched_title or ratio < 0.25:
        return None

    diff           = detect_difficulty(best_frame, y1, x_start)
    achievement    = ocr_achievement(best_frame)
    internal_level = get_internal_level(raw_songs, matched_title, diff)

    return SongResult(
        title=matched_title,
        difficulty=diff,
        internal_level=internal_level,
        achievement=achievement,
        rank=achievement_to_rank(achievement) if achievement is not None else "",
        confidence=ratio,
    )
