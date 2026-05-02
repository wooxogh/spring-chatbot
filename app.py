"""스프링(Spring) — 가족돌봄청년 사전상담 음성 데모

기획안: docs/spring-service-spec.md
범위: 2단계 (지능형 온보딩 및 1차 판별) = "사전 상담"
- 사후관리/신청/서류는 별도 프로젝트로 설계 예정

실행:
    pip install -r requirements.txt
    cp .env.example .env  # 후 GEMINI_API_KEY 입력
    streamlit run app.py
"""

import os
from datetime import datetime
from io import BytesIO

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from gtts import gTTS

load_dotenv()

# ======================================================================
# Streamlit 페이지 설정
# ======================================================================
st.set_page_config(page_title="스프링 — AI 사전상담", page_icon="🌱", layout="wide")

# ======================================================================
# Session state 초기화
# ======================================================================
if "report" not in st.session_state:
    st.session_state.report = {}
if "stage" not in st.session_state:
    st.session_state.stage = "인사·소개"
if "safety_alert" not in st.session_state:
    st.session_state.safety_alert = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "consent_given" not in st.session_state:
    st.session_state.consent_given = False
if "consent_timestamp" not in st.session_state:
    st.session_state.consent_timestamp = None

# ======================================================================
# Gemini가 자동 호출할 도구 함수 — st.session_state에 직접 기록
# ======================================================================
def update_report(
    name: str = "",
    age: int = 0,
    region: str = "",
    care_target: str = "",
    care_target_condition: str = "",
    care_content: str = "",
    care_hours_per_week: str = "",
    difficulties: str = "",
    economic_status: str = "",
    eligibility_route: str = "",
) -> str:
    """사용자에게서 정보를 얻을 때마다 호출하여 사전 상담 레포트를 업데이트합니다.

    Args:
        name: 사용자 이름.
        age: 만 나이 (정수).
        region: 거주 시/구 (예: 서울 강남구).
        care_target: 돌봄 대상자와의 관계 (예: 어머니, 할머니).
        care_target_condition: 돌봄 대상자의 상태 요약.
        care_content: 돌봄 내용 요약 (식사·이동·위생·약 등).
        care_hours_per_week: 주당 돌봄 시간 대략 (예: 약 30시간).
        difficulties: 본인이 겪는 어려움 (학업·일·건강·정서).
        economic_status: 경제 상황 요약 (수급 자격: 기초생활수급/차상위/일반, 돌봄으로 인한 경제적 부담, 의료비·생활비 어려움 등).
        eligibility_route: 1차 판별 결과 라우트.
    """
    if name:
        st.session_state.report["name"] = name
    if age:
        st.session_state.report["age"] = age
    if region:
        st.session_state.report["region"] = region
    if care_target:
        st.session_state.report["care_target"] = care_target
    if care_target_condition:
        st.session_state.report["care_target_condition"] = care_target_condition
    if care_content:
        st.session_state.report["care_content"] = care_content
    if care_hours_per_week:
        st.session_state.report["care_hours_per_week"] = care_hours_per_week
    if difficulties:
        st.session_state.report["difficulties"] = difficulties
    if economic_status:
        st.session_state.report["economic_status"] = economic_status
    if eligibility_route:
        st.session_state.report["eligibility_route"] = eligibility_route
    return "OK"


def set_stage(stage: str) -> str:
    """현재 상담 단계를 변경합니다.

    Args:
        stage: 다음 중 하나 — "인사·소개", "기본 정보 확인", "돌봄 실태조사", "라우팅 안내", "마무리".
    """
    st.session_state.stage = stage
    return "OK"


def trigger_safety_alert(reason: str) -> str:
    """자해·자살·학대·폭력·방임 등 위기 신호 감지 시 즉시 호출합니다.

    Args:
        reason: 위기 감지 이유 한 문장.
    """
    st.session_state.safety_alert = reason
    return "OK"


