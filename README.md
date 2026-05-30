# maimai DX Rating Clipper 사용설명서

---

## 시스템 요구사항

| 항목 | 최소 | 권장 |
|------|------|------|
| OS | Windows 10 64-bit | Windows 11 64-bit |
| Python | 3.10 이상 | 3.11 이상 |
| RAM | 4GB | 8GB 이상 |
| GPU | — (CPU로도 동작) | NVIDIA CUDA 지원 GPU |
| 브라우저 | Firefox | Firefox |

> ⚠️ **현재 Windows 전용**입니다. macOS · Linux는 지원하지 않습니다.

> GPU(CUDA)가 없으면 CPU로 동작하지만 스캔 속도가 크게 느려집니다.  
> NVIDIA GPU가 있다면 `setup/setup.bat` 실행 시 CUDA 버전이 자동으로 설치됩니다.

---

## 최초 설치 (처음 한 번만)

### 1. Python 설치

1. https://www.python.org/downloads 접속
2. 노란색 **Download Python 3.x.x** 버튼 클릭
3. 다운로드된 파일 실행
4. ⚠️ **반드시** 설치 화면 맨 아래 **"Add python.exe to PATH"** 체크박스 체크
5. **Install Now** 클릭
6. 설치 완료 후 **Close**

### 2. 패키지 설치

1. `setup` 폴더 안의 `setup.bat` 파일 더블클릭
2. 검은 창이 열리면서 자동으로 설치됨
3. **"설치 완료!"** 메시지가 나오면 아무 키 눌러서 닫기

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

## CLOVA OCR 설정 (선택)

곡명 인식 정확도를 높이기 위해 **Naver Cloud Platform CLOVA OCR**을 사용합니다.  
없어도 레이팅 변동 감지 및 클립 생성은 정상 동작합니다.

1. [Naver Cloud Platform](https://www.ncloud.com) 가입 후 로그인
2. **CLOVA OCR** 서비스에서 커스텀 도메인 생성
3. **API Gateway** 탭에서 **Invoke URL** 복사
4. **Secret Key** 복사
5. `config/credentials/clova_ocr.txt` 파일을 다음 형식으로 작성:

```
CLOVA_OCR_URL=여기에_Invoke_URL_붙여넣기
CLOVA_OCR_SECRET=여기에_Secret_Key_붙여넣기
```

> CLOVA OCR이 없으면 곡명·달성률·난이도 추출이 동작하지 않습니다.  
> 레이팅 변동 감지와 클립 생성은 정상 동작하지만, 스캔 결과에 곡 정보가 표시되지 않습니다.

---

## config/credentials 폴더에 있어야 하는 파일

| 파일 이름 | 설명 | 갱신 |
|-----------|------|------|
| `client_secret.json` | Google API 인증 (YouTube 업로드용) | 한 번만 |
| `youtube_token.json` | 자동 생성됨 | 자동 갱신 |
| `clova_ocr.txt` | CLOVA OCR Invoke URL + Secret Key | 한 번만 |

> YouTube / maimai DX NET 쿠키는 파일 불필요 — Firefox 로그인 세션을 자동으로 읽습니다.

---

## 오류가 날 때

> 💡 프로그램 시작 시 **환경 점검**이 자동으로 실행됩니다.  
> ffmpeg · Tesseract OCR · YOLO 모델 · YouTube 로그인 · Google 인증 파일 · CLOVA OCR · maimai DB · GPU 8개 항목을 확인합니다.

항목은 중요도에 따라 분류됩니다.

| 항목 | 분류 | 시작 버튼 영향 |
|------|------|----------------|
| ffmpeg | 필수 | 오류 시 시작 불가 |
| Tesseract OCR | 필수 | 오류 시 시작 불가 |
| YOLO 모델 | 필수 | 오류 시 시작 불가 |
| YouTube 로그인 | 필수 | 오류 시 시작 불가 |
| Google 인증 파일 | 조건부 | **자동 업로드 ON** 일 때만 필수 |
| CLOVA OCR | 조건부 | **곡 정보 추출 ON** 일 때만 필수 (없으면 곡명·달성률·난이도 추출 불가) |
| maimai DB | 조건부 | **곡 정보 추출 ON** 일 때만 필수 (없으면 퍼지 매칭 불가) |
| GPU(CUDA) | 선택 | 없어도 시작 가능 (CPU로 동작, 속도 저하) |

시작 불가 시 버튼 아래에 원인 항목이 표시됩니다.  
대부분은 `setup/setup.bat`을 다시 실행하거나 아래 표를 참고하면 해결됩니다.

| 오류 메시지 / 상황 | 해결법 |
|-------------------|--------|
| 환경 점검 — ffmpeg 오류 | `setup/setup.bat` 다시 실행 |
| 환경 점검 — Tesseract OCR 오류 | `setup/setup.bat` 다시 실행 |
| 환경 점검 — YOLO 모델 오류 | `best_nano.pt` 파일이 프로젝트 폴더 루트에 있는지 확인 |
| 환경 점검 — YouTube 로그인 경고 | Firefox에서 youtube.com에 로그인했는지 확인 |
| 환경 점검 — Google 인증 파일 경고 | `config/credentials/client_secret.json` 준비 여부 확인 (자동 업로드 ON인 경우) |
| 환경 점검 — CLOVA OCR 오류 | `config/credentials/clova_ocr.txt` 파일과 URL·Secret Key가 올바른지 확인 |
| Sign in to confirm you're not a bot | Firefox에서 YouTube에 로그인했는지 확인하세요. |
| 곡 정보가 표시되지 않음 | CLOVA OCR 설정 및 **곡 정보 추출** 토글이 ON인지 확인 |
| YouTube 업로드 실패 / 인증 오류 | 환경 점검 패널의 **🔑 재인증** 버튼 클릭, 또는 `config/credentials/youtube_token.json` 삭제 후 재실행 |
| CUDA / torch 오류 | `setup/setup.bat` 다시 실행 |
| Python을 찾을 수 없음 | Python 재설치 (PATH 체크 확인) |
