"""gekichumai/dxrating JSON 기반 곡 DB (7일 캐시, 자동 갱신)."""
import datetime
import json
import urllib.request
from pathlib import Path
from typing import Optional

from config.settings import PROJECT_DIR

_DXDATA_URL = (
    "https://raw.githubusercontent.com/gekichumai/dxrating/main"
    "/packages/dxdata/dxdata.json"
)
_CACHE_PATH = PROJECT_DIR / "config" / "song_db_cache.json"
_CACHE_TTL  = datetime.timedelta(days=7)

_DIFF_KEY = {
    "BASIC": "basic", "ADVANCED": "advanced", "EXPERT": "expert",
    "MASTER": "master", "Re:MASTER": "remaster",
}

_MEM_CACHE: Optional[tuple[list[str], list[dict]]] = None


def _fetch_raw() -> list[dict]:
    req    = urllib.request.Request(_DXDATA_URL, headers={"User-Agent": "maimai-clipper/1.0"})
    data   = urllib.request.urlopen(req, timeout=15).read()
    parsed = json.loads(data.decode("utf-8"))
    # 최상위가 {"songs": [...]} 형태인 경우 대응
    if isinstance(parsed, dict):
        return parsed.get("songs", [])
    return parsed


def _cache_fresh() -> bool:
    if not _CACHE_PATH.exists():
        return False
    age = datetime.datetime.now() - datetime.datetime.fromtimestamp(_CACHE_PATH.stat().st_mtime)
    return age < _CACHE_TTL


def _load_cache() -> Optional[list[dict]]:
    try:
        parsed = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return parsed.get("songs", []) or None
        return parsed if parsed else None
    except Exception:
        return None


def _save_cache(raw: list[dict]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_song_db(region: str = "intl") -> tuple[list[str], list[dict]]:
    """(titles, raw_songs) 반환. region="" 이면 전체 곡 포함."""
    global _MEM_CACHE
    if _MEM_CACHE is not None:
        return _MEM_CACHE

    raw: Optional[list[dict]] = None

    if _cache_fresh():
        raw = _load_cache()

    if raw is None:
        try:
            raw = _fetch_raw()
            _save_cache(raw)
            print(f"    song DB 갱신 완료 ({len(raw)}곡)")
        except Exception as e:
            print(f"⚠️  song DB fetch 실패: {e} — 캐시 사용")
            raw = _load_cache()

    if raw is None:
        print("⚠️  song DB 사용 불가 (캐시 없음, 네트워크 실패)")
        return [], []

    def _in_region(song: dict) -> bool:
        return any(
            sh.get("regions", {}).get(region)
            for sh in song.get("sheets", [])
        )

    filtered = [s for s in raw if _in_region(s)] if region else raw
    # 宴会場(우타게) 보면은 레이팅에 반영되지 않아 클리핑 대상이 아니므로 후보에서 제외
    titles = sorted({s["title"] for s in filtered if s.get("category") != "宴会場"})
    _MEM_CACHE = (titles, raw)
    return _MEM_CACHE


def get_internal_level(
    raw_songs: list[dict],
    title: str,
    difficulty: str,
    region: str = "intl",
) -> Optional[float]:
    """title + difficulty → internalLevelValue. regionOverrides 우선."""
    diff_key = _DIFF_KEY.get(difficulty, difficulty.lower())

    for song in raw_songs:
        if song.get("title") != title:
            continue
        for sheet in song.get("sheets", []):
            if sheet.get("difficulty") != diff_key:
                continue
            overrides = sheet.get("regionOverrides", {}).get(region, {})
            if "internalLevelValue" in overrides:
                return float(overrides["internalLevelValue"])
            val = sheet.get("internalLevelValue")
            if val is not None:
                return float(val)
    return None
