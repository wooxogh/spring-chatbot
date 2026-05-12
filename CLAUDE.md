# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 정체

서비스명 **'스프링(Spring)'** — 가족돌봄청소년·청년 원스톱 공공복지 AI 안내 서비스의 **챗봇 프로토타입 (Python/Streamlit 단순 데모)**.

폴더명에 'spring'이 들어가지만 Spring Boot/Java가 아니다. Spring은 서비스명이고 스택은 Python이다.

공식 서비스 기획안: `docs/spring-service-spec.md`. 기능 추가·수정 시 항상 이 기획안의 4단계 플로우(선제 발굴 → 온보딩/스크리닝 → Route A/B 라우팅 → 사후관리)에 비추어 어디에 해당하는지 먼저 확인할 것. 기획안과 어긋나는 변경은 사용자에게 확인을 받는다.

## 실행 / 셋업

```bash
# 처음 한 번
cp .env.example .env   # GEMINI_API_KEY 채우기 — https://aistudio.google.com/apikey
.venv/bin/pip install -r requirements.txt

# 매번 실행
.venv/bin/streamlit run app.py
```

테스트·린트·빌드 스크립트는 없다 (단순 데모).

## 아키텍처

전체 앱이 단일 파일 `app.py`에 들어있다. Streamlit 세션 상태로 4단계 phase 머신을 돌린다:

```
consent  →  signup  →  mode  →  chat
(동의)    (이름/생년월일/거주지)  (사전상담|사후관리)  (Gemini 챗)
```

각 phase는 `render_consent / render_signup / render_mode_select / render_chat` 한 함수가 그린다. 파일 끝의 라우터(`phase = st.session_state.phase`)가 분기한다.

### 라우팅 판별 (`determine_route`)

이미지 스크립트(`image.png`) 기준으로 가입 정보에서 즉시 결정된다. 시스템 프롬프트와 첫 인사 문장이 라우트별로 다르므로 라우팅 로직을 바꿀 때는 인사 문구(`branch_message`)도 함께 본다.

- **Route A (포털 불가, 오프라인)**: 9~13세, 또는 39세 초과/제대군인, 또는 간편인증 불가
- **Route B (포털 가능)**: 14~39세 + 서울(→서울복지포털) 또는 청년ON 관할 지역(인천·울산·충북·전북)
- **범위 외**: 9세 미만·39세 초과 비제대군인 → 다른 자원 안내

지원 지자체 키워드는 `SEOUL_KEYWORDS` / `YOUTH_ON_KEYWORDS` 상수. 시/도 키워드만 매칭하므로 "서울특별시 강남구"처럼 시 단위가 들어와도 동작한다.

### Gemini 챗 세션 — 도구 호출 패턴

`_start_chat_session()`이 모드별 시스템 프롬프트(`PRE_SYSTEM_TEMPLATE` / `POST_SYSTEM_TEMPLATE`)와 tool 함수 목록으로 `client.chats.create(...)`를 호출해서 세션을 만들고, kickoff 메시지로 첫 발화를 강제한다. 모델은 `gemini-2.5-flash`.

도구 함수는 모델이 호출하면 **즉시 `st.session_state`를 변경**한다 (사이드 이펙트 함수):

- 사전상담: `update_report`, `set_stage`, `trigger_safety_alert`
- 사후관리: `record_post_care`, `set_stage`, `trigger_safety_alert`

함수의 docstring과 파라미터 설명이 그대로 Gemini의 function declaration이 되므로 docstring을 함부로 줄이지 말 것. 도구 시그니처를 바꾸면 시스템 프롬프트의 사용 설명도 같이 갱신한다.

### 멀티모달 입출력

- 사용자 입력: `st.chat_input`(텍스트) + `st.audio_input`(오디오, WAV로 전달) + 사후관리에선 영수증 이미지 업로드. 세 가지가 하나의 `parts` 리스트로 합쳐져 `chat.send_message(parts)`로 전달된다.
- 어시스턴트 응답: 텍스트는 채팅창에, gTTS로 합성한 한국어 MP3가 `st.session_state.next_audio`에 담겨 다음 rerun에서 자동 재생.

응답은 음성 변환 전제로 짧게 가야 한다. 시스템 프롬프트가 "2~3문장 / 목록·이모지·번호 금지"를 강제하므로 프롬프트 수정 시 이 제약을 보존한다.

### 위기 신호 처리

`trigger_safety_alert(reason)` 호출 시 `st.session_state.safety_alert`가 세팅되고 `render_chat`이 상단에 빨간 배너 + 1393/1577-1391/1366/112/120 안내를 띄운다. 자해·학대·방임 등 신호에서 모델이 자동 호출하도록 시스템 프롬프트에 명시되어 있다.

### 상태 리셋

사이드바의 "🔄 처음부터 다시" 버튼이 `st.session_state` 전체를 삭제한다. 새 키를 추가했을 때 이 버튼 동작에 영향이 가는지(또는 새 키만 따로 비워야 하는지) 점검할 것.

## 작업 시 주의

- 사용자 노출 문구는 모두 한국어. 시스템 프롬프트의 어조(공공복지·동행·맞춤형)에 맞춘다.
- 메모리 노트와 현재 코드 사이에 범위 차이가 있을 수 있다. 메모리는 "데모 범위 = 2단계 사전상담만"이라고 적혀 있지만 현재 `app.py`에는 사후관리 모드까지 구현되어 있다. 범위 관련 결정은 메모리 단정 대신 사용자에게 확인한다.
- DB·인증·실서비스 보안은 의도적으로 최소화되어 있다 (세션 메모리만 사용). PR 단계가 아닌 데모이므로 영구 저장소 추가는 사용자 합의 후에.
- `image.png`(8.7MB)는 기획 스크립트 참고 자료다. 커밋되어 있어도 코드가 직접 읽지는 않는다.
