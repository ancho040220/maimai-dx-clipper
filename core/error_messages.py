"""오류 메시지 한국어 변환 및 로그 저장."""

_ERROR_PATTERNS = [
    ("sign in to confirm",        "YouTube 로그인이 필요합니다. Firefox에서 youtube.com에 로그인 후 다시 시도하세요."),
    ("unable to extract",         "영상 정보를 가져올 수 없습니다. URL이 올바른지 확인하거나 잠시 후 다시 시도하세요."),
    ("video unavailable",         "재생할 수 없는 영상입니다. 비공개이거나 삭제된 영상일 수 있습니다."),
    ("private video",             "비공개 영상입니다. Firefox에서 해당 계정으로 로그인되어 있는지 확인하세요."),
    ("http error 429",            "요청이 너무 많습니다. 잠시 후 다시 시도하세요."),
    ("http error 403",            "접근이 거부됐습니다. 로그인 상태 또는 영상 권한을 확인하세요."),
    # 자켓 CDN — 더 일반적인 네트워크 패턴보다 앞에 둬야 구체적인 안내가 나간다
    ("dxrating.net",              "곡 자켓 서버에 연결할 수 없습니다. 이미 받아둔 자켓으로는 계속 동작하며, 신곡 자켓은 다음 실행 때 다시 시도합니다."),
    ("jacket_index",              "자켓 인덱스를 읽을 수 없습니다. cache/jacket_index.npz를 삭제하면 다음 실행 때 새로 만듭니다."),
    ("connection reset",          "네트워크 연결이 끊겼습니다. 인터넷 연결을 확인하세요."),
    ("getaddrinfo failed",        "인터넷에 연결할 수 없습니다. 네트워크 연결을 확인하세요."),
    ("timed out",                 "서버 응답 시간이 초과됐습니다. 인터넷 연결을 확인하거나 잠시 후 다시 시도하세요."),
    ("no such host",              "서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요."),
    ("quotaexceeded",             "YouTube API 할당량이 초과됐습니다. 내일 다시 시도하세요."),
    ("uploadlimitexceeded",       "오늘 YouTube 업로드 한도에 도달했습니다. 내일 다시 시도하세요."),
    ("invalid_grant",             "Google 인증이 만료됐습니다. config/credentials/youtube_token.json을 삭제 후 재실행하세요."),
    ("access_denied",             "Google 계정 접근이 거부됐습니다. OAuth 동의 화면에서 본인 계정을 테스트 사용자로 추가했는지 확인하세요."),
    ("client_secret",             "Google 인증 파일이 없습니다. README의 'client_secret.json 만드는 법'을 따라 설정하세요."),
    ("no such file or directory", "파일을 찾을 수 없습니다. 경로를 확인하세요."),
    ("permission denied",         "파일 접근 권한이 없습니다. 해당 폴더의 쓰기 권한을 확인하세요."),
    ("no space left",             "디스크 공간이 부족합니다. 저장 공간을 확보 후 다시 시도하세요."),
    ("disk full",                 "디스크 공간이 부족합니다. 저장 공간을 확보 후 다시 시도하세요."),
    ("ffmpeg",                    "영상 처리 중 오류가 발생했습니다. setup/setup.bat을 다시 실행해 ffmpeg를 재설치하세요."),
    ("tesseract",                 "OCR 엔진 오류입니다. setup/setup.bat을 다시 실행하세요."),
    ("cuda out of memory",        "GPU 메모리가 부족합니다. 다른 프로그램을 종료 후 다시 시도하세요."),
    ("cuda",                      "GPU(CUDA) 오류가 발생했습니다. CPU 모드로 재시도합니다."),
    ("best_nano",                 "AI 모델 파일(best_nano.onnx)을 찾을 수 없습니다. 프로그램 파일이 손상됐을 수 있습니다."),
]


def translate_error(error) -> str:
    """예외 또는 문자열을 받아 한국어 메시지 반환. 매칭 없으면 폴백."""
    original = str(error)
    # 이미 한국어 메시지면 그대로 반환 (이중 변환 방지)
    if any('가' <= c <= '힣' for c in original):
        return original[:500] + ("..." if len(original) > 500 else "")
    text = original.lower()
    for pattern, message in _ERROR_PATTERNS:
        if pattern in text:
            return message
    short = original[:200] + ("..." if len(original) > 200 else "")
    return f"알 수 없는 오류가 발생했습니다.\n오류 내용: {short}"


def log_error(error, context: str = "") -> None:
    """원본 오류를 logs/error.log에 저장."""
    import traceback
    from datetime import datetime
    from config.settings import PROJECT_DIR

    try:
        logs_dir = PROJECT_DIR / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / "error.log"

        if log_file.exists() and log_file.stat().st_size > 10 * 1024 * 1024:
            log_file.write_text("", encoding="utf-8")

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
            if context:
                f.write(f" [{context}]")
            f.write("\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass
