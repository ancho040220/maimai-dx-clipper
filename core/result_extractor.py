"""maimai DX 결과화면에서 곡명 / 난이도 / 달성률 추출 (1000×1000 크롭 기준)."""
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import cv2
import numpy as np

from config.settings import (
    JACKET_CANDIDATE_MIN, JACKET_CONFIRM_MIN, JACKET_MARGIN_MIN, TITLE_OCR_LANG,
)
from core import jacket_index
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


_title_ocr = None

# 곡명 바 영역 (1000×1000 크롭 기준). 남색 단색 바 위 흰 글씨라 배경 간섭이 없다.
_TITLE_Y1, _TITLE_Y2, _TITLE_X1, _TITLE_X2 = 195, 236, 245, 825


def _get_title_ocr():
    """곡명 전용 PaddleOCR(일본어). 달성률·난이도용 en 모델과 별도 인스턴스."""
    global _title_ocr
    if _title_ocr is None:
        from paddleocr import PaddleOCR
        _title_ocr = PaddleOCR(use_angle_cls=False, lang=TITLE_OCR_LANG, show_log=False)
    return _title_ocr


def ocr_song_title(img: np.ndarray) -> str:
    """곡명 바를 OCR해 원문 텍스트를 반환. 실패 시 빈 문자열.

    업스케일하면 오히려 인식률이 떨어지므로 크롭을 그대로 넣는다.
    """
    bar = img[_TITLE_Y1:_TITLE_Y2, _TITLE_X1:_TITLE_X2]
    if bar.size == 0:
        return ""
    try:
        result = _get_title_ocr().ocr(bar, cls=False)
    except Exception as e:
        print(f"  ⚠️  곡명 OCR 실패: {e}")
        return ""
    if not result or not result[0]:
        return ""
    return "".join(line[1][0] for line in result[0])


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
    chart_type:     Optional[str] = None   # "std"(스탠다드) / "dx"(でらっくす) / None(판별 불가)
    my_best:        Optional[float] = None  # 결과 화면의 MY BEST (이번 판 이전의 최고 달성률)
    best_delta:     Optional[float] = None  # MY BEST와의 차이(절댓값)
    new_record:     Optional[bool]  = None  # True 신기록 / False 아님 / None 판독 못 함
    record_exact:   Optional[bool]  = None  # True: 세 숫자의 합이 정확히 맞음 / False: 두 값에서 복원한 값
    record_frame:   Optional[int]   = None  # 신기록 판독에 쓴 프레임의 위치 (입력 프레임 목록 기준)


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

# 난이도 띠 오른쪽의 채보 종류 알약 (1000×1000 기준) — 스탠다드는 파란 알약, DX는 흰 알약
_TYPE_Y1, _TYPE_Y2, _TYPE_X1, _TYPE_X2 = 168, 186, 622, 690


def detect_chart_type(img: np.ndarray) -> Optional[str]:
    """결과 화면의 알약 색으로 표준/DX 판별. 애매하면 None (추측하지 않는다).

    같은 곡·난이도라도 표준과 DX의 레벨이 다르다(83곡). 샘플 8장에서 파랑 0.72 / 흰색 0.53으로
    서로 다른 쪽이 0.1 미만이라 간격이 크다.
    """
    roi = img[_TYPE_Y1:_TYPE_Y2, _TYPE_X1:_TYPE_X2]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    blue  = float(((h >= 95) & (h <= 118) & (s >= 110) & (v >= 150)).mean())
    white = float(((s <= 70) & (v >= 190)).mean())
    if blue >= 0.4 and blue > white:
        return "std"
    if white >= 0.3 and white > blue:
        return "dx"
    return None


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

