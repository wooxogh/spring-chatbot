# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 정체

서비스명 **'스프링(Spring)'** — 가족돌봄청소년·청년 원스톱 공공복지 AI 안내 서비스의 **챗봇 프로토타입 (Python/Streamlit 단순 데모)**.

폴더명에 'spring'이 들어가지만 Spring Boot/Java가 아니다. Spring은 서비스명이고 스택은 Python이다.

공식 서비스 기획안: `docs/spring-service-spec.md`. 기능 추가·수정 시 항상 이 기획안의 4단계 플로우(선제 발굴 → 온보딩/스크리닝 → Route A/B 라우팅 → 사후관리)에 비추어 어디에 해당하는지 먼저 확인할 것. 기획안과 어긋나는 변경은 사용자에게 확인을 받는다.

## 실행 / 셋업

```bash
# 처음 한 번
cp .env.example .env   # OPENAI_API_KEY 채우기 — https://platform.openai.com/api-keys
.venv/bin/pip install -r requirements.txt

# 매번 실행
.venv/bin/streamlit run app.py
```

테스트·린트·빌드 스크립트는 없다 (단순 데모).

## 아키텍처

5개 모듈 구조. Streamlit 세션 상태로 4단계 phase 머신을 돌린다:

```
consent  →  signup  →  mode  →  chat
(동의)    (이름/생년월일/거주지)  (사전상담|사후관리)  (OpenAI 챗)
```

| 파일 | 책임 |
|---|---|
| `app.py` | 페이지 setup, CSS, 세션 상태 초기화, OpenAI 클라이언트, 각 phase의 render_* 함수, 파일 끝 라우터 |
| `routing.py` | `determine_route` 라우트 판별, `routing_guidance_for` 라우트별 시스템 프롬프트 가이던스, `calc_age` |
| `tools.py` | 4개 도구 함수(`update_report` 등), JSON 스키마, `TOOL_DISPATCH`, `build_pre_tools/build_post_tools`, `PRE_STAGES/POST_STAGES` |
| `prompts.py` | `PRE_SYSTEM_TEMPLATE` / `POST_SYSTEM_TEMPLATE` 문자열 |
| `chat.py` | `start_chat_session`, `run_chat_turn` (tool-call 루프), `format_chat_error`, `_strip_images_in_place`, `OPENAI_MODEL`, `MAX_TOOL_LOOPS` |

각 phase는 `render_consent / render_signup / render_mode_select / render_chat` 한 함수가 그린다. 파일 끝의 라우터(`phase = st.session_state.phase`)가 분기한다. 임포트 그래프는 단방향 — `app → chat → {tools, prompts, routing}`, `tools/prompts/routing`은 서로 의존하지 않는다.

### 라우팅 판별 (`routing.determine_route`)

이미지 스크립트(`image.png`) 기준으로 가입 정보에서 즉시 결정된다. 시스템 프롬프트와 첫 인사 문장이 라우트별로 다르므로 라우팅 로직을 바꿀 때는 인사 문구(`branch_message`)도 함께 본다.

시그니처: `determine_route(age, region, is_veteran=False, force_route_a=False)`. 간편인증 불가는 `force_route_a=True`로 전달해 Route A로 강제한다 (의미가 다른 사유를 `is_veteran` 하나에 OR로 묶지 않는다).

- **Route A (포털 불가, 오프라인)**: 9~13세, 또는 39세 초과/제대군인, 또는 `force_route_a=True`
- **Route B (포털 가능)**: 14~39세 + 서울(→서울복지포털) 또는 청년ON 관할 지역(인천·울산·충북·전북)
- **범위 외**: 9세 미만·39세 초과 비제대군인 → `render_mode_select`가 별도 안내 화면을 띄우고 챗 진입을 차단

지원 지자체 키워드는 `SEOUL_KEYWORDS` / `YOUTH_ON_KEYWORDS` 상수. 가입 폼은 시/도 selectbox + 시/군/구·동 자유 입력으로 분리되어 있어, 매칭 누락(예: "강남구"만 입력) 사고를 막는다.

### OpenAI 챗 세션 — 도구 호출 패턴 (`chat.py`)

