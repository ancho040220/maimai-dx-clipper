"""곡 자켓 이미지 기반 곡명 식별.

dxdata 의 imageName(SHA-256) 으로 CDN에서 자켓을 받아 축소 특징벡터 인덱스를
만들고, 결과 화면의 자켓 크롭과 코사인 최근접 매칭한다.

곡명 텍스트를 읽지 않으므로 CJK 인식 정확도에 의존하지 않는다. 신곡은 dxdata
갱신 시 imageName 차집합만 추가로 받으면 되고, 인덱스에 없는 곡은 최근접
점수가 낮게 나와 미등록으로 걸러진다.
"""
import concurrent.futures as _cf
import urllib.request
from typing import Optional

import cv2
import numpy as np

from config.settings import (
    CACHE_DIR, JACKET_CDN_URL, JACKET_FEAT_N, JACKET_UTAGE_CATEGORY,
)

_INDEX_PATH = CACHE_DIR / "jacket_index.npz"

# 결과 화면 자켓 크롭 후보 (1000×1000 기준 y, x, 한 변).
# 게임 화면상 자켓은 난이도 색 테두리에 둘러싸여 있어 크롭이 몇 px만 어긋나도
# 테두리가 섞여 점수가 급락한다. 정렬이 맞을 때 점수가 뚜렷하게 최대가 되므로
# 후보를 훑어 최고점을 취하는 방식으로 ROI를 자가 보정한다.
_GRID = [
    (159 + dy, 168 + dx, 72 + ds)
    for dy in range(-6, 7, 2)
    for dx in range(-6, 7, 2)
    for ds in (-4, 0, 4)
]

_cache: Optional[tuple[list[str], np.ndarray]] = None


def _feat(img: np.ndarray) -> np.ndarray:
    """축소 → 평균 제거 → L2 정규화. 밝기·대비 차이를 흡수한다."""
    x = cv2.resize(img, (JACKET_FEAT_N, JACKET_FEAT_N), interpolation=cv2.INTER_AREA)
    v = x.astype(np.float32).ravel()
    v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-6)


def _fetch(image_name: str) -> Optional[np.ndarray]:
    url = JACKET_CDN_URL.format(image_name)
    req = urllib.request.Request(url, headers={"User-Agent": "maimai-clipper/1.0"})
    for _ in range(3):
        try:
            data = urllib.request.urlopen(req, timeout=20).read()
            if data:
                return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            continue
    return None


def _wanted(raw_songs: list[dict]) -> list[str]:
    """인덱싱 대상 imageName — 우타게는 원곡 자켓을 공유하므로 제외."""
    return sorted({
        s["imageName"] for s in raw_songs
        if s.get("imageName") and s.get("category") != JACKET_UTAGE_CATEGORY
    })


def _load() -> tuple[list[str], np.ndarray]:
    if not _INDEX_PATH.exists():
        return [], np.zeros((0, JACKET_FEAT_N * JACKET_FEAT_N * 3), np.float32)
    try:
        z = np.load(_INDEX_PATH, allow_pickle=False)
        return list(z["hashes"]), z["feats"].astype(np.float32)
    except Exception:
        return [], np.zeros((0, JACKET_FEAT_N * JACKET_FEAT_N * 3), np.float32)


def _save(hashes: list[str], feats: np.ndarray) -> None:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(_INDEX_PATH, hashes=np.array(hashes), feats=feats.astype(np.float32))


def pending(raw_songs: list[dict]) -> int:
    """아직 인덱스에 없는 곡 수 — 곡 DB가 갱신되면 그만큼 늘어난다."""
    have = set(_load()[0])
    return sum(1 for h in _wanted(raw_songs) if h not in have)


def ensure_index(raw_songs: list[dict], quiet: bool = False) -> tuple[list[str], np.ndarray]:
    """인덱스를 최신 곡 DB에 맞춘다. 새로 생긴 imageName 만 내려받는다."""
    global _cache

    hashes, feats = _load()
    have = {h: i for i, h in enumerate(hashes)}
    want = _wanted(raw_songs)
    missing = [h for h in want if h not in have]

    if missing:
        if not quiet:
            print(f"  자켓 인덱스 갱신 — {len(missing)}곡 다운로드 중...")
        got_h, got_f, failed = [], [], 0
        with _cf.ThreadPoolExecutor(8) as ex:
            for h, img in zip(missing, ex.map(_fetch, missing)):
                if img is None:
                    failed += 1
                    continue
                got_h.append(h)
                got_f.append(_feat(img))
        if got_f:
            hashes = hashes + got_h
            feats = np.vstack([feats, np.stack(got_f)]) if len(feats) else np.stack(got_f)
            _save(hashes, feats)
        if not quiet:
            msg = f"  자켓 인덱스: {len(hashes)}곡"
            if failed:
                msg += f" (다운로드 실패 {failed}곡 — 다음 실행 시 재시도)"
            print(msg)

    _cache = (hashes, feats)
    return _cache


def match(
    frame_1000: np.ndarray,
    raw_songs:  list[dict],
    top_k:      int = 5,
) -> list[tuple[str, float]]:
    """결과 화면에서 자켓을 매칭해 (곡명, 점수) 를 점수 내림차순으로 반환.

    점수는 코사인 유사도(-1~1). 인덱스가 비어 있으면 빈 리스트를 반환한다.
    """
    global _cache
    if _cache is None:
        ensure_index(raw_songs)
    hashes, feats = _cache
    if not hashes:
        return []

    h, w = frame_1000.shape[:2]
    crops = []
    for (y, x, s) in _GRID:
        if y < 0 or x < 0 or y + s > h or x + s > w:
            continue
        crops.append(_feat(frame_1000[y:y + s, x:x + s]))
    if not crops:
        return []

    sims = np.stack(crops) @ feats.T            # (후보, 곡)
    gi, _ = np.unravel_index(sims.argmax(), sims.shape)
    row = sims[gi]                               # 정렬이 가장 잘 맞은 크롭의 전곡 점수

    # imageName → 곡명 (우타게 제외 후에는 1:1)
    h2t: dict[str, str] = {}
    for s in raw_songs:
        if s.get("category") != JACKET_UTAGE_CATEGORY and s.get("imageName"):
            h2t.setdefault(s["imageName"], s["title"])

    order = np.argsort(-row)[:top_k]
    return [(h2t[hashes[i]], float(row[i])) for i in order if hashes[i] in h2t]
