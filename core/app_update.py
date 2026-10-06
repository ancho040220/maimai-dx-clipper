"""앱 새 버전 확인 — GitHub 최신 릴리스 번호만 조회한다.

보내는 것은 공개 API 요청 하나뿐이고 사용자 정보는 없다. 자동으로 내려받거나 교체하지 않는다.
알림과 릴리스 페이지 링크까지만 한다. 실행 중인 파일을 덮어쓸 수 없고, 코드 서명이 없어서
무단 교체는 안전하지 않기 때문이다. 어떤 실패도 앱 동작에 영향을 주지 않는다(None).
"""
import json
import re
import time
import urllib.request
from typing import Optional

from config.settings import CACHE_DIR
from config.version import APP_VERSION

REPO        = "ancho040220/maimai-dx-clipper"
_API_URL    = f"https://api.github.com/repos/{REPO}/releases/latest"
_PAGE_PREFIX = f"https://github.com/{REPO}/"
_CACHE_PATH = CACHE_DIR / "app_update.json"
_CACHE_TTL  = 6 * 3600     # 인증 없는 GitHub API 는 시간당 60회라서 자주 부르지 않는다


def _parse(tag: str) -> Optional[tuple]:
    """'v2.0.3' -> (2, 0, 3). 숫자 버전이 아니면 None."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_release_url(url: str) -> bool:
    """릴리스 페이지 열기에 쓰는 주소가 이 저장소 것인지 (임의 주소를 열지 않는다)."""
    return isinstance(url, str) and url.startswith(_PAGE_PREFIX)


def _read_cache() -> Optional[dict]:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if time.time() - float(data["checked_at"]) < _CACHE_TTL and _parse(data["tag"]):
            return data
    except Exception:
        pass
    return None


def _fetch(timeout: float) -> Optional[dict]:
    req = urllib.request.Request(_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"maimai-dx-clipper/{APP_VERSION}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag, url = data.get("tag_name", ""), data.get("html_url", "")
    if not _parse(tag) or not is_release_url(url):
        return None
    return {"checked_at": time.time(), "tag": tag, "url": url}


def check_latest(timeout: float = 6.0) -> Optional[dict]:
    """최신 릴리스 정보. 조회·해석에 실패하면 None.

    반환: {"current": "2.0.3", "latest": "2.0.5", "url": "...", "newer": True}
    """
    info = _read_cache()
    if info is None:
        try:
            info = _fetch(timeout)
        except Exception:
            return None
        if info is None:
            return None
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(json.dumps(info), encoding="utf-8")
        except Exception:
            pass
    latest, current = _parse(info["tag"]), _parse(APP_VERSION)
    return {
        "current": APP_VERSION,
        "latest":  info["tag"].lstrip("v"),
        "url":     info["url"],
        "newer":   bool(latest and current and latest > current),
    }