`start_chat_session()`이 모드별 시스템 프롬프트(`prompts.PRE_SYSTEM_TEMPLATE` / `POST_SYSTEM_TEMPLATE`)로 `st.session_state.history`를 초기화하고 kickoff 메시지로 첫 발화를 강제한다. 모델은 `OPENAI_MODEL`(기본 `gpt-4o-mini`). 매 턴마다 `run_chat_turn()`이 `client.chat.completions.create(...)`를 호출하고, 응답에 `tool_calls`가 있으면 `tools.TOOL_DISPATCH` 매핑으로 실제 Python 함수를 실행한 뒤 결과를 `role: tool` 메시지로 history에 넣고 다시 호출(최대 `MAX_TOOL_LOOPS=6`회). 호출 실패 시 user 메시지를 rollback해서 중복 누적을 막는다.

도구 함수는 모델이 호출하면 **즉시 `st.session_state`를 변경**한다 (사이드 이펙트 함수, `tools.py`):

- 사전상담: `update_report`, `set_stage`, `trigger_safety_alert`
- 사후관리: `record_post_care`, `set_stage`, `trigger_safety_alert`

도구 스키마는 `tools.py`의 `UPDATE_REPORT_TOOL` / `TRIGGER_SAFETY_ALERT_TOOL` / `RECORD_POST_CARE_TOOL`의 정적 dict + 모드별 enum을 끼워넣는 `_set_stage_tool_for(stages)` 빌더로 구성된다 (`set_stage`는 사전·사후 stage가 다르므로 합쳐서 enum을 주지 않는다). `build_pre_tools()` / `build_post_tools()`가 `start_chat_session` 시점에 모드에 맞는 도구 목록을 만들어준다. 도구 시그니처를 바꿀 때는 (1) Python 함수 시그니처, (2) 같은 파일의 JSON 스키마, (3) `prompts.py`의 사용 설명 세 곳을 같이 갱신한다.

라우트별 시스템 프롬프트의 H 단계 안내 문장은 `routing.routing_guidance_for(user)`가 분기해서 `{routing_guidance}` 자리에 채운다. 라우트가 바뀔 때 시스템 프롬프트 자체 대신 이 함수만 수정하면 된다.

### 입출력

- 사용자 입력: `st.chat_input`(텍스트) + 사후관리에선 영수증 이미지 업로드. 영수증은 expander 안의 "📤 이 영수증 보내기" 버튼으로 텍스트 없이도 즉시 전송 가능 (선택적 메모 텍스트 동반). 이미지는 base64 data URL로 인코딩되어 OpenAI vision 포맷(`image_url`)으로 들어간다.
- 영수증 이미지는 모델이 한 번 처리하면 다음 턴부터 `_strip_images_in_place()`가 history의 image_url 파트를 placeholder 텍스트로 치환한다 (토큰 비용 방지).
- 어시스턴트 응답: 텍스트로 채팅창에 표시.

응답은 짧게 가야 한다. 시스템 프롬프트가 "2~3문장 / 목록·이모지·번호 금지"를 강제하므로 프롬프트 수정 시 이 제약을 보존한다.

### 위기 신호 처리

`trigger_safety_alert(reason)` 호출 시 `st.session_state.safety_alert`가 세팅되고 `render_chat`이 상단에 빨간 배너 + 1393/1577-1391/1366/112/120 안내를 띄운다. 자해·학대·방임 등 신호에서 모델이 자동 호출하도록 시스템 프롬프트에 명시되어 있다.

### 상태 리셋·모드 전환

- 사이드바 **"🔄 처음부터 다시"**: `st.session_state` 전체 삭제. 새 키를 추가했을 때 이 버튼 동작에 영향이 가는지 점검.
- 사이드바 **"← 메뉴로 돌아가기"** (chat phase일 때만): 챗 관련 상태(messages·history·report·post_*·safety_alert)만 비우고 `phase="mode"`로 되돌린다 — 회원 정보는 유지.

## 작업 시 주의

- 사용자 노출 문구는 모두 한국어. 시스템 프롬프트의 어조(공공복지·동행·맞춤형)에 맞춘다.
- 메모리 노트와 현재 코드 사이에 범위 차이가 있을 수 있다. 메모리는 "데모 범위 = 2단계 사전상담만"이라고 적혀 있지만 현재 `app.py`에는 사후관리 모드까지 구현되어 있다. 범위 관련 결정은 메모리 단정 대신 사용자에게 확인한다.
- DB·인증·실서비스 보안은 의도적으로 최소화되어 있다 (세션 메모리만 사용). PR 단계가 아닌 데모이므로 영구 저장소 추가는 사용자 합의 후에.
- `image.png`(8.7MB)는 기획 스크립트 참고 자료다. 커밋되어 있어도 코드가 직접 읽지는 않는다.
