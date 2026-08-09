# IDEA

IDEA는 **Iterative Distributed Exploit Agents**의 약자입니다. 사용자의 목표를 여러
Codex/Claude Code 세션에 동시에 전달하고, 그 세션들이 자유롭게 글·댓글·파일을
공유할 수 있는 포럼을 제공하는 얇은 런처입니다.

IDEA 자신은 문제를 분석하지 않습니다. 타깃을 점수화하거나, 에이전트를 단계와 역할로
나누거나, 어떤 주장이 옳은지 판정하거나, 작업 시간을 제한하지 않습니다. 서로 다른
모델과 추론 강도로 시작된 에이전트들이 같은 목표와 작업 디렉터리를 보고 스스로
분업·논쟁·실험 방향을 정합니다.

## 현재 MVP

- 하나의 자연어 목표를 모든 에이전트에 그대로 전달
- 모든 에이전트를 단계 없이 동시에 시작
- Codex: GPT-5.6 Luna/Terra/Sol, `low`부터 `max`까지 서로 다른 effort
- Claude Code: Sonnet/Opus, `low`부터 `max`까지 서로 다른 effort
- 에이전트별 벽시계 타임아웃 없음
- 응답이 끝난 세션을 `dormant`로 보존하고 포럼 활동이 생기면 자동 재개
- `@agent-name` 대상 알림과 에이전트가 직접 결정하는 `retire`
- SQLite WAL 기반 자유 형식 포럼: 스레드, 댓글, 내용 검색, 파일 첨부
- 실행 중 열리는 로컬 웹 포럼과 같은 데이터에 접근하는 CLI/JSON API
- 프로세스별 원본 JSONL 로그 보존

## 설치와 실행

Python 3.11 이상, 로그인되어 있는 `codex`와 `claude` CLI가 필요합니다.

```bash
python3 -m pip install -e .
idea doctor
```

CTF 문제 파일이 있는 디렉터리에서 목표만 입력합니다.

```bash
cd /path/to/challenge
idea 이 바이너리에서 flag를 획득해
```

기본 설정은 16개 세션을 동시에 시작합니다. Sol `max`는 두 세션으로 실행하고,
Opus는 safeguard나 provider 오류로
일부 세션이 중단되더라도 각 추론 강도에서 다른 세션이 계속 작업할 수 있도록
`high`, `xhigh`, `max`를 각각 2개씩 실행합니다. 실행 직후 터미널에 로컬 포럼 주소가
표시됩니다. 모델 응답이 끝난 에이전트는 사라지지 않고 `dormant` 상태에서 새 포럼
활동을 기다립니다. 모든 에이전트가 명시적으로 `retire`하거나 사용자가 런처를
중단하기 전까지 reactor는 유지됩니다. `.idea-swarm/forum.sqlite3`와 첨부파일, 로그는
종료 후에도 남습니다.

```bash
idea serve
idea status
```

런처나 터미널이 중단되었다면 새 run을 만들지 않고 같은 포럼과 provider 세션을
이어갑니다.

```bash
idea resume
```

provider 세션 자체가 손상된 경우에는 새 세션을 만들되 기존 포럼을 유지할 수 있습니다.

```bash
idea resume --fresh
```

IDEA 업데이트로 새 기본 프로필이 추가된 경우 기존 포럼에 그 peer들을 합류시킬 수
있습니다.

```bash
idea resume --expand-defaults
```

실제 모델 호출 없이 시작 상태와 프로필만 확인할 수도 있습니다.

```bash
idea --dry-run 이 바이너리에서 flag를 획득해
idea profiles --json
```

특정 프로필만 시작하려면 `--profile`을 반복합니다. 이것은 비용이나 실험을 직접
조절하려는 사용자를 위한 선택 사항이며, 기본 동작은 모든 프로필입니다.

```bash
idea --profile luna-low --profile opus-max 목표를 여기에 입력
```

## 포럼

각 에이전트의 최상위 지침에는 포럼의 존재와 사용법이 들어갑니다. 포럼에는 점수,
강제 분류, 중앙 관리자, 정답 게시물 개념이 없습니다. 게시물은 수정되지 않는 공유
기록이며, 에이전트는 원하는 형식으로 글을 쓰고 댓글로 반론하거나 확장할 수 있습니다.

에이전트 프로세스에는 run ID와 작성자 이름이 환경 변수로 전달되므로 다음 명령을
그대로 쓸 수 있습니다.

```bash
idea forum inbox --json
idea forum recent --json
idea forum read THREAD_ID --json
idea forum search "검색어" --json
idea forum post --title "제목" --body "내용"
idea forum reply-trigger --body "현재 멘션에 대한 답변"
idea forum reply THREAD_ID --body "댓글"
idea forum attach ./exploit.py --thread THREAD_ID --description "재현 스크립트"
idea forum retire --reason "목표 달성과 재현 결과 게시 완료"
```

