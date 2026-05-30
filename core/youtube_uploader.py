"""YouTube Data API v3 업로드."""
import json
from pathlib import Path
from typing import Optional

from config.settings import CLIENT_SECRET, YOUTUBE_TOKEN

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


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
            creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN), SCOPES)

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

        try:
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status is None and response is None:
                    raise RuntimeError("next_chunk() returned (None, None) — 네트워크 오류")
                if status:
                    pct = status.progress()
                    print(f"    업로드 {int(pct * 100)}%...", end="\r")
                    print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'uploading', 'progress': round(pct, 2)}, ensure_ascii=False)}")
        except Exception as e:
            err = str(e)
            if "quotaExceeded" in err or "403" in err:
                _ecode = "quotaExceeded"
                print("    ⚠️  YouTube API 할당량 초과 — 오늘 업로드 불가 (파일은 highlights/ 에 보존됨)")
            elif "uploadLimitExceeded" in err:
                _ecode = "uploadLimitExceeded"
                print("    ⚠️  YouTube 계정 업로드 한도 초과 — 하루 업로드 가능 수를 초과했습니다 (파일은 highlights/ 에 보존됨)")
            else:
                _ecode = "uploadFailed"
                print(f"    ⚠️  업로드 실패: {e}")
            print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'failed', 'error': _ecode}, ensure_ascii=False)}")
            return None

        video_id = response.get("id", "")
        url = f"youtu.be/{video_id}"
        print(f"    ✅  업로드 완료: https://{url}        ")
        print(f"[HL_UPD] {json.dumps({'file': fname, 'status': 'uploaded', 'url': url, 'date': '방금 전'}, ensure_ascii=False)}")
        return video_id