# ======================================================================
# 시스템 프롬프트 — Spring 사전상담 페르소나
# ======================================================================
SYSTEM_PROMPT = """당신은 가족돌봄청소년·청년 원스톱 공공복지 AI 안내 서비스 '스프링(Spring)'의 사전 상담 AI 상담사입니다.

【슬로건】 "가족을 품은 온기로 피워낸 봄, 이제는 그대의 봄을 향해 도약하도록, Spring"
【타겟】 만 9세 ~ 39세, 법적 가족(민법상)을 돌보는 사람

【대화 원칙】
1. 친근하고 따뜻한 톤. 통화하듯 자연스럽게 말하기.
2. 한 번에 하나의 질문만. 묶어서 묻지 말 것.
3. 9세 어린이도 이해할 수 있는 쉬운 단어 사용.
4. 자연스러운 정서적 공감 표현 ("그러셨군요", "힘드셨겠어요", "잘 견뎌오셨네요").
5. 응답은 음성으로 변환되므로 짧게 (2~3문장 이내). 목록·숫자 나열·이모지 금지.
6. 답변에는 항상 사용자에게 들려줄 자연스러운 문장이 있어야 합니다 (도구만 호출하고 끝내지 말 것).

【사전 상담 흐름 — 이 단계만 담당. 신청·서류·사후관리는 별도】
① 인사·소개 (스프링 짧게 한 문장)
② 기본 정보 확인 (한 번에 하나씩): 이름 → 만 나이 → 거주 지역 → 돌봄 대상자와의 관계
③ 대화형 실태조사 (한 번에 한 항목씩, 자연스러운 흐름으로):
   - 돌봄 대상자의 상태 (질병·연령·장애 등)
   - 일상 돌봄 내용 (식사·이동·위생·약 챙김 등 ADL/IADL)
   - 주당 돌봄 시간 대략
   - 본인의 어려움 (학업·일·건강·정서)
   - **경제 상황** (수급 자격 — 기초생활수급/차상위/일반, 돌봄으로 인한 경제적 부담, 의료비·생활비 어려움)
     · 민감한 항목이므로 정중하게 묻고, "답하기 어려우시면 건너뛰셔도 돼요"라고 안내할 것.
④ 1차 적격 판별 + 라우팅 안내:
   - 만 9~13세 → "Route A 오프라인 — 보호자분과 동주민센터 방문이 필요해요"
   - 만 14세 이상 + 서울 거주 → "Route B 서울복지포털"
   - 만 14세 이상 + 청년ON 관할(인천·울산·충북·전북) → "Route B 청년ON"
   - 만 9세 미만 또는 39세 초과 또는 비가족 돌봄 → "비대상" 안내 + 안심돌봄120 등 다른 자원 안내
⑤ 마무리: 사전 상담 레포트가 정리되었음을 알리고, 다음 단계(신청)로 이어진다고 안내

【도구 사용 — 매우 중요】
- 사용자에게서 정보(이름·연령·지역·돌봄 대상자·돌봄 내용·시간·어려움·경제 상황)를 얻으면 **즉시** update_report 호출.
- 단계 진행 시 set_stage 호출 (예: 기본 정보 다 받으면 "돌봄 실태조사"로 변경).
- 자해·자살·학대·폭력·방임·심한 우울 신호 감지 시 **즉시** trigger_safety_alert 호출 후, 답변에 1393(자살예방상담)·1577-1391(노인보호)·1366(여성긴급)·112 안내.

【시작 인사 예시】
"안녕하세요. 가족돌봄청년 지원 서비스 스프링의 AI 상담사예요. 가족을 돌보느라 매일 애쓰고 계실 텐데, 오늘은 편하게 말씀해주시면 돼요. 먼저 성함이 어떻게 되세요?"
"""

# ======================================================================
# Gemini 클라이언트 + 채팅 세션
# ======================================================================
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    st.error("⚠️ GEMINI_API_KEY가 설정되지 않았습니다.")
    st.info("`.env.example`을 `.env`로 복사하고 키를 입력한 뒤 다시 실행해주세요.\nhttps://aistudio.google.com/apikey 에서 무료로 발급 가능합니다.")
    st.stop()

if "client" not in st.session_state:
    st.session_state.client = genai.Client(api_key=api_key)