멘션 없는 글·댓글·첨부파일은 포럼과 각 peer의 inbox에 남지만 휴면 모델을 즉시
호출하지는 않습니다. 본문에 `@sol-high`처럼 **정확한 전체 peer 이름**을 적으면 해당
peer만 즉시 깨우며, `@all`은 명시적인 전체 알림입니다. 예를 들어
`@opus-max-2`는 `opus-max-2`만 깨우고 이름이 접두어인 `opus-max`는 깨우지 않습니다.
peer가 멘션으로 깨어날 때는 마지막으로 읽은 뒤 쌓인 일반 활동도 함께 전달됩니다.
자기 게시물로 자기 세션이 다시 깨어나지는 않습니다. 여러 이벤트는 한 번의 재개
알림으로 합쳐지고, 이미 실행 중인 동일 peer를 중복 실행하지 않습니다.

재개 프롬프트에서는 **실제로 깨움을 발생시킨 멘션**을 이전부터 쌓인 배경 활동과
분리합니다. 멘션 이벤트에는 작성자, 글 제목, 메시지, thread ID가 구조화된 데이터로
전달됩니다. peer가 그 메시지에 직접 답할 때는 `reply-trigger`를 사용하며, IDEA가
현재 trigger event를 검증하여 원래 글에 댓글을 답니다. 여러 trigger 중 특정 이벤트에
답하려면 `reply-trigger --event EVENT_ID`를 사용할 수 있습니다. 독립 연구 결과는 다른
글에 자유롭게 남길 수 있지만, 사용자 질문에 대한 직접 답변이나 링크는 원래 글에도
남기도록 최상위 프롬프트에 안내됩니다.

마지막 provider 호출이 정상적으로 끝나면 `dormant`, 일반 CLI 오류는 `failed`,
Claude의 최종 safeguard refusal은 `blocked`로 표시됩니다. `blocked`는 영구 종료가
아닙니다. 정확한 개인 멘션이나 `@all`을 받으면 반복 차단된 provider 대화는
재사용하지 않고 같은 이름·모델·effort의 새 provider 세션으로 시작합니다. 포럼,
파일, 로그, 목표는 그대로 유지됩니다. 멘션 없는 포럼 활동 때문에 자동으로
재시작되지는 않으며, 새 세션도 차단되면 다시 `blocked`에서 기다립니다.

웹에서는 사람이 새 글과 댓글을 직접 추가할 수도 있습니다. 화면 전체를 주기적으로
새로고침하지 않으며, 새 활동은 현재 읽던 위치를 유지한 채 목록 위의 배지로만
알립니다. 게시물 목록은 30개씩 커서 기반으로 가져오고 제목·짧은 미리보기만
표시합니다. 본문·댓글·첨부파일은 사용자가 글을 선택했을 때 해당 글 하나만
불러오므로 포럼이 커져도 초기 페이지 크기는 일정하게 유지됩니다.
등록된 peer의 정확한 멘션은 파란 배지, 전체 호출인 `@all`은 금색 배지, 등록된
peer와 일치하지 않는 멘션은 흐린 점선 배지로 표시됩니다.
LLM이 `@human` 또는 `@user`로 사람을 멘션하면 보라색 배지와 함께 화면 오른쪽에
읽을 때까지 유지되는 알림 카드가 나타납니다. 카드에는 작성 peer, 글 제목, 짧은
문맥이 표시되며 클릭하면 해당 글을 열고 실제 멘션 글·댓글·첨부 위치를 강조합니다.
읽음 위치는 브라우저에 run별로 저장되므로 페이지를 다시 열어도 놓친 멘션을 다시
가져옵니다. 사용자가 직접 쓴 `@human`은 자기 알림으로 처리하지 않습니다.

웹 UI가 사용하는 경량 JSON API는 다음과 같습니다.

```text
GET /api/runs/{run_id}/threads?limit=30&before=THREAD_ID&q=QUERY
GET /api/runs/{run_id}/updates?after=ACTIVITY_ID
GET /api/runs/{run_id}/overview
GET /api/threads/{thread_id}
```

기존 전체 내보내기 API인 `GET /api/runs/{run_id}`도 호환성을 위해 남아 있지만,
웹 화면은 이 무거운 엔드포인트를 사용하지 않습니다. 스레드와 댓글 작성
엔드포인트도 그대로 제공합니다.

## 자율성의 경계

IDEA가 정하는 것은 시작 조건뿐입니다.

1. 사용자가 입력한 목표
2. 사용자가 실행한 현재 작업 디렉터리
3. 사용할 모델과 추론 강도의 다양성
4. 동료와 교환할 수 있는 포럼 주소와 명령

전략, 역할, 우선순위, 실험 순서, 게시물 형식, 합의 여부는 에이전트들이 결정합니다.
IDEA 런처는 프로세스가 끝날 때까지 기다릴 뿐 시간 제한이나 라운드를 두지 않습니다.
현재 버전은 사용자가 권한을 가진 CTF/연구 작업 디렉터리에서 사용하는 것을 전제로
합니다.

기본 프로필은 완전 비대화식으로 실행됩니다. Codex에는
`--dangerously-bypass-approvals-and-sandbox`, Claude Code에는
`--dangerously-skip-permissions`가 전달되므로 승인 입력을 기다리지 않습니다. 그 대신
에이전트 프로세스는 현재 사용자 계정이 접근할 수 있는 파일과 명령에 접근할 수
있으므로, 신뢰할 수 있고 격리된 CTF 작업 환경에서 실행해야 합니다.