def ocr_achievement(img: np.ndarray, lo: float = _ACH_MIN, strict: bool = False) -> Optional[float]:
    """달성률 OCR — PaddleOCR (1000×1000 기준 y=245:415, x=40:680). 미검출 시 None.

    lo: 유효 범위 하한. 기본 50% (레이팅이 오르는 판만 볼 때). 신기록 분석은 D 랭크(50% 미만)도 읽어야 한다.
    strict: 큰 점수 글자로 보이는 후보만 쓴다. 큰 숫자를 못 읽었을 때 위쪽 배너의 MY BEST·개선폭을
            달성률로 잘못 돌려주지 않게 한다 (lo를 낮추면 개선폭도 범위에 들어와서 필요하다).
    """
    region = img[_ACH_Y1:_ACH_Y2, _ACH_X1:_ACH_X2]
    if region.size == 0:
        return None

    result = _get_paddle_ocr().ocr(region, cls=False)
    # (val, y_center, box_h, is_large)
    candidates: list[tuple[float, float, float, bool]] = []
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
                    if lo <= val <= _ACH_MAX:
                        candidates.append((val, y_center, box_h, is_large))
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
                if lo <= val <= _ACH_MAX:
                    return val
            except ValueError:
                pass
        return None

    if strict:
        candidates = [c for c in candidates if c[3]]
    if candidates:
        # box_h<25인 후보만 있으면 = MY BEST만 인식, 현재 점수는 분할됨
        if all(x[2] < 25 for x in candidates):
            reconstructed = _reconstruct(lower_frags)
            if reconstructed is not None:
                return reconstructed
        return max(candidates, key=lambda x: x[1])[0]

    return _reconstruct(lower_frags)


# ── 신기록 판독 ───────────────────────────────────────────────────────────────
# 결과 화면에는 같은 정보가 세 군데 있다 — 달성률(큰 숫자), MY BEST, 개선폭(배너)이고 서로
# 달성률 = MY BEST ± 개선폭 으로 묶여 있다. 셋을 다 읽으려 하면 작은 글씨(개선폭)의 한 자리 오독이나
# 큰 숫자를 못 읽는 프레임(빨간 점수, 애니메이션 효과) 하나 때문에 판 전체를 놓친다. 그래서 프레임별로
# 읽은 값을 판 단위로 모아, 서로 맞는 조합을 찾고, 하나가 비면 나머지로 복원한다.

# MY BEST / 개선폭 배너 (1000×1000 크롭 기준) — 달성률 큰 숫자 바로 위. NEW RECORD 라벨도 이 안에 있다.
_BEST_Y1, _BEST_Y2, _BEST_X1, _BEST_X2 = 255, 315, 300, 565
_BEST_TOL     = 0.00015   # 4자리 숫자끼리의 덧셈·뺄셈이라 사실상 정확히 맞아야 한다
_DELTA_SLACK  = 0.05      # 개선폭을 한 자리 오독해도 이 안이면 같은 값으로 본다 (소수 둘째 자리 오독이면 0.02 어긋난다)
_MIN_DIFF     = 0.01      # 달성률과 MY BEST가 이보다 가까우면 한 자리 오독이 판정을 뒤집을 수 있다
_RECORD_TRIES = 10        # 한 판에서 판독을 시도할 프레임 수 (선명한 순). 결과 화면을 촘촘히 읽으면 한 판에 10~25프레임이 된다

# 점수 숫자 색은 랭크 구간으로 갈린다. 실측(결과 화면 점수 색상 H, 0~179): A~AAA(86~96.5%)=빨강·분홍 H≈175,
# S 이상(97.3~100.8%)=주황·금색 H≈21. 랭크 표의 글자색(A류 빨강, S류 주황)과 같다.
# 파랑(H≈100)은 80% 미만: D 랭크 아이콘이 뜬 완료 화면에서 13.4%와 3.7%가 파랑으로 나왔고, 60~80%(BBB~B)도
# 파랑이라고 확인했다 (C 50~60%는 직접 못 봤다). 점수가 올라가는 도중의 프레임이 섞일 수 있어 가장 늦은 프레임의 색만 본다.
_TIER_ROI = (300, 420, 60, 560)
_TIER_MIN_PIXELS = 3000
_TIER_RANGE = {"blue": (0.0, 80.0), "red": (80.0, 97.0), "gold": (97.0, 101.5)}


def score_tier(img: np.ndarray) -> Optional[str]:
    """점수 숫자 색으로 달성률 구간 추정: "blue"(80% 미만) / "red"(80~97%) / "gold"(97% 이상) / None(숫자가 안 보임)."""
    y1, y2, x1, x2 = _TIER_ROI
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    m = (hsv[..., 1] > 110) & (hsv[..., 2] > 150)
    if int(m.sum()) < _TIER_MIN_PIXELS:
        return None
    mode = int(np.bincount(hsv[..., 0][m].astype(np.int32), minlength=180).argmax())
    if mode >= 165 or mode <= 5:
        return "red"
    if 10 <= mode <= 32:
        return "gold"
    if 90 <= mode <= 130:
        return "blue"
    return None


