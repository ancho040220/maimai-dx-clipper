"""YouTube Data API v3 업로드."""
import json
from pathlib import Path
from typing import Optional

from config.settings import CLIENT_SECRET, YOUTUBE_TOKEN

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # 업로드 대상 채널이 본인 채널인지 확인하는 데 필요 (channels.list mine=true).
    # 스코프가 바뀌면 기존 토큰이 무효가 되어 재인증이 일어난다.
    "https://www.googleapis.com/auth/youtube.readonly",
]

_RETRIABLE_STATUS   = frozenset({500, 502, 503, 504})   # 일시적 서버 오류 → 재시도
_UPLOAD_MAX_RETRIES = 5


def _http_error_reason(exc) -> str:
    """HttpError content에서 error reason 추출 (문자열 매칭 대신 구조화 파싱)."""
    try:
        data = json.loads(exc.content.decode("utf-8"))
        errs = data.get("error", {}).get("errors", [])
        if errs:
            return errs[0].get("reason", "")
    except Exception:
        pass
    return ""


class YouTubeUploader:
    """
    최초 실행 시 브라우저에서 OAuth 인증 → youtube_token.json에 토큰 저장.
    이후엔 토큰 자동 갱신.

    사전 준비:
        Google Cloud Console에서 OAuth 2.0 자격증명 다운로드
        → config/credentials/client_secret.json 에 저장
    """

    def __init__(self):
        self._yt = None

    def authenticate(self):
        """OAuth 인증을 즉시 수행 (파이프라인 시작 전 사전 검증용)."""
        self._authenticate()

    def _authenticate(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "필요 패키지 없음:\n"
                "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )

        creds = None
        if YOUTUBE_TOKEN.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN), SCOPES)
            except Exception:
                creds = None
            # readonly 스코프를 추가하기 전에 발급된 토큰은 권한이 모자라므로 다시 받는다
            if creds is not None and not creds.has_scopes(SCOPES):
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CLIENT_SECRET.exists():
                    raise FileNotFoundError(
                        f"client_secret.json 없음: {CLIENT_SECRET}\n"
                        "Google Cloud Console → API 및 서비스 → 사용자 인증 정보 → "
                        "OAuth 2.0 클라이언트 ID 다운로드 후 config/credentials/ 에 저장하세요."
                    )
                flow  = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
                creds = flow.run_local_server(port=0)
            YOUTUBE_TOKEN.write_text(creds.to_json(), encoding="utf-8")

        self._yt = build("youtube", "v3", credentials=creds)

    def my_channel_id(self) -> Optional[str]:
        """인증된 계정의 채널 ID. 조회 실패 시 None."""
        if self._yt is None:
            self._authenticate()
        try:
            res = self._yt.channels().list(part="id", mine=True).execute()
            items = res.get("items", [])
            return items[0]["id"] if items else None
        except Exception:
            return None

    def upload(self, video_path: Path, title: str, description: str) -> Optional[str]:
        """영상을 YouTube(미등록)로 업로드하고 video ID를 반환. 실패 시 None."""
        from googleapiclient.http import MediaFileUpload

        if self._yt is None:
            self._authenticate()

        fname = video_path.name
        print(f"    📤  YouTube 업로드 중: {title}")
        print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'uploading', 'progress': 0}, ensure_ascii=False)}")
        body = {
            "snippet": {
                "title":       title,
                "description": description,
                "tags":        ["maimai", "maimaidx", "레이팅", "rating"],
                "categoryId":  "20",
            },
            "status": {"privacyStatus": "unlisted"},
        }
        media   = MediaFileUpload(str(video_path), chunksize=10 * 1024 * 1024, resumable=True)
        request = self._yt.videos().insert(part="snippet,status", body=body, media_body=media)

        from googleapiclient.errors import HttpError
        import socket, ssl, time

        _retriable_exc = (socket.timeout, ssl.SSLError, ConnectionError, OSError)

        def _fail(ecode: str, msg: str) -> None:
            print(f"    ⚠️  {msg}")
            print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'failed', 'error': ecode}, ensure_ascii=False)}")

        response = None
        retry    = 0
        # resumable 세션이므로 일시 오류 시 next_chunk() 재호출로 이어받는다 (A-12)
        while response is None:
            error_label = None
            try:
                status, response = request.next_chunk()
                if status is None and response is None:
                    error_label = "빈 응답"
                elif status:
                    pct = status.progress()
                    print(f"    업로드 {int(pct * 100)}%...", end="\r")
                    print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'uploading', 'progress': round(pct, 2)}, ensure_ascii=False)}")
            except HttpError as e:
                code = getattr(e.resp, "status", None)
                if code not in _RETRIABLE_STATUS:
                    # 재시도 불가 — 상태코드/reason 구조화 분류 (A-24)
                    reason = _http_error_reason(e)
                    if code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
                        _fail("quotaExceeded", "YouTube API 할당량 초과 — 오늘 업로드 불가 (파일은 highlights/ 에 보존됨)")
                    elif reason == "uploadLimitExceeded":
                        _fail("uploadLimitExceeded", "YouTube 계정 업로드 한도 초과 (파일은 highlights/ 에 보존됨)")
                    elif code in (401, 403):
                        _fail("authFailed", f"업로드 권한/인증 오류 (HTTP {code}, {reason or '원인 미상'}) — 재인증이 필요할 수 있습니다")
                    else:
                        _fail("uploadFailed", f"업로드 실패 (HTTP {code}, {reason or str(e)[:80]})")
                    return None
                error_label = f"HTTP {code}"
            except _retriable_exc as e:
                error_label = type(e).__name__
            except Exception as e:
                error_label = str(e)[:60]

            if error_label:
                retry += 1
                if retry > _UPLOAD_MAX_RETRIES:
                    _fail("uploadFailed", f"업로드 실패: {error_label} — {_UPLOAD_MAX_RETRIES}회 재시도 후 포기 (파일은 highlights/ 에 보존됨)")
                    return None
                delay = min(2 ** retry, 30)
                print(f"    ⚠️  업로드 일시 오류({error_label}) — {delay}초 후 재시도 {retry}/{_UPLOAD_MAX_RETRIES}...")
                time.sleep(delay)

        video_id = response.get("id", "")
        url = f"youtu.be/{video_id}"
        print(f"    ✅  업로드 완료: https://{url}        ")
        print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'uploaded', 'url': url, 'date': '방금 전'}, ensure_ascii=False)}")
        return video_id
