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

---

## 대안 경로: Google Cloud 없이 (Upload-Post)

`SHORTFORGE_UPLOADER=uploadpost`로 바꾸면 2번(Google Cloud)·3번(auth) 단계가 통째로 사라진다.

1. https://upload-post.com 가입 → API 키 복사 (무료 10편/월, 유료 ~$16/월~)
2. 대시보드에서 **프로필**(예: `main`) 만들고 그 안에 YouTube 채널 연결 — "Google로 로그인 → 허용" 클릭이 전부. 같은 프로필에 TikTok·Instagram·Facebook도 붙일 수 있음. 채널 세트가 더 있으면 프로필을 더 만들면 됨(`second`, `third`…)
3. GitHub → Secrets: `UPLOAD_POST_API_KEY` / Variables: `SHORTFORGE_UPLOADER=uploadpost`, `UPLOAD_POST_USERS=main,second`, `UPLOAD_POST_PLATFORMS=youtube,tiktok,instagram`
4. 끝. 한 편 렌더 → 프로필마다 1회 POST → 각 프로필의 연결된 플랫폼 전부에 게시.

주의: `stats`(조회수·YPP 거리)는 YouTube Data API 전용이라 이 경로에선 안 채워짐 — 조회수는 Upload-Post 대시보드/YouTube Studio에서 본다. 이 어댑터는 그들의 OpenAPI 스펙으로 만들었고 이 컨테이너에선 라이브 호출 못 해봤다(egress 차단). 첫 실행 로그 던져주면 드리프트 잡는다.

---

## 경로 1 확정판: YouTube + Instagram 둘 다 공식 API로 (v1.2)

`SHORTFORGE_UPLOADER=youtube,instagram` — 한 편 렌더 → 두 플랫폼에 각각 게시, 한쪽이 실패해도 다른 쪽은 올라가고 실패분은 다음 실행의 `upload-pending`이 재시도한다.

### YouTube 쪽
위 1~3번 그대로 (Google Cloud → OAuth 데스크톱 클라이언트 → `shortforge auth`).

### Instagram 쪽 (Meta for Developers, 15분) — 공개 URL 호스팅 불필요
Instagram의 **resumable upload**를 쓰기 때문에 영상 바이트를 rupload.facebook.com에 직접 밀어넣는다. 어디에도 영상을 호스팅할 필요 없음.

1. **인스타 계정을 프로페셔널로 전환**: 인스타 앱 → 설정 → 계정 유형 및 도구 → 프로페셔널 계정으로 전환 → 크리에이터(무료). 페이스북 페이지 연결은 **필요 없음**(Instagram Login 방식).
2. https://developers.facebook.com/apps → **앱 만들기** → 사용 사례에서 **"Instagram"**(또는 "Other" → Business) 선택 → 앱 이름 아무거나 → 만들기.
3. 앱 대시보드 왼쪽 → **Instagram** → **"API setup with Instagram login"** → **1. Generate access tokens** → **Add account** → 네 인스타 계정 로그인·허용 → 그 계정 옆 **Generate token** → 팝업에서 권한 허용 → 토큰 복사. (이게 바로 **60일짜리 long-lived 토큰**이다. 앱은 개발 모드 그대로 두면 됨 — 본인 계정에 올리는 데 앱 심사 불필요.)
4. 로컬에서:
   ```bash
   IG_TOKEN_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")   # 아무 긴 문자열
   echo "IG_TOKEN_KEY=$IG_TOKEN_KEY" >> .env
   python -m shortforge ig-auth        # 토큰 붙여넣기 → @계정명·IG_USER_ID·쿼터 확인 → env 줄 출력
   ```
   출력된 `IG_USER_ID`, `IG_ACCESS_TOKEN`, `IG_TOKEN_KEY`를 `.env`와 GitHub에 넣는다.
5. GitHub → **Secrets**: `IG_ACCESS_TOKEN`, `IG_TOKEN_KEY` / **Variables**: `IG_USER_ID`, `SHORTFORGE_UPLOADER=youtube,instagram`.

토큰 수명: 60일. 워크플로가 매일 `ig-refresh`를 돌려 50일 미만 남으면 갱신하고, 갱신된 토큰은 `IG_TOKEN_KEY`로 암호화해서 `state/ig_token.enc`에 커밋한다(GitHub Actions는 자기 secret을 못 고치니까). 사람 손 안 탄다. 한도: 계정당 24시간 100편(API 게시 기준).

### 검증 상태
- YouTube 어댑터: resumable 프로토콜을 모킹 테스트로 검증, 라이브 미검증(여기 egress 차단).
- Instagram 어댑터: Meta 문서의 컨테이너→rupload→status→publish 4단계를 그대로 구현, 모킹 테스트 7개 통과, 라이브 미검증. 첫 `run --upload` 로그의 `[ig]` 줄을 던져주면 드리프트를 잡는다.
- 인코딩은 Reels 스펙에 맞춤: H.264 4:2:0, AAC 128k 48kHz, moov 앞(+faststart), edit list 없음(`-use_editlist 0`), 9:16 1080×1920 30fps.
