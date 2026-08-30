"""실행 전 환경 자동 점검."""
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config.settings import NO_WINDOW

_DESCS = {
    "ffmpeg":          "클립 커팅 및 라이브 녹화에 사용",
    "tesseract":       "영상에서 레이팅 숫자를 읽는 OCR 엔진",
    "yolo":            "영상에서 게임 화면 영역을 감지하는 AI 모델",
    "client_secret":   "본인 영상인지 확인하고 자동 업로드하는 데 사용",
    "cuda":            "AI 분석 속도 향상 (없으면 CPU로 동작)",
    "firefox_youtube": "영상 다운로드 및 스트림 접근에 사용",
    "jacket_index":    "곡 자켓 이미지로 곡명을 식별 (최초 1회 다운로드, 신곡만 추가)",
    "song_db":         "곡명·레벨 정보 (gekichumai/dxrating, 7일 캐시)",
}


def check_environment() -> list:
    """6개 항목을 점검하고 결과를 list[dict]로 반환."""

    def _ffmpeg():
        if shutil.which("ffmpeg") is None:
            return {
                "id": "ffmpeg", "label": "ffmpeg",
                "status": "error",
                "message": "setup/setup.bat을 다시 실행해 ffmpeg를 설치하세요.",
            }
        return {"id": "ffmpeg", "label": "ffmpeg", "status": "ok", "message": "정상"}

    def _tesseract():
        from config.settings import TESSERACT_CMD
        try:
            r = subprocess.run(
                [TESSERACT_CMD, "--version"], capture_output=True, timeout=5,
                creationflags=NO_WINDOW,
            )
            if r.returncode != 0:
                raise RuntimeError("returncode != 0")
        except Exception:
            return {
                "id": "tesseract", "label": "Tesseract OCR",
                "status": "error",
                "message": "setup/setup.bat을 다시 실행해 Tesseract OCR을 설치하세요.",
            }
        return {"id": "tesseract", "label": "Tesseract OCR", "status": "ok", "message": "정상"}

    def _yolo():
        from config.settings import MODEL_PATH
        if not Path(MODEL_PATH).exists():
            return {
                "id": "yolo", "label": "YOLO 모델",
                "status": "error",
                "message": "best_nano.pt 파일이 없습니다. 프로그램 파일이 손상됐을 수 있습니다.",
            }
        return {"id": "yolo", "label": "YOLO 모델", "status": "ok", "message": "정상"}

    def _client_secret():
        import json as _json
        from config.settings import CLIENT_SECRET, YOUTUBE_TOKEN

        _id = "client_secret"
        _label = "Google 인증 파일"

        # 1. client_secret.json 존재 확인
        if not CLIENT_SECRET.exists():
            return {
                "id": _id, "label": _label, "status": "warning",
                "message": "client_secret.json이 없습니다. README의 'client_secret.json 만드는 법'을 따라 설정하세요.",
            }

        # 2. JSON 구조 유효성 확인
        try:
            data = _json.loads(CLIENT_SECRET.read_text(encoding="utf-8"))
            if not ("installed" in data or "web" in data):
                raise ValueError("installed/web 키 없음")
        except Exception:
            return {
                "id": _id, "label": _label, "status": "error",
                "message": "client_secret.json 형식이 올바르지 않습니다. Google Cloud Console에서 다시 다운로드하세요.",
            }

        # 3. youtube_token.json 미존재 → 최초 인증 미완료
        if not YOUTUBE_TOKEN.exists():
            return {
                "id": _id, "label": _label, "status": "warning",
                "message": "아직 YouTube 계정 연동이 안 됐습니다. 파이프라인을 처음 실행하면 브라우저에서 인증 창이 열립니다.",
            }

        # 4. 토큰 로드 + 갱신 시도 (실제 인증 유효성 검증)
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google.auth.exceptions import RefreshError

            _SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
            creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN), _SCOPES)

            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        YOUTUBE_TOKEN.write_text(creds.to_json(), encoding="utf-8")
                    except RefreshError:
                        # 토큰 폐기됨 → 삭제 후 재인증 안내
                        YOUTUBE_TOKEN.unlink(missing_ok=True)
                        return {
                            "id": _id, "label": _label, "status": "warning",
                            "message": "YouTube 인증이 만료됐습니다. 파이프라인을 실행하면 브라우저에서 재인증됩니다.",
                        }
                else:
                    return {
                        "id": _id, "label": _label, "status": "warning",
                        "message": "YouTube 인증이 만료됐습니다. 파이프라인을 실행하면 브라우저에서 재인증됩니다.",
                    }
        except ImportError:
            # Google 라이브러리 미설치 시 파일 존재만으로 통과
            return {"id": _id, "label": _label, "status": "ok", "message": "파일 확인됨"}
        except Exception as e:
            return {
                "id": _id, "label": _label, "status": "error",
                "message": f"YouTube 인증 점검 실패: {e}",
            }

        return {"id": _id, "label": _label, "status": "ok", "message": "인증 완료"}

    def _cuda():
        from core.gpu import has_nvidia, onnx_uses_gpu
        _id, _label = "cuda", "GPU 가속"
        if onnx_uses_gpu():
            return {"id": _id, "label": _label, "status": "ok", "message": "정상"}
        if has_nvidia():
            return {
                "id": _id, "label": _label,
                "status": "warning",
                "message": "GPU는 있지만 가속 패키지가 없어 CPU로 실행됩니다. "
                           "setup/setup.bat 을 다시 실행하면 속도가 5배 빨라집니다.",
            }
        return {
            "id": _id, "label": _label,
            "status": "warning",
            "message": "GPU 가속을 사용할 수 없어 CPU로 실행됩니다. 분석 속도가 느릴 수 있습니다.",
        }

    def _jacket_index():
        from config.settings import JACKET_CDN_URL
        LABEL = "자켓 인덱스"
        try:
            from core import jacket_index
            from data.song_db import load_song_db
            _, raw = load_song_db("intl")
            if not raw:
                return {
                    "id": "jacket_index", "label": LABEL,
                    "status": "warning",
                    "message": "곡 DB를 불러오지 못해 인덱스를 확인할 수 없습니다.",
                }
            hashes, _feats = jacket_index._load()
            need = len(jacket_index._wanted(raw))
            if len(hashes) >= need:
                return {
                    "id": "jacket_index", "label": LABEL,
                    "status": "ok", "message": f"정상 ({len(hashes)}곡)",
                }
            # 부족분이 있으면 CDN 접근 가능 여부까지 확인
            import urllib.request
            probe = jacket_index._wanted(raw)[0]
            req = urllib.request.Request(JACKET_CDN_URL.format(probe),
                                         headers={"User-Agent": "maimai-clipper/1.0"})
            urllib.request.urlopen(req, timeout=10).read(1)
            return {
                "id": "jacket_index", "label": LABEL,
                "status": "warning",
                "message": f"새 곡 {need - len(hashes)}개의 자켓을 받을 수 있습니다.",
            }
        except Exception as e:
            return {
                "id": "jacket_index", "label": LABEL,
                "status": "error",
                "message": f"자켓 CDN에 접근할 수 없습니다: {e}",
            }

    def _song_db():
        import datetime
        from config.settings import PROJECT_DIR
        cache_path = PROJECT_DIR / "config" / "song_db_cache.json"
        cache_ttl  = datetime.timedelta(days=7)

        if cache_path.exists():
            try:
                mtime = datetime.datetime.fromtimestamp(cache_path.stat().st_mtime)
                age   = datetime.datetime.now() - mtime
                if age < cache_ttl:
                    days = age.days
                    return {"id": "song_db", "label": "maimai DB", "status": "ok", "message": f"정상 ({days}일 전 캐시)"}
            except Exception:
                pass

        try:
            from data.song_db import load_song_db
            titles, _ = load_song_db(region="intl")
            if titles:
                return {"id": "song_db", "label": "maimai DB", "status": "ok", "message": f"정상 (갱신됨, {len(titles)}곡)"}
            raise RuntimeError("곡 목록 없음")
        except Exception as e:
            if cache_path.exists():
                return {"id": "song_db", "label": "maimai DB", "status": "warning", "message": f"갱신 실패 — 이전 캐시 사용 중: {e}"}
            return {"id": "song_db", "label": "maimai DB", "status": "error", "message": f"곡 DB 로드 실패: {e}"}

    def _firefox_youtube():
        # yt-dlp 네트워크 요청 대신 browser_cookie3로 쿠키 직접 확인 (빠르고 신뢰성 높음)
        try:
            import browser_cookie3 as _bc3
            jar = _bc3.firefox(domain_name="youtube.com")
            # 로그인 상태일 때만 존재하는 YouTube 세션 쿠키
            login_cookies = {"SAPISID", "LOGIN_INFO", "__Secure-3PSID", "SID", "HSID"}
            has_session = any(c.name in login_cookies for c in jar)
            if not has_session:
                raise RuntimeError("no session cookies found")
        except Exception:
            return {
                "id": "firefox_youtube", "label": "YouTube 로그인",
                "status": "warning",
                "message": "Firefox에서 youtube.com에 로그인되어 있는지 확인하세요.",
            }
        return {"id": "firefox_youtube", "label": "YouTube 로그인", "status": "ok", "message": "정상"}

    _FALLBACKS = {
        "ffmpeg": {
            "id": "ffmpeg", "label": "ffmpeg",
            "status": "error",
            "message": "점검 중 오류가 발생했습니다. setup/setup.bat을 다시 실행하세요.",
        },
        "tesseract": {
            "id": "tesseract", "label": "Tesseract OCR",
            "status": "error",
            "message": "점검 중 오류가 발생했습니다. setup/setup.bat을 다시 실행하세요.",
        },
        "yolo": {
            "id": "yolo", "label": "YOLO 모델",
            "status": "error",
            "message": "점검 중 오류가 발생했습니다. best_nano.pt 파일을 확인하세요.",
        },
        "client_secret": {
            "id": "client_secret", "label": "Google 인증 파일",
            "status": "warning",
            "message": "점검 중 오류가 발생했습니다. client_secret.json을 확인하세요.",
        },
        "cuda": {
            "id": "cuda", "label": "GPU(CUDA)",
            "status": "warning",
            "message": "점검 중 오류가 발생했습니다. CPU로 실행됩니다.",
        },
        "firefox_youtube": {
            "id": "firefox_youtube", "label": "YouTube 로그인",
            "status": "warning",
            "message": "Firefox에서 youtube.com에 로그인되어 있는지 확인하세요.",
        },
        "jacket_index": {
            "id": "jacket_index", "label": "자켓 인덱스",
            "status": "error",
            "message": "점검 중 오류가 발생했습니다.",
        },
        "song_db": {
            "id": "song_db", "label": "maimai DB",
            "status": "warning",
            "message": "점검 중 오류가 발생했습니다.",
        },
    }

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            "ffmpeg":          ex.submit(_ffmpeg),
            "tesseract":       ex.submit(_tesseract),
            "yolo":            ex.submit(_yolo),
            "firefox_youtube": ex.submit(_firefox_youtube),
            "client_secret":   ex.submit(_client_secret),
            "jacket_index":    ex.submit(_jacket_index),
            "song_db":         ex.submit(_song_db),
            "cuda":            ex.submit(_cuda),
        }

        timeouts = {"firefox_youtube": 15, "jacket_index": 20, "song_db": 20}
        results = []
        for key, f in futures.items():
            try:
                results.append(f.result(timeout=timeouts.get(key, 10)))
            except Exception:
                results.append(_FALLBACKS[key])

    for r in results:
        r["desc"] = _DESCS.get(r["id"], "")
    return results
