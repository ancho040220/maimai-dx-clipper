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
    "client_secret":   "YouTube 자동 업로드 인증에만 사용",
    "cuda":            "AI 분석 속도 향상 (없으면 CPU로 동작)",
    "firefox_youtube": "영상 다운로드 및 스트림 접근에 사용",
    "clova_ocr":       "일본어 곡명 인식에 사용하는 CLOVA OCR API",
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
        import sys
        # bridge.py가 메인 스레드에서 미리 로드했으면 재사용 (DLL 재로딩 방지)
        torch = sys.modules.get("torch")
        if torch is None:
            try:
                import torch
            except ModuleNotFoundError:
                return {
                    "id": "cuda", "label": "GPU(CUDA)",
                    "status": "warning",
                    "message": "PyTorch가 설치되어 있지 않습니다. setup/setup.bat을 다시 실행하세요.",
                }
            except Exception:
                # torch는 설치됐지만 CUDA DLL 로드 실패 (드라이버 버전 불일치 등)
                return {
                    "id": "cuda", "label": "GPU(CUDA)",
                    "status": "warning",
                    "message": "PyTorch DLL 로드 실패 — NVIDIA 드라이버를 최신으로 업데이트하거나 "
                               "setup/setup.bat을 다시 실행하세요.",
                }
        try:
            cuda_ok = torch.cuda.is_available()
        except Exception:
            cuda_ok = False
        if not cuda_ok:
            return {
                "id": "cuda", "label": "GPU(CUDA)",
                "status": "warning",
                "message": "CUDA를 사용할 수 없어 CPU로 실행됩니다. 분석 속도가 느릴 수 있습니다.",
            }
        return {"id": "cuda", "label": "GPU(CUDA)", "status": "ok", "message": "정상"}

    def _clova_ocr():
        from config.settings import CREDENTIALS_DIR, CLOVA_OCR_URL, CLOVA_OCR_SECRET
        cred_path = CREDENTIALS_DIR / "clova_ocr.txt"
        if not cred_path.exists():
            return {
                "id": "clova_ocr", "label": "CLOVA OCR",
                "status": "error",
                "message": f"인증 파일이 없습니다. config/credentials/clova_ocr.txt를 생성하고 CLOVA_OCR_URL과 CLOVA_OCR_SECRET을 입력하세요.",
            }
        if not CLOVA_OCR_URL or not CLOVA_OCR_SECRET:
            return {
                "id": "clova_ocr", "label": "CLOVA OCR",
                "status": "error",
                "message": "clova_ocr.txt에 CLOVA_OCR_URL 또는 CLOVA_OCR_SECRET이 없습니다.",
            }
        try:
            import requests as _req
            import base64, uuid, numpy as np, cv2
            blank = np.zeros((10, 10, 3), dtype=np.uint8)
            _, buf = cv2.imencode('.jpg', blank)
            img_b64 = base64.b64encode(buf).decode('utf-8')
            payload = {
                "version": "V2",
                "requestId": str(uuid.uuid4()),
                "timestamp": 0,
                "images": [{"format": "jpg", "name": "test", "data": img_b64}],
            }
            headers = {"X-OCR-SECRET": CLOVA_OCR_SECRET, "Content-Type": "application/json"}
            resp = _req.post(CLOVA_OCR_URL, json=payload, headers=headers, timeout=10)
            if resp.status_code == 401:
                return {
                    "id": "clova_ocr", "label": "CLOVA OCR",
                    "status": "error",
                    "message": "인증 실패(401) — clova_ocr.txt의 Secret Key를 확인하세요.",
                }
            if resp.status_code not in (200, 400):
                return {
                    "id": "clova_ocr", "label": "CLOVA OCR",
                    "status": "warning",
                    "message": f"API 응답 코드 {resp.status_code} — URL이 올바른지 확인하세요.",
                }
        except Exception as e:
            return {
                "id": "clova_ocr", "label": "CLOVA OCR",
                "status": "warning",
                "message": f"API 연결 실패: {e}",
            }
        return {"id": "clova_ocr", "label": "CLOVA OCR", "status": "ok", "message": "정상"}

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
        "clova_ocr": {
            "id": "clova_ocr", "label": "CLOVA OCR",
            "status": "error",
            "message": "점검 중 오류가 발생했습니다. clova_ocr.txt를 확인하세요.",
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
            "clova_ocr":       ex.submit(_clova_ocr),
            "song_db":         ex.submit(_song_db),
            "cuda":            ex.submit(_cuda),
        }

        timeouts = {"firefox_youtube": 15, "clova_ocr": 15, "song_db": 20}
        results = []
        for key, f in futures.items():
            try:
                results.append(f.result(timeout=timeouts.get(key, 10)))
            except Exception:
                results.append(_FALLBACKS[key])

    for r in results:
        r["desc"] = _DESCS.get(r["id"], "")
    return results