def read_banner(img: np.ndarray) -> tuple[Optional[float], Optional[float], bool]:
    """배너에서 (MY BEST, 개선폭, NEW RECORD 라벨 유무). 왼쪽 숫자가 MY BEST, 오른쪽이 개선폭, 부호는 읽지 않는다."""
    roi = img[_BEST_Y1:_BEST_Y2, _BEST_X1:_BEST_X2]
    if roi.size == 0:
        return None, None, False
    try:
        result = _get_paddle_ocr().ocr(roi, cls=False)
    except Exception as e:
        print(f"  ⚠️  MY BEST OCR 실패: {e}")
        return None, None, False
    found: list[tuple[float, float]] = []      # (x 중심, 값)
    label = False
    for line in (result or []):
        for box, (text, _) in (line or []):
            squeezed = text.replace(" ", "")
            if "RECORD" in squeezed.upper():
                label = True
            xc = sum(pt[0] for pt in box) / len(box)
            for m in re.findall(r"\d{1,3}\.\d{4}", squeezed):
                found.append((xc, float(m)))
    if len(found) < 2:
        return None, None, label
    found.sort(key=lambda t: t[0])
    return found[0][1], found[-1][1], label


def read_record_banner(img: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    best, delta, _ = read_banner(img)
    return best, delta


def judge_record(ach: Optional[float], best: Optional[float], delta: Optional[float]) -> Optional[bool]:
    """달성률 = MY BEST ± 개선폭 이 성립하는 쪽으로 신기록 여부를 판정. 어느 쪽도 안 맞으면 None(판독 오류)."""
    if ach is None or best is None or delta is None:
        return None
    if abs(ach - (best + delta)) < _BEST_TOL:
        return True
    if abs(ach - (best - delta)) < _BEST_TOL:
        return False
    return None


@dataclass
class RecordReading:
    achievement: float
    my_best:     float
    delta:       float            # 개선폭(절댓값)
    new_record:  bool
    exact:       bool             # True: 세 숫자의 합이 정확히 맞음 / False: 두 값에서 계산해 복원한 값
    how:         str = ""
    frame_index: Optional[int] = None   # 이 값을 읽는 데 쓴 프레임 (입력 프레임 목록의 위치) — 썸네일로 쓴다


def _frame_reading(img: np.ndarray) -> dict:
    shifted = _shift_to_standard(img)
    best, delta, label = read_banner(shifted)
    ach = ocr_achievement(shifted, lo=0.0, strict=True)
    # 큰 숫자를 못 읽으면 MY BEST 값을 대신 돌려주는 경우가 있다 — 달성률로 쓰지 않는다
    if ach is not None and best is not None and abs(ach - best) < _BEST_TOL:
        ach = None
    return {"ach": ach, "best": best, "delta": delta, "label": label, "tier": score_tier(shifted)}


def _frame_of(readings: list, ach=None, best=None, delta=None) -> Optional[int]:
    """주어진 값을 가장 많이 읽어 낸 프레임(같으면 더 늦은 프레임 — 점수가 안정된 쪽)의 위치."""
    scored = []
    for r in readings:
        hit = sum(1 for key, want in (("ach", ach), ("best", best), ("delta", delta)) if want is not None and r[key] == want)
        if hit:
            scored.append((hit, r["idx"]))
    return max(scored)[1] if scored else None


def _resolve(readings: list) -> Optional[RecordReading]:
    """프레임별 읽기 값(시간순)을 모아 서로 맞는 조합을 찾는다."""
    from collections import Counter
    achs   = Counter(r["ach"]   for r in readings if r["ach"]   is not None)
    bests  = Counter(r["best"]  for r in readings if r["best"]  is not None)
    deltas = Counter(r["delta"] for r in readings if r["delta"] is not None)

    # ① 세 숫자의 합이 정확히 맞는 조합 — 서로 다른 프레임에서 읽은 값을 섞어도 된다
    exact = []
    for a, na in achs.items():
        for b, nb in bests.items():
            for d, nd in deltas.items():
                v = judge_record(a, b, d)
                if v is not None:
                    exact.append((na + nb + nd, a, b, d, v))
    if exact:
        _, a, b, d, v = max(exact)
        return RecordReading(a, b, d, v, True, "합 일치", _frame_of(readings, a, b, d))

    # ② 달성률과 MY BEST는 읽혔고 개선폭이 한 자리 틀린 경우 — 개선폭을 차이로 다시 계산한다
    derived = []
    for a, na in achs.items():
        for b, nb in bests.items():
            diff = abs(a - b)
            if diff >= _MIN_DIFF and any(abs(d - diff) <= _DELTA_SLACK for d in deltas):
                derived.append((na + nb, a, b, round(diff, 4), a > b))
    if derived:
        _, a, b, d, v = max(derived)
        return RecordReading(a, b, d, v, False, "달성률−MY BEST", _frame_of(readings, a, b))

    # ③ 달성률을 못 읽었지만 MY BEST와 개선폭은 읽힌 경우 — 부호를 정하면 달성률이 복원된다
    if bests and deltas:
        b = bests.most_common(1)[0][0]
        d = deltas.most_common(1)[0][0]
        n_banner = sum(1 for r in readings if r["best"] is not None)
        tier = next((r["tier"] for r in reversed(readings) if r["tier"] is not None), None)   # 가장 늦은 프레임(안정된 색)
        plus, minus = b + d, b - d
        sign = None
        if any(r["label"] for r in readings):
            sign = "+"                                     # NEW RECORD 라벨은 신기록 판에서만 뜬다
        elif tier is not None:
            lo, hi = _TIER_RANGE[tier]
            fits = [sg for sg, v in (("+", plus), ("-", minus)) if lo <= v < hi or (hi == 101.5 and v == hi)]
            if len(fits) == 1:
                sign = fits[0]                             # 점수 색 구간에 들어가는 쪽이 하나뿐이면 그쪽
        if sign is None and n_banner >= 2:
            sign = "-"                                     # 배너가 두 번 이상 읽혔는데 라벨이 한 번도 없다
        if sign == "+" and plus <= 101.5:
            return RecordReading(round(plus, 4), b, d, True, False, "MY BEST+개선폭", _frame_of(readings, None, b, d))
        if sign == "-" and minus >= 0:
            return RecordReading(round(minus, 4), b, d, False, False, "MY BEST−개선폭", _frame_of(readings, None, b, d))
    return None


def read_record(frames_1000: list[np.ndarray]) -> tuple[Optional[RecordReading], list]:
    """한 판의 결과 화면 프레임(시간순)에서 신기록 여부와 개선폭을 판독한다 → (판독 결과 또는 None, 프레임별 읽기 값).

    선명한 프레임부터 읽고, 읽을 때마다 지금까지의 값으로 판정을 시도해 정확히 맞으면 바로 멈춘다.
    """
    order = sorted(range(len(frames_1000)), key=lambda i: _sharpness(frames_1000[i]), reverse=True)[:_RECORD_TRIES]
    readings: dict = {}
    result = None
    for i in order:
        readings[i] = _frame_reading(frames_1000[i])
        readings[i]["idx"] = i
        result = _resolve([readings[k] for k in sorted(readings)])
        if result is not None and result.exact:
            break
    done = [readings[k] for k in sorted(readings)]
    if result is None:
        print("  [신기록] 판독 실패 — 달성률·MY BEST·개선폭으로 판정할 수 없음")
        return None, done
    mark = "" if result.exact else " (복원)"
    print(f"  [신기록] {'신기록' if result.new_record else '신기록 아님'}{mark} — MY BEST {result.my_best} 개선폭 {result.delta} 달성률 {result.achievement} [{result.how}]")
    return result, done


def find_record(frames_1000: list[np.ndarray]) -> Optional[RecordReading]:
    return read_record(frames_1000)[0]


def stable_achievement(readings: list) -> Optional[float]:
    """판정이 안 풀렸을 때 쓸 수 있는 달성률 — 서로 다른 프레임에서 같은 값이 두 번 이상 읽힌 것만."""
    from collections import Counter
    top = Counter(r["ach"] for r in readings if r["ach"] is not None).most_common(1)
    return top[0][0] if top and top[0][1] >= 2 else None


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

def identify_song(
    frame:     np.ndarray,
    titles:    list[str],
    raw_songs: list[dict],
) -> tuple[str, float, str]:
    """자켓 매칭을 주 신호로, 곡명 OCR을 보조로 곡을 식별.

    (곡명, 신뢰도, 판정 근거) 반환. 식별 실패 시 곡명은 빈 문자열.

    자켓은 고유하지만 우타게 제외 후에도 픽셀이 동일한 곡이 1쌍 있고, 흰 배경
    미니멀 자켓끼리는 점수가 접근한다. 그런 접전에서만 OCR이 후보를 가른다.
    """
    cands = jacket_index.match(frame, raw_songs, top_k=5)
    text  = normalize_ocr(ocr_song_title(frame))

    if cands:
        top_title, top_score = cands[0]
        margin = top_score - (cands[1][1] if len(cands) > 1 else 0.0)

        if top_score >= JACKET_CONFIRM_MIN:
            if margin >= JACKET_MARGIN_MIN:
                return top_title, top_score, f"자켓 {top_score:.3f}"

            # 후보 접전 — OCR로 가린다
            shortlist = [t for t, sc in cands if sc >= JACKET_CANDIDATE_MIN]
            if text and len(shortlist) > 1:
                pick, ratio = fuzzy_match(text, shortlist)
                if pick:
                    return pick, ratio, f"자켓 {top_score:.3f}(접전) + OCR {ratio:.2f}"
            # OCR이 없으면 1등을 쓰되 신뢰도는 마진으로 낮게 준다
            return top_title, margin, f"자켓 {top_score:.3f}(접전, 마진 {margin:.3f})"

    # 자켓 최고점이 기준 미달 = 미등록 신곡일 가능성이 높다.
    # 이를 뒤집으려면 OCR이 확실해야 하므로 문턱을 높게 잡는다.
    if text:
        matched, ratio = fuzzy_match(text, titles)
        if matched and ratio >= 0.75:
            top = f"{cands[0][1]:.3f}" if cands else "없음"
            return matched, ratio, f"OCR 단독 {ratio:.2f} (자켓 최고점 {top})"

    return "", 0.0, "미인식"


# ── 크롭 위치 보정 ──────────────────────────────────────────────────────────
# 방송에 따라 YOLO 박스가 원 둘레의 장식 링까지 포함해서, 크롭 안의 게임 화면이 표준보다 위아래로
# 밀리거나 크게 보인다. 곡명 영역·자켓·채보 알약은 고정 좌표라 몇 px만 어긋나도 곡을 못 읽는다.
# 곡명 막대의 세로 위치는 정확하게(±1px) 구해지므로, 그만큼 이미지를 평행 이동해 표준 위치로 돌려놓는다.
# (가로 위치와 배율은 자켓이 막대에 붙어 불안정해서 쓰지 않는다 — 자켓 격자가 흡수한다.)
_BAR_REF   = (257, 194, 560, 39)          # 표준 곡명 막대(자켓 제외) x, y, 폭, 높이 — 구버전 결과 화면 13장 중앙값
_BAR_SCALES = (0.92, 0.96, 1.0, 1.04, 1.08, 1.12)
_SHIFT_MIN, _SHIFT_MAX = 4, 40            # 이 범위의 어긋남만 보정한다 (작으면 그대로, 크면 막대를 잘못 찾은 것)
_SHIFT_MIN_SCORE = 0.85                   # 막대 모양이 이만큼 맞을 때만 믿는다


def _bar_offset_y(img: np.ndarray) -> Optional[tuple]:
    """곡명 막대의 세로 위치가 표준에서 벗어난 정도 (어긋남 px, 일치 점수). 막대를 못 찾으면 None.

    남색 마스크를 만들어 막대 크기의 직사각형 템플릿을 여러 배율로 맞춘다.
    """
    h, s, v = cv2.split(cv2.cvtColor(img, cv2.COLOR_BGR2HSV))
    m = ((h >= 100) & (h <= 130) & (s >= 100) & (v >= 40) & (v <= 190)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (41, 9)))
    top, bot = 60, 360                     # 막대가 있을 법한 위쪽 띠만 탐색
    m = m[top:bot].astype(np.float32)
    best_score, best_y = -1.0, None
    for sc in _BAR_SCALES:
        w, hh = int(round(_BAR_REF[2] * sc)), int(round(_BAR_REF[3] * sc))
        pad = max(4, hh // 5)
        t = np.zeros((hh + 2 * pad, w + 2 * pad), np.float32)
        t[pad:pad + hh, pad:pad + w] = 1.0
        if t.shape[0] >= m.shape[0] or t.shape[1] >= m.shape[1]:
            continue
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(m, t, cv2.TM_CCOEFF_NORMED))
        if score > best_score:
            best_score, best_y = score, loc[1] + pad + top
    if best_y is None:
        return None
    return best_y - _BAR_REF[1], best_score