# ======================================================================
# 동의 게이트 — 기획안 2단계: "개인정보 수집 동의를 가장 먼저 진행"
# ======================================================================
if not st.session_state.consent_given:
    st.title("🌱 스프링 (Spring)")
    st.subheader("가족돌봄청년 사전상담을 시작하기 전에")

    with st.container(border=True):
        st.markdown(
            """
            ### 📌 서비스 안내

            **'스프링'은 가족돌봄청소년·청년 지원 제도 안내 및 신청을 돕기 위한 정부 산하 공공복지 AI 안내 서비스**입니다.

            - 본 사전 상담은 **신청에 앞서, 본인의 상황에 맞는 안내를 드리기 위한 절차**입니다.
            - 답변하신 내용은 **사전 상담 보조 자료로만 사용**되며, 「개인정보 보호법」에 따라 안전하게 보관됩니다.
            - 부담 갖지 마시고 편하게 말씀해주세요. **언제든 중단**하실 수 있습니다.
            - 답하기 어려운 항목은 **건너뛰셔도 됩니다.**
            """
        )

    st.markdown("### ✅ 시작 전 동의 항목")

    consent_pi = st.checkbox(
        "**[필수]** 개인정보 수집·이용 동의 — 이름, 연령, 거주지, 돌봄 정보를 사전 상담 목적으로 수집합니다."
    )
    consent_sensitive = st.checkbox(
        "**[필수]** 민감정보 처리 동의 — 돌봄 대상자의 건강·경제 상황 등 민감정보를 처리하는 것에 동의합니다."
    )
    consent_minor = st.checkbox(
        "**[필수]** 만 14세 미만은 법정대리인(부모·보호자)이 함께 동의해야 합니다. 이를 확인하였습니다."
    )

    all_agreed = consent_pi and consent_sensitive and consent_minor

    st.markdown("---")
    if st.button(
        "✨ 동의하고 사전 상담 시작하기",
        type="primary",
        disabled=not all_agreed,
        use_container_width=True,
    ):
        st.session_state.consent_given = True
        st.session_state.consent_timestamp = datetime.now().isoformat(timespec="seconds")
        st.rerun()

    if not all_agreed:
        st.caption("모든 필수 항목에 동의해야 시작할 수 있습니다.")

    st.caption(
        "긴급 상황이라면 동의 절차 없이도 즉시 도움 받으실 수 있어요 — "
        "**1393**(자살예방), **112**(신고), **120**(안심돌봄)."
    )

    st.stop()

if "chat" not in st.session_state:
    st.session_state.chat = st.session_state.client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[update_report, set_stage, trigger_safety_alert],
            temperature=0.7,
        ),
    )
    # 시작 인사 트리거
    try:
        first_resp = st.session_state.chat.send_message(
            "[시스템] 사용자가 입장했습니다. 시작 인사로 대화를 열어주세요."
        )
        first_text = first_resp.text or "안녕하세요. 스프링 AI 상담사예요. 성함이 어떻게 되세요?"
    except Exception as e:
        first_text = f"(시작 오류: {e})"
    st.session_state.messages.append({"role": "assistant", "content": first_text})
    try:
        tts = gTTS(text=first_text, lang="ko")
        buf = BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        st.session_state.next_audio = buf.read()
    except Exception:
        st.session_state.next_audio = None

