# maimai DX Rating Clipper 사용설명서

---

## 다운로드

**[▶ 최신 버전 다운로드 (maimai_clipper.exe)](https://github.com/ancho040220/maimai-dx-clipper/releases/latest)**

---

## 시스템 요구사항

| 항목 | 최소 | 권장 |
|------|------|------|
| OS | Windows 10 64-bit | Windows 11 64-bit |
| Python | 3.10 이상 | 3.11 이상 |
| RAM | 4GB | 8GB 이상 |
| GPU | — (CPU로도 동작) | DirectX 12 지원 GPU |
| 브라우저 | Firefox | Firefox |

> ⚠️ **현재 Windows 전용**입니다. macOS · Linux는 지원하지 않습니다.

> GPU가 있으면 자동으로 사용합니다. NVIDIA·AMD·Intel 어느 쪽이든 되고
> CUDA 설치가 필요 없으며, GPU가 없으면 CPU로 동작합니다.
> (기준 PC 실측: GPU 5.3ms/프레임 vs CPU 27.6ms)

---

## 최초 설치 (처음 한 번만)

`setup` 폴더 안의 **`setup.bat`** 파일을 더블클릭하면 됩니다.
검은 창이 열리면서 필요한 것을 순서대로 설치합니다.

1. **Python** — 없으면 자동으로 설치합니다.
   설치되면 *"Close this window and run setup.bat again"* 메시지가 나오는데,
   창을 닫고 `setup.bat`을 **한 번 더 실행**하세요. (PATH 반영에 필요합니다)
2. **Python 패키지**
3. **ffmpeg**
4. **Tesseract OCR**

*"Installation complete!"* 메시지가 나오면 아무 키나 눌러 닫으세요.

> Python이 없고 winget도 없는 환경이면 https://www.python.org/downloads 에서
> 직접 설치하세요. 설치 화면 맨 아래 **"Add python.exe to PATH"** 체크박스를
> 반드시 체크해야 합니다.

---

## YouTube 로그인 설정

yt-dlp가 **Firefox 브라우저의 로그인 세션**을 직접 읽어 사용합니다.  
별도 파일 내보내기가 필요 없고, Firefox에 로그인만 되어 있으면 됩니다.

1. **Firefox** 브라우저를 설치합니다 (없을 경우)
2. Firefox에서 **YouTube**(youtube.com)에 접속해 Google 계정으로 **로그인**
3. 그것으로 끝입니다 — 프로그램이 알아서 쿠키를 읽습니다

> 다른 브라우저(Edge, Chrome 등)를 쓰고 싶다면 환경변수 `YTDLP_BROWSER=chrome` 으로 지정할 수 있지만,  
> Chrome은 Windows DPAPI 암호화 문제로 실패할 수 있으므로 **Firefox 권장**합니다.

---


## client_secret.json 만드는 법

YouTube에 영상을 자동으로 업로드하기 위한 Google 인증 파일입니다.  
한 번만 만들면 됩니다.

### 1. Google Cloud 프로젝트 만들기

1. https://console.cloud.google.com 접속
2. 업로드에 사용할 YouTube 채널의 **Google 계정으로 로그인**
3. 화면 상단 왼쪽에 **"프로젝트 선택"** 드롭다운 클릭
4. 오른쪽 상단 **"새 프로젝트"** 클릭
5. 프로젝트 이름에 `maimai` 입력 → **만들기** 클릭
6. 잠시 후 상단에 "프로젝트가 생성되었습니다" 알림이 뜨면 **"프로젝트 선택"** 클릭

### 2. YouTube Data API 활성화

1. 왼쪽 상단 햄버거 메뉴(☰) → **"API 및 서비스"** → **"라이브러리"** 클릭
2. 검색창에 `YouTube Data API v3` 입력 후 엔터
3. **YouTube Data API v3** 클릭
4. 파란색 **"사용 설정"** 버튼 클릭

### 3. OAuth 동의 화면 설정

1. 왼쪽 메뉴 → **"API 및 서비스"** → **"OAuth 동의 화면"** 클릭
2. **"외부"** 선택 → **"만들기"** 클릭
3. 아래 항목만 입력하고 나머지는 빈칸으로 두기
   - **앱 이름**: `maimai`
   - **사용자 지원 이메일**: 본인 Gmail 선택
   - **개발자 연락처 이메일**: 본인 Gmail 입력
4. **"저장 후 계속"** 클릭
5. 다음 화면(범위)에서 아무것도 건드리지 않고 **"저장 후 계속"** 클릭
6. 다음 화면(테스트 사용자)에서 **"+ ADD USERS"** 클릭
7. 본인 Gmail 주소 입력 → **"추가"** 클릭 → **"저장 후 계속"** 클릭
8. 요약 화면에서 **"대시보드로 돌아가기"** 클릭

### 4. OAuth 클라이언트 ID 만들기

1. 왼쪽 메뉴 → **"API 및 서비스"** → **"사용자 인증 정보"** 클릭
2. 상단 **"+ 사용자 인증 정보 만들기"** 클릭 → **"OAuth 클라이언트 ID"** 클릭
3. **"애플리케이션 유형"** 드롭다운에서 **"데스크톱 앱"** 선택
4. 이름은 그대로 두고 **"만들기"** 클릭
5. 팝업창에서 **"JSON 다운로드"** 클릭
6. 다운로드된 파일 이름을 `client_secret.json` 으로 변경
7. `config/credentials/` 폴더 안에 복사

### 5. 처음 실행 시 Google 로그인

1. 프로그램을 처음 실행하면 브라우저가 자동으로 열림
2. Google 로그인 창에서 본인 계정 클릭
3. **"maimai이(가) Google 계정에 액세스하려고 합니다"** → **"계속"** 클릭
4. **"YouTube에서 동영상 관리"** 항목에 체크 → **"계속"** 클릭
5. 브라우저 닫으면 자동으로 진행됨

> 이후 실행부터는 로그인 창이 뜨지 않습니다.  
> 업로드가 갑자기 안 되면 `config/credentials/youtube_token.json` 파일을 삭제 후 재실행하세요.

---

## 매번 실행하는 법

1. `maimai_clipper.exe` 더블클릭
2. GUI 창이 열리면:
   - **YouTube URL** 입력 후 **상태 확인** 클릭
   - **시작 레이팅** 입력 (예: `14000`)
   - **시작/종료 시간** 입력 (생략하면 전체 구간 분석)
   - **자동 YouTube 업로드** 토글 설정
   - **시작** 버튼 클릭
3. 스캔이 완료되면 **스캔 결과** 화면으로 자동 이동됩니다.
   - 감지된 레이팅 상승 항목이 표시됩니다.
   - 오른쪽 상단의 **전체 선택 / 전체 해제** 버튼으로 일괄 선택하거나, 체크박스로 개별 선택하세요.
   - **클립 생성 시작 (N)** 버튼을 클릭하면 메인 화면으로 이동하며 다운로드 → 클립 커팅 → 업로드가 진행됩니다.
   - OCR 인식 결과가 틀렸다면 각 항목 오른쪽의 **✏️ 수정** 버튼을 눌러 곡명·난이도·달성률·내부 레벨을 직접 수정할 수 있습니다.
   - 클립을 생성하지 않으려면 전체 선택 해제 후 나타나는 **✕ 종료** 버튼으로 프로그램을 닫으세요.

---

## 곡명 인식 방식

결과 화면의 **자켓 이미지**를 곡 DB의 자켓과 대조해 곡을 식별합니다.
API 키나 별도 설정이 필요 없고, 첫 분석 시 자켓을 자동으로 내려받습니다.

1. 결과 화면에서 자켓 영역을 잘라 곡 DB의 자켓 1,600여 장과 대조
2. 일치도가 충분히 높고 2등과 차이가 크면 그대로 확정
3. 자켓이 비슷한 곡끼리 접전이면 **곡명 OCR**(PaddleOCR)로 후보를 가림
4. 일치하는 자켓이 없으면 **미등록 곡**으로 처리 (곡 DB에 아직 없는 신곡)

> 자켓 인덱스는 `cache/jacket_index.npz`에 저장됩니다(약 2.5MB).  
> 곡 DB에 신곡이 추가되면 환경 점검 패널의 **자켓 인덱스** 항목에 알림이 뜨고,  
> **⬇️ 업데이트** 버튼으로 새 곡의 자켓만 받을 수 있습니다.  
> 우타게(宴会場) 보면은 레이팅에 반영되지 않아 인식 대상에서 제외됩니다.

---

## config/credentials 폴더에 있어야 하는 파일

| 파일 이름 | 설명 | 갱신 |
|-----------|------|------|
| `client_secret.json` | Google API 인증 (YouTube 업로드용) | 한 번만 |
| `youtube_token.json` | 자동 생성됨 | 자동 갱신 |

> YouTube / maimai DX NET 쿠키는 파일 불필요 — Firefox 로그인 세션을 자동으로 읽습니다.

---

## 오류가 날 때

> 💡 프로그램 시작 시 **환경 점검**이 자동으로 실행됩니다.  
> ffmpeg · Tesseract OCR · YOLO 모델 · YouTube 로그인 · Google 인증 파일 · 자켓 인덱스 · maimai DB · GPU 8개 항목을 확인합니다.

항목은 중요도에 따라 분류됩니다.

| 항목 | 분류 | 시작 버튼 영향 |
|------|------|----------------|
| ffmpeg | 필수 | 오류 시 시작 불가 |
| Tesseract OCR | 필수 | 오류 시 시작 불가 |
| YOLO 모델 | 필수 | 오류 시 시작 불가 |
| YouTube 로그인 | 필수 | 오류 시 시작 불가 |
| Google 인증 파일 | 조건부 | **자동 업로드 ON** 일 때만 필수 |
| 자켓 인덱스 | 조건부 | **곡 정보 추출 ON** 일 때만 필수 (첫 실행 시 자켓 다운로드에 인터넷 필요) |
| maimai DB | 조건부 | **곡 정보 추출 ON** 일 때만 필수 (없으면 퍼지 매칭 불가) |
| GPU 가속 | 선택 | 없어도 시작 가능 (CPU로 동작, 속도 저하) |

시작 불가 시 버튼 아래에 원인 항목이 표시됩니다.  
대부분은 `setup/setup.bat`을 다시 실행하거나 아래 표를 참고하면 해결됩니다.

| 오류 메시지 / 상황 | 해결법 |
|-------------------|--------|
| 환경 점검 — ffmpeg 오류 | `setup/setup.bat` 다시 실행 |
| 환경 점검 — Tesseract OCR 오류 | `setup/setup.bat` 다시 실행 |
| 환경 점검 — YOLO 모델 오류 | `best_nano.onnx` 파일이 프로젝트 폴더 루트에 있는지 확인 |
| 환경 점검 — YouTube 로그인 경고 | Firefox에서 youtube.com에 로그인했는지 확인 |
| 환경 점검 — Google 인증 파일 경고 | `config/credentials/client_secret.json` 준비 여부 확인 (자동 업로드 ON인 경우) |
| 환경 점검 — 자켓 인덱스 오류 | 인터넷 연결 확인 (자켓 다운로드에 필요). **⬇️ 업데이트** 버튼으로 다시 시도할 수 있습니다 |
| Sign in to confirm you're not a bot | Firefox에서 YouTube에 로그인했는지 확인하세요. |
| 곡 정보가 표시되지 않음 | **곡 정보 추출** 토글이 ON인지 확인 |
| YouTube 업로드 실패 / 인증 오류 | 환경 점검 패널의 **🔑 재인증** 버튼 클릭, 또는 `config/credentials/youtube_token.json` 삭제 후 재실행 |

| Python을 찾을 수 없음 | Python 재설치 (PATH 체크 확인) |

---

## 라이선스

이 프로그램은 **GNU Affero General Public License v3.0** 으로 배포됩니다.
전문은 저장소 루트의 [LICENSE](LICENSE) 파일에 있습니다.

```
Copyright (C) 2026 ancho040220

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
```

AGPL을 택한 이유는 아래 두 의존성이 강한 카피레프트이기 때문입니다.
이 프로그램을 배포하거나 네트워크 서비스로 제공하려면 소스도 함께 공개해야 합니다.

| 구성 요소 | 용도 | 라이선스 |
|-----------|------|----------|
| [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) | 화면 감지 모델 학습 (배포물에는 미포함) | **AGPL-3.0** |
| [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) | 데스크톱 UI | **GPL-3.0** |
| [ONNX Runtime](https://onnxruntime.ai) | 화면 감지 모델 추론 | MIT |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 달성률·난이도·곡명 인식 | Apache-2.0 |
| [OpenCV](https://opencv.org) | 영상 처리 | Apache-2.0 |
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) | 레이팅 숫자 인식 | Apache-2.0 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 영상 다운로드 | Unlicense |
| [ffmpeg](https://ffmpeg.org) | 클립 커팅·인코딩 | LGPL/GPL |

곡 정보는 [gekichumai/dxrating](https://github.com/gekichumai/dxrating) 의 dxdata를,
자켓 이미지는 같은 프로젝트의 CDN을 사용합니다.

> 이 프로그램은 SEGA 및 maimai와 무관한 비공식 도구입니다.
> 게임 영상·음원·자켓의 권리는 각 권리자에게 있으며, 다운로드한 영상을
> 재업로드할 때는 원 저작자와 플랫폼 약관을 확인하세요.
