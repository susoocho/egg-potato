# 쇼츠 업로드 로봇

구글 드라이브 폴더에 영상을 넣어두면, 매일 새벽 5시(한국 시간)에 그날 올릴 영상을 골라
유튜브에 **예약 업로드**해요. 영상은 아침 7시부터 밤 11시 30분까지 고르게 나눠서 공개돼요.
올린 영상은 드라이브의 `올린 영상` 폴더로 옮겨져서 두 번 올라가지 않아요.

## 평소에 하실 일

1. 주말에 편집한 영상을 드라이브 업로드 폴더에 넣어요.
2. 파일 이름은 `원본이름_번호.mp4` 로 해주세요. 예: `아카시로_1.mp4`, `아카시로_2.mp4`
   - 같은 원본에서 나온 변형은 **한 번 올라가면 최소 6일 쉬었다가** 다음 변형이 올라가요.
     하루 27개 기준으로 약 150개 간격이에요. 자막이나 노래를 바꾼 변형을 넣어두면 알아서 간격을 두고 재활용돼요.
   - 제목은 파일 이름으로 자동으로 만들어요. `아카시로_2.mp4` → `아카시로 #couple #shorts`
3. 끝이에요. 영상이 하루 목표(27개)보다 적으면 실행 기록에 "부족해요"라고 남아요.

## 음원 영상: 폰에서 비공개로 올리면 자동 예약

유행 음원은 유튜브 앱에서만 붙일 수 있어서, 음원 영상은 이렇게 해요.

1. 유튜브 앱에서 영상 선택 → **사운드 추가** → 제목은 `그네`처럼 짧게
   (같은 원본의 다른 버전이면 `그네 2`, `그네 3`)
2. 공개 설정을 **비공개**로 하고 업로드. 임시보관함은 안 돼요.
3. 끝이에요. 매시간 `비공개 쇼츠 자동 예약`이 돌면서
   - 제목을 틀에 맞게 바꾸고 설명, 태그를 붙이고
   - 하루 `per_day`개씩 빈 시간에 예약을 걸어요.
   - 같은 원본의 다음 버전은 `min_days_between_variants`일 뒤로 잡아요.

계속 비공개로 두고 싶은 영상은 제목에 `#보관`을 넣으면 건드리지 않아요.
`scheduler.since` 이전에 올린 비공개 영상도 건드리지 않아요.
`scheduler.first_publish_date`에 날짜(예: `2026-12-26`)를 넣으면 그날부터 공개되도록 잡아요.
잠시 멈추려면 저장소 변수 `SCHEDULER_ENABLED`를 `false`로 바꾸면 돼요.

## 설정 바꾸기 (`config.json`)

| 항목 | 뜻 |
|---|---|
| `per_day` | 하루에 올릴 개수 (지금 27) |
| `max_per_original_per_day` | 같은 원본의 변형을 하루에 몇 개까지 올릴지 (지금 1) |
| `min_days_between_variants` | 같은 원본의 다음 변형까지 쉬는 날 수 (지금 6) |
| `publish_start`, `publish_end` | 공개 시간대 |
| `title_templates` | 제목 틀. `{name}` 자리에 원본 이름이 들어가요. 변형 번호마다 돌아가며 써요 |
| `description`, `tags` | 설명과 태그 |

바꾸고 싶으면 Claude에게 말해주세요.

## 처음 연결 (한 번만)

모두 **커플 채널이 있는 구글 계정**으로 해야 해요. Claude가 내장 브라우저로 같이 해드릴 수 있어요.

1. **드라이브 폴더 만들기**: 드라이브에 `egg-potato 업로드` 폴더를 만들고, 주소창의
   `folders/` 뒤에 있는 글자(폴더 ID)를 복사해요.
2. **구글 클라우드 설정** (console.cloud.google.com)
   - 새 프로젝트 만들기
   - API 라이브러리에서 `YouTube Data API v3` 와 `Google Drive API` 사용 설정
   - OAuth 동의 화면: 외부(External), 앱 이름 아무거나, 본인 이메일 입력 후
     **게시(Publish app, 프로덕션)** 로 바꾸기. 테스트 상태로 두면 7일마다 연결이 끊겨요.
   - 사용자 인증 정보 → OAuth 클라이언트 ID → 웹 애플리케이션,
     승인된 리디렉션 URI에 `https://developers.google.com/oauthplayground` 추가.
     만들어진 **클라이언트 ID**와 **클라이언트 보안 비밀번호**를 복사해요.
3. **연결 토큰 받기** (developers.google.com/oauthplayground)
   - 오른쪽 위 톱니바퀴 → `Use your own OAuth credentials` 체크 → 2번의 ID와 비밀번호 입력
   - 왼쪽 입력칸에 아래 두 줄을 넣고 `Authorize APIs`
     `https://www.googleapis.com/auth/youtube.upload`
     `https://www.googleapis.com/auth/drive`
   - 로그인할 때 **Egg and Potato Couple 채널**을 골라요. "확인되지 않은 앱" 경고가 나오면
     고급 → 이동 을 누르면 돼요 (내가 만든 앱이라 괜찮아요).
   - `Exchange authorization code for tokens` → **Refresh token** 복사
   - 자동 예약까지 쓰려면 권한에 `https://www.googleapis.com/auth/youtube` 도 같이 넣어야 해요.
4. **GitHub에 저장** (저장소 Settings → Secrets and variables → Actions)
   - Secrets 에 4개 추가: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `DRIVE_FOLDER_ID`
5. **연습 실행**: Actions 탭 → `매일 쇼츠 업로드` → Run workflow (연습 모드 체크된 채로).
   오늘 올릴 목록이 제대로 나오는지 확인해요.
6. **켜기**: 같은 화면의 Variables 에 `UPLOAD_ENABLED` = `true` 추가.
   이때부터 매일 새벽 5시에 실제로 올라가요. 멈추고 싶으면 `false` 로 바꾸면 돼요.

## 알아둘 것

- 유튜브 API 기본 한도는 하루 100개 업로드라서 27개는 여유 있어요.
- 유행 음원은 이 로봇이 넣을 수 없어요. 음원을 파일에 직접 넣으면 저작권 신고가 걸릴 수 있으니,
  음원이 중요한 영상은 유튜브 앱에서 직접 올리는 게 안전해요.
- 같은 원본의 거의 똑같은 변형을 많이 올리면 유튜브가 반복 콘텐츠로 보고 노출을 줄일 수 있어요.
  유튜브에는 원본당 1~2개만 넣고, 나머지는 틱톡과 릴스에 쓰는 걸 추천해요.
- 실행이 실패하면 GitHub가 이메일로 알려줘요.