# ======================================================================
# 사이드바
# ======================================================================
with st.sidebar:
    st.title("🌱 스프링 (Spring)")
    st.caption("가족돌봄청년 사전상담 데모")
    st.divider()

    st.subheader("진행 단계")
    stages = ["인사·소개", "기본 정보 확인", "돌봄 실태조사", "라우팅 안내", "마무리"]
    try:
        idx = stages.index(st.session_state.stage)
    except ValueError:
        idx = 0
    for i, s in enumerate(stages):
        if i < idx:
            st.markdown(f"✅ {s}")
        elif i == idx:
            st.markdown(f"▶️ **{s}**")
        else:
            st.markdown(f"○ {s}")

    st.divider()
    st.subheader("긴급 연락처")
    st.markdown("☎️ **1393** 자살예방상담")
    st.markdown("☎️ **1577-1391** 노인보호전문기관")
    st.markdown("☎️ **1366** 여성긴급전화")
    st.markdown("☎️ **112** 신고")
    st.markdown("☎️ **120** 안심돌봄(서울)")

    st.divider()
    if st.session_state.consent_timestamp:
        st.caption(f"🔐 동의 시각: {st.session_state.consent_timestamp}")
    if st.button("🔄 대화 초기화", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ======================================================================
# 메인 UI — 좌:채팅 / 우:사전 상담 레포트
# ======================================================================
col_chat, col_report = st.columns([3, 2])

with col_chat:
    st.title("AI 사전상담")
    st.caption("음성으로 답하시거나, 아래 텍스트로 입력해도 됩니다.")

    if st.session_state.safety_alert:
        st.error(
            f"🚨 **위기 신호 감지**: {st.session_state.safety_alert}\n\n"
            "즉시 **1393**(자살예방상담)·**112**·**120**(안심돌봄)으로 연락하세요. "
            "가까운 어른에게도 도움을 요청해주세요."
        )

    # 대화 내역
    chat_box = st.container(height=420)
    with chat_box:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

    # TTS 자동재생 (대화 직후)
    if st.session_state.get("next_audio"):
        st.audio(st.session_state.next_audio, format="audio/mp3", autoplay=True)
        st.session_state.next_audio = None

    # 입력
    st.markdown("**🎙️ 마이크를 눌러 말씀하시거나, 아래 텍스트로 입력하세요.**")
    audio_data = st.audio_input(
        "음성 입력",
        label_visibility="collapsed",
        key=f"audio_{len(st.session_state.messages)}",
    )
    text_input = st.chat_input("텍스트로 입력...")

    user_audio_bytes = audio_data.read() if audio_data is not None else None
    user_text = text_input

    if user_audio_bytes or user_text:
        # 사용자 메시지 표시용
        display_text = user_text if user_text else "🎙️ (음성 입력)"
        st.session_state.messages.append({"role": "user", "content": display_text})

        # Gemini로 보낼 parts 구성
        parts = []
        if user_text:
            parts.append(types.Part.from_text(text=user_text))
        if user_audio_bytes:
            parts.append(types.Part.from_bytes(data=user_audio_bytes, mime_type="audio/wav"))

        with st.spinner("스프링이 듣고 있어요…"):
            try:
                response = st.session_state.chat.send_message(parts)
                assistant_text = (response.text or "").strip()
                if not assistant_text:
                    assistant_text = "(상담사가 응답하지 않았습니다. 다시 말씀해주세요.)"
            except Exception as e:
                assistant_text = f"(오류: {e})"

        st.session_state.messages.append({"role": "assistant", "content": assistant_text})

        # TTS
        try:
            tts = gTTS(text=assistant_text, lang="ko")
            buf = BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            st.session_state.next_audio = buf.read()
        except Exception:
            st.session_state.next_audio = None

        st.rerun()

with col_report:
    st.title("📋 사전 상담 레포트")
    st.caption("대화에 따라 자동으로 작성됩니다.")

    r = st.session_state.report

    with st.container(border=True):
        st.markdown("**기본 정보**")
        c1, c2 = st.columns(2)
        with c1:
            st.metric("이름", r.get("name", "—"))
            st.metric("거주지", r.get("region", "—"))
        with c2:
            st.metric("만 나이", str(r.get("age", "—")))
            st.metric("주당 돌봄", r.get("care_hours_per_week", "—"))

    with st.container(border=True):
        st.markdown("**돌봄 정보**")
        st.write(f"**대상자**: {r.get('care_target', '—')}")
        st.write(f"**상태**: {r.get('care_target_condition', '—')}")
        st.write(f"**돌봄 내용**: {r.get('care_content', '—')}")

    with st.container(border=True):
        st.markdown("**본인 상황**")
        st.write(f"**어려움**: {r.get('difficulties', '—')}")
        st.write(f"**경제 상황**: {r.get('economic_status', '—')}")

    with st.container(border=True):
        st.markdown("**1차 판별 결과**")
        if r.get("eligibility_route"):
            st.success(f"➡️ {r['eligibility_route']}")
        else:
            st.info("판별 진행 중…")