def _shift_to_standard(img: np.ndarray) -> np.ndarray:
    """곡명 막대의 세로 위치가 표준과 다르면 이미지를 평행 이동해 맞춘다. 표준 화면은 그대로 돌려준다."""
    found = _bar_offset_y(img)
    if found is None:
        return img
    oy, score = found
    if score < _SHIFT_MIN_SCORE or not (_SHIFT_MIN <= abs(oy) <= _SHIFT_MAX):
        return img
    M = np.float32([[1, 0, 0], [0, 1, -oy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def extract_from_frames(
    frames_1000: list[np.ndarray],
    titles:      list[str],
    raw_songs:   list[dict],
    fps:         int   = 30,
    skip_sec:    float = 0.3,
    video_ts:    Optional[float] = None,
    with_record: bool = False,
) -> Optional[SongResult]:
    """가장 선명한 프레임 1장에서 곡 정보를 추출 → SongResult. 실패 시 None.

    with_record=True 면 MY BEST·개선폭도 읽어 신기록 여부를 채운다. 달성률과 합이 맞는 프레임을
    선명한 순으로 찾고, 찾으면 그 프레임의 달성률을 쓴다(합이 맞는 것이 곧 교차 검증).
    """
    if not frames_1000:
        return None

    # find_song_bar를 게이트로 사용하지 않음 — 전체 프레임 선명도로 최적 프레임 선택
    best_frame = max(frames_1000, key=_sharpness)
    best_frame = _shift_to_standard(best_frame)   # 어긋난 방송 크롭을 표준 위치로

    # bar 위치 (detect_difficulty 용; 실패 시 고정 fallback)
    bar = find_song_bar(best_frame)
    if bar is not None:
        y1, y2  = bar
        x_start = _find_text_x_start(best_frame, y1, y2)
    else:
        y1, y2, x_start = 165, 200, 150

    matched_title, ratio, reason = identify_song(best_frame, titles, raw_songs)
    print(f"  [곡명] {matched_title or '미인식'} — {reason}")
    if not matched_title:
        return None

    diff           = detect_difficulty(best_frame, y1, x_start)
    achievement    = ocr_achievement(best_frame)
    chart_type     = detect_chart_type(best_frame)
    internal_level = get_internal_level(raw_songs, matched_title, diff, chart_type=chart_type)

    my_best = best_delta = new_record = record_exact = record_frame = None
    if with_record:
        found, readings = read_record(frames_1000)
        if found is not None:
            achievement, my_best, best_delta, new_record = found.achievement, found.my_best, found.delta, found.new_record
            record_exact = found.exact
            record_frame = found.frame_index
        else:
            # 큰 점수를 못 읽은 프레임에서 MY BEST 등 배너 숫자가 달성률로 딸려 나올 수 있다 — 믿을 만한 값만 쓴다
            achievement = stable_achievement(readings)

    return SongResult(
        title=matched_title,
        difficulty=diff,
        internal_level=internal_level,
        achievement=achievement,
        rank=achievement_to_rank(achievement) if achievement is not None else "",
        confidence=ratio,
        chart_type=chart_type,
        my_best=my_best,
        best_delta=best_delta,
        new_record=new_record,
        record_exact=record_exact,
        record_frame=record_frame,
    )
