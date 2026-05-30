"""네트워크 재시도 유틸리티."""
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_NO_RETRY_PHRASES = (
    "Sign in to confirm",
    "Login required",
    "quotaExceeded",
    "올바른 YouTube URL",
    "봇으로 인식",
)


def is_retryable(exc: BaseException) -> bool:
    """True면 재시도 가능, False면 즉시 raise."""
    msg = str(exc)
    for phrase in _NO_RETRY_PHRASES:
        if phrase in msg:
            return False
    try:
        import requests
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            if exc.response.status_code in (401, 403):
                return False
    except ImportError:
        pass
    return True


def with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    backoff: float = 2.0,
    label: str = "",
) -> T:
    """fn()을 최대 max_attempts회 시도. 실패 시 지수 백오프 후 재시도."""
    delay = base_delay
    last_exc: BaseException = RuntimeError("with_retry: max_attempts must be >= 1")

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_retryable(exc):
                raise
            if attempt < max_attempts:
                print(f"  ⚠️  {label} 재시도 {attempt}/{max_attempts - 1} ({delay:.0f}초 후)...")
                time.sleep(delay)
                delay *= backoff

    raise last_exc
