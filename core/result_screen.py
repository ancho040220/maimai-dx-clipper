"""결과 화면 탐지 — 크롭(1000×1000)의 곡명 막대 위치와 폭만 본다. OCR·모델 없이 cv2/numpy만 쓴다."""
from typing import Optional

import cv2
import numpy as np

# 곡명 막대(자켓 포함 남색 띠)가 결과 화면에서 놓이는 범위. 선곡·전환 화면에도 막대 모양이 있어서
# 모양만으로는 오탐이 나므로 위치·폭 조건이 필수다.
# 표준 위치는 y≈0.194. 폭은 자켓이 막대에 붙는 정도에 따라 프레임마다 달라진다(같은 방송 안에서도
# 557~583, 다른 방송 620~679). 하한을 560으로 잡았다가 557~559px 프레임을 놓쳐서, 판독 가능한 프레임이
# 있는데도 판 전체를 못 읽은 적이 있다. 장식 링 방송은 게임 화면이 17px쯤 위로 올라가 있어서(y≈0.177)
# 위쪽을 넉넉히 연다. 하한을 500까지 낮춰도 메뉴·플레이 화면 오탐은 늘지 않았다.
_Y_MIN, _Y_MAX = 0.16, 0.21
_W_MIN, _W_MAX = 500, 730


def find_title_bar(img: np.ndarray) -> Optional[tuple]:
    """가로로 아주 긴 남색 띠(곡명 막대)를 위치와 상관없이 찾는다 → (x, y, w, h) 또는 None.

    곡명 글자가 띠를 위아래로 끊으므로 가로·세로로 넉넉히 이어 붙인 뒤 모양으로 거른다.
    """
    h, s, v = cv2.split(cv2.cvtColor(img, cv2.COLOR_BGR2HSV))
    mask = ((h >= 100) & (h <= 130) & (s >= 100) & (v >= 40) & (v <= 190)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (61, 13)))
    # 폭이 좁은 덩어리(자켓 썸네일, 작은 라벨)를 떼어낸다 — 긴 막대만 남는다
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(0.30 * img.shape[1]), 9)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    width = img.shape[1]
    best = None
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if w >= 0.40 * width and 14 <= hh <= 80 and w / hh >= 6 and area >= 0.6 * w * hh:
            if best is None or w > best[2]:
                best = (int(x), int(y), int(w), int(hh))
    return best


_NEIGHBOR_MARGIN = 10   # 막대는 위·아래 이웃보다 이만큼 이상 어두워야 한다
_IMG_V_MIN       = 60   # 화면 평균 밝기가 이보다 어두우면 결과 화면이 아니다 (암전·로고 화면)


def _stands_out(img: np.ndarray, bar: tuple) -> bool:
    """막대가 위·아래 이웃보다 어두운 띠로 도드라지는지, 화면이 너무 어둡지 않은지.

    결과 화면의 곡명 막대는 밝은 난이도 띠와 배경 사이의 어두운 남색 띠다. 전체가 어두운 남색인
    화면(암전·로고 장면)은 배경 전체가 '막대 색'이라 모양 조건만으로는 막대로 잘못 잡힌다.
    """
    x, y, w, h = bar
    if cv2.resize(img, (100, 100), interpolation=cv2.INTER_AREA).max(axis=2).mean() < _IMG_V_MIN:
        return False
    v = img.max(axis=2)
    inner = float(v[y:y + h, x:x + w].mean())
    above = v[max(0, y - 14):max(0, y - 3), x:x + w]
    below = v[y + h + 3:y + h + 14, x:x + w]
    if above.size == 0 or below.size == 0:
        return False
    return inner + _NEIGHBOR_MARGIN <= min(float(above.mean()), float(below.mean()))


def is_result_screen(crop_1000: np.ndarray) -> bool:
    """1000×1000 크롭이 결과 화면이면 True. 막대가 표준 위치·폭 범위에 있고 주변보다 도드라질 때만."""
    bar = find_title_bar(crop_1000)
    if bar is None:
        return False
    _, y, w, _ = bar
    if not (_Y_MIN <= y / crop_1000.shape[0] <= _Y_MAX and _W_MIN <= w <= _W_MAX):
        return False
    return _stands_out(crop_1000, bar)
