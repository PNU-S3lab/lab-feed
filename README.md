# lab-피드

S3Lab Slack `#lab-피드`에 우수 학회 CFP와 정보컴퓨터공학부 대학원 공지를 올립니다.

## 무엇을 올리나

| 출처 | 내용 |
| --- | --- |
| [sec-deadlines](https://sec-deadlines.github.io/) | 보안·프라이버시·암호 학회 마감일 (정식 학회만, 워크숍 제외) |
| [huggingface/ai-deadlines](https://github.com/huggingface/ai-deadlines) | AI·ML 학회 마감일 |
| [Pusnow gist](https://gist.github.com/Pusnow/6eb933355b5cb8d31ef1abcb3c3e1206) | 정보과학회 2024 등급, BK21플러스 IF (2018) |
| [대학원 공지사항 RSS](https://cse.pusan.ac.kr/cse/14227/subview.do) | 학과 공지 |

학회는 CORE A* 또는 A이거나, 정보과학회 최우수·우수인 것만 남깁니다.

- 새 CFP가 데이터에 올라오면 한 번
- 마감 14일 전에 한 번 (마감일마다 한 번만, 매일 반복하지 않음)
- 학과 공지는 새 글마다 한 번

매일 `#lab-피드` 채널 캔버스(마감 일정표)와 구독용 캘린더 [`cfp.ics`](https://raw.githubusercontent.com/PNU-S3lab/lab-feed/main/cfp.ics)도 갱신합니다.
캘린더에는 학회마다 CFP가 올라온 날부터 마지막 마감일까지 이어지는 종일 일정과, 초록·논문 마감 시각 일정이 들어갑니다. CFP가 올라온 날은 출처 저장소에 그 연도 항목이 처음 등록된 날로 계산하므로 실제 공개일과 며칠 차이가 날 수 있습니다. 보안 학회의 초록 마감은 sec-deadlines의 comment 문구(예: 1 week before deadline)에서 계산합니다.

첫 실행에서는 하나씩 알리지 않고 현재 접수 중인 목록만 한 번 올립니다. 이미 알린 항목은 `state.json`에 기록됩니다.

## 설치

1. https://api.slack.com/apps → Create New App → From a manifest → S3Lab → `manifest.yml` 붙여넣기
2. Incoming Webhooks → Add New Webhook → `#lab-피드` 선택 → 웹훅 URL 복사
3. 이 저장소를 GitHub에 올리고 Settings → Secrets and variables → Actions → `SLACK_WEBHOOK_URL`에 웹훅 URL 저장
4. OAuth & Permissions에서 Bot User OAuth Token을 복사해 Secret `SLACK_BOT_TOKEN`에 저장 (캔버스 갱신용)
5. Actions 탭 → lab-feed → Run workflow로 첫 실행. 이후 매일 09:00 KST에 자동 실행

로컬 확인: `pip install -r requirements.txt && python3 feed.py --dry-run`

## 한계

- 마감일 데이터는 커뮤니티가 관리합니다. AI 학회는 갱신이 느린 편이라 마감일이 늦게 입력될 수 있습니다.
- BK 점수는 BK21플러스 2018 목록 기준입니다. 4단계용 새 목록이 나오면 gist가 갱신되는지 확인하세요.
