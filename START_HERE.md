# 크롬북에서 실제로 돌리기 (처음 한 번, 15분)

## 1. zip을 리눅스로 옮기기

1. 채팅에서 `shortforge_v2.0.0.zip` 다운로드 → ChromeOS **파일** 앱의 **다운로드**에 저장됨
2. 그 파일 **우클릭 → "Linux와 공유"** (이미 폴더가 공유돼 있으면 생략)
3. 터미널(Linux) 열고:

```bash
ls /mnt/chromeos/MyFiles/Downloads/ 2>/dev/null || ls /mnt/shared/MyFiles/Downloads/
```

둘 중 나오는 쪽이 네 공유 경로다. 아래에선 `$DL` 로 쓴다:

```bash
DL=/mnt/chromeos/MyFiles/Downloads   # 위에서 나온 경로로
cp "$DL/shortforge_v2.0.0.zip" ~/    # 9p는 느리니 홈으로 복사하고 작업
cd ~ && unzip -q shortforge_v2.0.0.zip && cd shortforge
```

## 2. 설치 (한 줄)

```bash
bash install.sh
```

- Arch(baguette)면 `pacman`, Debian이면 `apt`로 ffmpeg·chromium·python을 깔고
- `.venv` 만들어 의존성 설치, `~/.local/bin/shortforge` 런처 생성
- 백그라운드 서비스 등록(`systemctl --user enable --now shortforge-web`)
- TTS 모델 350MB 1회 다운로드
- 끝나면 브라우저에 **http://127.0.0.1:8787** 이 열린다

중간에 `sudo` 비밀번호 한 번 물어본다. 5~10분 걸린다(대부분 모델 다운로드).

## 3. 앱에서 (클릭만)

1. **[YouTube 로그인]** → 크로미움 창이 뜬다 → 평소처럼 구글 로그인 → 창은 그대로 두고 앱으로 돌아옴
2. **[확인]** → 점이 초록이면 성공
3. (선택) **[Instagram 로그인]** → 같은 방식
4. 설정에서 **공개 범위 = unlisted** 로 두고 **[지금 1편 만들어 올리기]** ← 첫 편은 눈으로 확인
5. 잘 올라갔으면 public 으로 바꾸고 **자동화 스위치 ON**

## 문제가 생기면

| 증상 | 할 일 |
|---|---|
| 크로미움 창이 안 뜸 | 서비스 대신 터미널에서: `systemctl --user stop shortforge-web && shortforge web` |
| 업로드가 중간에 멈춤 | 앱의 **게시 기록**에 실패 단계 + **스크린샷** 링크가 뜬다 → 그 이미지를 나한테 보내라 |
| 점이 빨감 | 로그인 세션 만료 → **[로그인]** 다시 |
| 다시 시작 | `systemctl --user restart shortforge-web` |
| 로그 보기 | 앱 맨 아래, 또는 `journalctl --user -u shortforge-web -f` |
| 완전 삭제 | `systemctl --user disable --now shortforge-web && rm -rf ~/shortforge` |

## 상태 파일 (백업할 것)

- `state/ledger.sqlite` — 뭘 썼고 뭘 올렸는지 (지우면 같은 소재 재사용)
- `state/chrome-profile/` — 로그인 세션 (지우면 재로그인)
- `out/` — 만든 mp4들
