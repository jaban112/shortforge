# SETUP — 30분 안에 무인 가동까지

순서대로. 전부 네 손으로만 가능한 것들이고(계정·키), 그 뒤로는 GitHub Actions가 알아서 돈다.

## 0. 이게 뭘 하는지 (30초)

매일 2번(13:00 / 22:00 UTC) GitHub Actions 러너가:
1. Wikipedia "On This Day" API에서 오늘 날짜의 검증된 사건들을 가져오고 (또는 NASA APOD)
2. 아직 안 쓴 사건 하나를 골라 스크립트를 쓰고 — **출처 텍스트에 없는 숫자·고유명사가 하나라도 있으면 스크립트 폐기** (grounding gate)
3. 로컬 TTS(Kokoro)로 문장 단위 합성 → 자막 타이밍이 추정이 아니라 정확
4. Commons 라이선스 확인된 사진(CC0/CC-BY/CC-BY-SA/PD만) 또는 절차적 배경 위에 ffmpeg로 1080×1920 렌더
5. YouTube Data API로 업로드(`containsSyntheticMedia=true` 자동 표기), 원장(sqlite)에 기록, 상태 페이지 갱신 → repo에 커밋

너는 `status/README.md`만 가끔 열어보면 된다.

## 1. YouTube 채널

1. youtube.com → 프로필 → "채널 만들기" (브랜드 계정으로 새로). 이름은 `SHORTFORGE_CHANNEL_NAME`과 맞춰라 (기본 "Today in History").
2. YouTube Studio → 설정 → 채널 → 기능 사용 자격 → **전화번호 인증** (중급 기능). Shorts만 올릴 거면 필수는 아니지만, 미인증 채널은 하루 업로드 수 제한이 더 빡빡하다.

## 2. Google Cloud OAuth 클라이언트 (업로드 권한)

1. https://console.cloud.google.com → 새 프로젝트 (이름 아무거나, 예: shortforge)
2. "API 및 서비스" → "라이브러리" → **YouTube Data API v3** → 사용 설정
3. "OAuth 동의 화면" → User Type **외부** → 앱 이름/이메일 입력 → 범위는 건너뛰어도 됨 → 테스트 사용자에 **네 구글 계정 추가**
4. 동의 화면 페이지 상단 **"앱 게시" → 프로덕션으로 전환**. ← 이거 안 하면 refresh token이 **7일마다 만료**돼서 무인 업로드가 조용히 죽는다. "확인 필요" 경고는 무시해도 됨(본인 계정만 쓰니까; 로그인 때 "확인되지 않은 앱" 화면에서 '고급 → 이동'으로 통과).
5. "사용자 인증 정보" → "+ 사용자 인증 정보 만들기" → **OAuth 클라이언트 ID** → 애플리케이션 유형 **데스크톱 앱** → 만들기 → **클라이언트 ID / 클라이언트 보안 비밀** 복사

## 3. refresh token 발급 (1회, 로컬에서)

```bash
git clone <your-repo> shortforge && cd shortforge
pip install -e ".[dev]"          # ffmpeg는 따로: sudo pacman -S ffmpeg  (Arch) / sudo apt install ffmpeg
cp .env.example .env             # YT_CLIENT_ID, YT_CLIENT_SECRET 채우기
python -m shortforge auth
```

URL이 뜬다 → 아무 브라우저(크롬OS 쪽 브라우저도 됨)에서 열고 승인 → 마지막에 `http://127.0.0.1:8765/?code=...` 로 리다이렉트되는데 그 페이지가 안 열려도 상관없다. **주소창 전체를 복사해서 터미널에 붙여넣기** → `YT_REFRESH_TOKEN=...` 출력. `.env`에 넣어라.

## 4. 로컬에서 한 번 돌려보기 (선택이지만 권장)

```bash
python -m shortforge doctor                          # ffmpeg·모델·키·egress 점검
python -m shortforge render-fixture fixtures/otd_1620.json   # 네트워크 없이 샘플 렌더 (첫 실행 때 모델 350MB 받음)
python -m shortforge run                             # 오늘 날짜로 진짜 1편 렌더 (업로드 X)
python -m shortforge run --upload                    # 업로드까지
```

첫 몇 편은 `SHORTFORGE_PRIVACY=unlisted`로 올려서 눈으로 확인하고 public으로 바꿔도 된다.

## 5. GitHub 무인 가동

1. private repo 만들고 push (`.env`는 .gitignore에 있음 — 절대 커밋 금지)
2. repo → Settings → Secrets and variables → Actions → **Secrets**:
   - `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` (필수)
   - `ANTHROPIC_API_KEY` (선택 — 있으면 LLM 작가, 없으면 템플릿 작가)
   - `NASA_API_KEY` (선택 — apod 팩 쓸 때)
3. 같은 화면 **Variables** (선택): `SHORTFORGE_CHANNEL_NAME`, `SHORTFORGE_PACK`, `SHORTFORGE_VOICE`, `SHORTFORGE_PRIVACY`, `SHORTFORGE_VIDEOS_PER_RUN`
4. Settings → Actions → General → Workflow permissions → **Read and write** (원장 커밋용)
5. Actions 탭 → "shortforge daily" → **Run workflow** 한 번 수동 실행 → 초록불 확인 → 끝. 이후 매일 13:00/22:00 UTC 자동.

실패하면 GitHub가 이메일로 알려준다(빨간불). 성공은 조용하다. `status/README.md`에 업로드 목록·조회수·YPP 거리(구독 1,000 / 90일 Shorts 조회 10,000,000)가 매일 갱신된다.

## 6. 하루 몇 편?

YouTube API 할당량 기본 10,000 units/일, 업로드 1편 = 1,600 → **최대 6편/일**. 워크플로는 2편/일로 시작. 늘리려면 `daily.yml`의 cron 줄을 추가하거나 `SHORTFORGE_VIDEOS_PER_RUN` 변수를 올려라. 팩을 늘리려면(`apod`) 두 번째 워크플로를 복사해서 `pack: apod`로.

## 7. 크롬북에서 직접 돌리고 싶으면

`ops/shortforge.service`, `ops/shortforge.timer` 주석대로. Arch VM에서 `pacman -S ffmpeg ttf-dejavu` 필요. 12스레드면 1편 렌더 ~1분.

## 알아둘 것 (정직하게)

- **돈은 YPP 승인 뒤에만 들어온다.** 광고 수익 티어 = 구독 1,000 + 90일 Shorts 조회 1,000만(2027-02-01부터 2,000만). 팬펀딩 티어 = 구독 500 + 300만. 승인은 사람 심사고, 2025-07부터 "템플릿 양산·합성음성+스톡영상 고정공식" 채널은 **inauthentic content**로 거절된다. 이 설계가 출처 표기·매일 다른 소재·검증된 사실로 그 판정을 피하려는 것이지, 피한다는 보장은 없다.
- Shorts RPM은 낮다(광고 수익 분배 45%). 조회 1,000만이 채워질 정도 채널이면 월 수십만 원대가 현실적 출발점이고, 그 이상은 롱폼·제휴·스폰서로 넘어가야 한다. 이 코드는 그 다음 단계의 토대(원장·업로더·검증 파이프라인)까지 포함한다.
- 이 컨테이너에서는 Wikimedia/Google 네트워크가 막혀 있어 **API 라이브 호출은 미검증**이다. 오프라인 픽스처로 렌더 전 과정은 검증했고(실제 mp4 생성·프레임 확인), API 응답 파서는 문서화된 응답 형태로 테스트했다. `python -m shortforge doctor`가 네 환경에서 진짜 egress를 확인해준다.
