"""스프링(Spring) — 가족돌봄청년 사전상담·사후관리 데모

기획안: docs/spring-service-spec.md
참조 스크립트: image.png (서울시 거주 = 서울시복지재단 기준)

플로우 (image.png):
    회원/가입 (이름·생년월일·주소지 수집)
        → 챗봇 사전 상담
            나이 분기:
              · 만 14세 이상 + 서울 + 간편인증 가능 → 포털 사용 가능 (Route B)
              · 만 9~13세                       → 포털 사용 불가 → 동주민센터 방문 (Route A)
              · 만 39세 초과·제대군인           → 포털 사용 불가 → 구청 방문      (Route A)
              · 그 외                            → 비대상 안내
        → 사후 관리 (별도 모드)
            · 일상 돌봄 현황 (ADL/IADL)
            · 경제적 지출 내역 (영수증 업로드)
            · 자격 유지 / 거주지 확인

실행:
    .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import base64
import os
from datetime import date, datetime

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from chat import format_chat_error, run_chat_turn, start_chat_session
from routing import calc_age, determine_route
from tools import POST_STAGES, PRE_STAGES

load_dotenv()

# ======================================================================
# 페이지 설정
# ======================================================================
st.set_page_config(page_title="스프링 — 가족돌봄청년 원스톱 안내", page_icon="🌱", layout="wide")

# ======================================================================
# 작은 스타일 (브랜드 톤)
# ======================================================================
st.markdown(
    """
    <style>
      :root { --spring-orange: #ff6b3d; --spring-peach: #fde4d8; --spring-mint: #a8e0c5; --spring-ink:#333; --spring-mute:#9aa0a6; }
      .spring-title { color: var(--spring-orange); font-weight: 800; }
      .spring-tag {
        display:inline-block; padding:4px 10px; border-radius:999px;
        background:var(--spring-peach); color:var(--spring-orange);
        font-size:0.85em; font-weight:700; margin-right:6px;
      }
      .spring-quote { color:#666; font-style:italic; }

      /* 상단 phase breadcrumb */
      .crumbs {
        display:flex; flex-wrap:wrap; gap:6px; align-items:center;
        font-size:0.85em; margin: 4px 0 16px 0;
      }
      .crumb {
        padding:4px 10px; border-radius:999px;
        background:#f4f4f6; color:#888;
      }
      .crumb.active { background:var(--spring-orange); color:#fff; font-weight:700; }
      .crumb.done   { background:var(--spring-mint); color:#0d4d2c; }
      .crumb-sep    { color:#bbb; padding:0 2px; }

      /* 사이드바 — 세로 진행 단계 */
      .stage-row {
        padding:7px 10px; margin:3px 0; border-radius:6px;
        border-left:3px solid #eee;
        color: var(--spring-mute); font-size:0.92em;
        display:flex; align-items:center; gap:8px;
      }
      .stage-row .stage-num {
        width:18px; height:18px; line-height:18px; text-align:center;
        border-radius:999px; background:#eee; color:#888;
        font-size:0.75em; font-weight:700; flex:0 0 18px;
      }
      .stage-row.active {
        border-left-color: var(--spring-orange);
        background: var(--spring-peach);
        color: var(--spring-orange); font-weight:700;
      }
      .stage-row.active .stage-num { background: var(--spring-orange); color:#fff; }
      .stage-row.done {
        border-left-color: var(--spring-mint);
        color:#0d4d2c;
      }
      .stage-row.done .stage-num { background: var(--spring-mint); color:#0d4d2c; }

      /* 챗 헤더 현재 단계 칩 */
      .stage-now {
        display:inline-block; vertical-align:middle; margin-left:10px;
        padding:4px 10px; border-radius:999px;
        background:var(--spring-peach); color:var(--spring-orange);
        font-size:0.75em; font-weight:700;
      }

      /* 레포트 — 라벨/값 한 줄 */
      .rep-row {
        display:flex; justify-content:space-between; gap:12px;
        padding:5px 0; border-bottom:1px dashed #eee;
        font-size:0.92em;
      }
      .rep-row:last-child { border-bottom:none; }
      .rep-k { color:#666; flex:0 0 38%; }
      .rep-v { color:#222; text-align:right; flex:1; word-break:break-word; }
      .rep-row.empty .rep-v { color:#c0c4cc; font-style:italic; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ======================================================================
# 세션 상태 초기화
# ======================================================================
def _init_state() -> None:
    defaults = {
        "phase": "consent",          # consent → signup → mode → chat
        "mode": None,                 # "pre" (사전상담) | "post" (사후관리)
        "consent_given": False,
        "consent_timestamp": None,
        # 회원 정보
        "user": {},                   # {name, birth, age, region, route, can_portal}
        # 챗봇
        "messages": [],
        "stage": "첫 인사",
        "report": {},
        "safety_alert": None,
        # 사후관리 — 영수증/지출
        "post_receipts": [],          # [{filename, amount, item}]
        "post_residency_change": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()

# ======================================================================
# OpenAI 클라이언트
# ======================================================================
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    st.error("⚠️ OPENAI_API_KEY가 설정되지 않았습니다.")
    st.info(
        "`.env.example`을 `.env`로 복사하고 키를 입력한 뒤 다시 실행해주세요.\n"
        "https://platform.openai.com/api-keys 에서 발급 가능합니다."
    )
    st.stop()

if "client" not in st.session_state:
    st.session_state.client = OpenAI(api_key=api_key)



# ======================================================================
# 공통 — phase breadcrumb (동의 → 가입 → 모드 → 상담)
# ======================================================================
_PHASES = [("consent", "동의"), ("signup", "정보 입력"), ("mode", "메뉴 선택"), ("chat", "AI 상담")]


def render_phase_breadcrumb() -> None:
    phase = st.session_state.phase
    cur = next((i for i, (k, _) in enumerate(_PHASES) if k == phase), 0)
    parts: list[str] = []
    for i, (_, label) in enumerate(_PHASES):
        if i < cur:
            cls, txt = "done", f"✓ {label}"
        elif i == cur:
            cls, txt = "active", f"{i + 1}. {label}"
        else:
            cls, txt = "", f"{i + 1}. {label}"
        parts.append(f"<span class='crumb {cls}'>{txt}</span>")
    sep = "<span class='crumb-sep'>›</span>"
    st.markdown(f"<div class='crumbs'>{sep.join(parts)}</div>", unsafe_allow_html=True)


# ======================================================================
# 0. 동의 게이트
# ======================================================================
def render_consent() -> None:
    st.markdown("<h1>🌱 <span class='spring-title'>스프링 (Spring)</span></h1>", unsafe_allow_html=True)
    st.caption("가족을 품은 온기로 피워낸 봄, 이제는 그대의 봄을 향해 도약하도록")
    render_phase_breadcrumb()
    st.subheader("시작하기 전에")

    with st.container(border=True):
        st.markdown(
            """
            **‘스프링’은 가족돌봄청소년·청년 지원 제도 안내 및 신청을 돕기 위한 정부 산하 공공복지 AI 안내 서비스**입니다.

            - 본 사전 상담은 **신청에 앞서, 본인의 상황에 맞는 안내를 드리기 위한 절차**입니다.
            - 답변하신 내용은 **사전 상담 보조 자료로만 사용**되며, 「개인정보 보호법」에 따라 안전하게 보관됩니다.
            - 부담 갖지 마시고 편하게 말씀해주세요. **언제든 중단**하실 수 있습니다.
            - 답하기 어려운 항목은 **건너뛰셔도 됩니다.**
            """
        )

    st.markdown("### ✅ 시작 전 동의 항목")
    a = st.checkbox("**[필수]** 개인정보 수집·이용 동의 — 이름, 생년월일, 거주지, 돌봄 정보를 사전 상담 목적으로 수집합니다.")
    b = st.checkbox("**[필수]** 민감정보 처리 동의 — 돌봄 대상자의 건강·경제 상황 등 민감정보를 처리합니다.")
    c = st.checkbox("**[필수]** 만 14세 미만은 법정대리인(부모·보호자)이 함께 동의해야 합니다. 이를 확인하였습니다.")

    st.markdown("---")
    if st.button("✨ 동의하고 다음으로", type="primary", disabled=not (a and b and c), use_container_width=True):
        st.session_state.consent_given = True
        st.session_state.consent_timestamp = datetime.now().isoformat(timespec="seconds")
        st.session_state.phase = "signup"
        st.rerun()

    if not (a and b and c):
        st.caption("모든 필수 항목에 동의해야 시작할 수 있습니다.")

    st.caption("긴급 상황이라면 동의 절차 없이도 즉시 도움 받으실 수 있어요 — **1393**(자살예방), **112**(신고), **120**(안심돌봄).")


# ======================================================================
# 1. 회원/가입 — 이름·생년월일·주소지
# ======================================================================
def render_signup() -> None:
    st.markdown("<h1>🌱 <span class='spring-title'>회원 가입</span></h1>", unsafe_allow_html=True)
    st.caption("이름·생년월일·주소지를 받아 챗봇이 사전 상담을 자동으로 맞춰 드려요.")
    render_phase_breadcrumb()

    SIDO_OPTIONS = [
        "서울특별시",
        "인천광역시",
        "울산광역시",
        "충청북도",
        "전라북도",
        "기타 (직접 입력)",
    ]

    with st.form("signup_form", border=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("이름", placeholder="예) 김봄이", max_chars=20)
            birth = st.date_input(
                "생년월일",
                value=date(2005, 1, 1),
                min_value=date(1970, 1, 1),
                max_value=date.today(),
                format="YYYY-MM-DD",
            )
            is_veteran = st.checkbox("제대군인입니다 (해당 시 체크)")
        with col2:
            sido = st.selectbox(
                "거주지 — 시/도",
                SIDO_OPTIONS,
                index=0,
                help="라우팅 판별 기준이에요. 지원 지자체(서울·인천·울산·충북·전북) 외는 ‘기타’를 선택하세요.",
            )
            district = st.text_input(
                "거주지 — 시/군/구·동 (선택)",
                placeholder="예) 강남구 역삼동",
                max_chars=40,
            )
            sido_etc = ""
            if sido == "기타 (직접 입력)":
                sido_etc = st.text_input("기타 시/도 직접 입력", placeholder="예) 경기도, 부산광역시")
            simple_auth = st.checkbox("간편인증(공동인증서·PASS 등) 사용 가능", value=True)

        submitted = st.form_submit_button("✨ 사전 상담 시작", type="primary", use_container_width=True)

    if submitted:
        base_sido = sido_etc.strip() if sido == "기타 (직접 입력)" else sido
        region_full = (base_sido + " " + district.strip()).strip()
        if not name.strip() or not base_sido:
            st.error("이름과 거주지(시/도)는 필수입니다.")
            return
        age = calc_age(birth)
        route = determine_route(
            age,
            region_full,
            is_veteran=is_veteran,
            force_route_a=not simple_auth,
        )
        st.session_state.user = {
            "name": name.strip(),
            "birth": birth.isoformat(),
            "age": age,
            "region": region_full,
            "is_veteran": is_veteran,
            "simple_auth": simple_auth,
            "route": route["route"],
            "route_label": route["route_label"],
            "can_portal": route["can_portal"],
            "branch_message": route["branch_message"].replace("OO님", f"{name.strip()}님"),
        }
        st.session_state.phase = "mode"
        st.rerun()


# ======================================================================
# 2. 모드 선택 — 사전상담 / 사후관리
# ======================================================================
def render_mode_select() -> None:
    u = st.session_state.user
    st.markdown(f"<h1>🌱 <span class='spring-title'>{u['name']}님, 환영해요</span></h1>", unsafe_allow_html=True)
    st.markdown(
        f"<span class='spring-tag'>만 {u['age']}세</span> "
        f"<span class='spring-tag'>{u['region']}</span> "
        f"<span class='spring-tag'>{u['route_label']}</span>",
        unsafe_allow_html=True,
    )
    render_phase_breadcrumb()

    # OUT_OF_SCOPE — 가족돌봄청년 사업의 연령·자격 범위 밖
    if u.get("route") == "OUT_OF_SCOPE":
        with st.container(border=True):
            st.warning(
                "**가족돌봄청년 지원 사업의 연령 범위(만 9~39세)에 해당하지 않으세요.**\n\n"
                "이 데모는 그 범위 내에서만 사전 상담을 제공해요. 다만 도움받으실 수 있는 다른 자원이 있으니 안내해 드릴게요."
            )
            st.markdown(
                "- ☎️ **120 안심돌봄** (서울) — 돌봄·복지 종합 안내\n"
                "- ☎️ **1577-1391** 노인보호전문기관\n"
                "- ☎️ **1366** 여성긴급전화\n"
                "- 가까운 **동주민센터** 복지 담당자 상담"
            )
            if st.button("← 회원 정보 다시 입력", key="back_signup_oos"):
                st.session_state.phase = "signup"
                st.rerun()
        return

    st.write("진행하실 메뉴를 골라주세요.")

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.subheader("🌷 사전 상담")
            st.caption("처음 신청 전, AI 상담사가 가족돌봄 상황을 듣고 맞춤 라우트를 안내합니다.")
            st.markdown(
                "- 대상자와의 관계 확인\n"
                "- 가족 구성원·돌봄 내용\n"
                "- 경제 상황·어려움\n"
                "- 라우팅 안내 (포털 vs 오프라인)"
            )
            if st.button("사전 상담 시작 →", key="mode_pre", use_container_width=True, type="primary"):
                st.session_state.mode = "pre"
                st.session_state.phase = "chat"
                st.session_state.stage = "첫 인사"
                start_chat_session()
                st.rerun()
    with col2:
        with st.container(border=True):
            st.subheader("🍃 사후 관리 (월간 돌봄기록서)")
            st.caption("**이미 가족돌봄청년 지원에 선정된 분만 이용하세요.** 매월 돌봄기록서를 작성합니다.")
            st.markdown(
                "- 지난 달 돌봄 변화 (ADL/IADL)\n"
                "- 경제적 지출 (영수증 업로드)\n"
                "- 거주지·자격 유지 확인"
            )
            st.caption("⚠️ 데모이므로 실제 선정 여부를 확인하지는 않습니다.")
            if st.button("사후 관리 시작 →", key="mode_post", use_container_width=True):
                st.session_state.mode = "post"
                st.session_state.phase = "chat"
                st.session_state.stage = "월간 기록"
                start_chat_session()
                st.rerun()

    st.divider()
    if st.button("← 회원 정보 다시 입력"):
        st.session_state.phase = "signup"
        st.rerun()


# ======================================================================
# 4. 사이드바
# ======================================================================
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("<h2>🌱 <span class='spring-title'>스프링</span></h2>", unsafe_allow_html=True)
        st.caption("가족돌봄청년 원스톱 안내 · 데모")
        st.divider()

        u = st.session_state.user
        if u:
            st.markdown(f"**{u['name']}** 님 · 만 **{u['age']}**세")
            st.caption(u["region"])
            st.markdown(f"<span class='spring-tag'>{u['route_label']}</span>", unsafe_allow_html=True)
            st.divider()

        if st.session_state.phase == "chat":
            stages = PRE_STAGES if st.session_state.mode == "pre" else POST_STAGES
            try:
                idx = stages.index(st.session_state.stage)
            except ValueError:
                idx = 0
            st.subheader("진행 단계")
            for i, s in enumerate(stages):
                cls = "active" if i == idx else ("done" if i < idx else "")
                num = "✓" if i < idx else str(i + 1)
                st.markdown(
                    f"<div class='stage-row {cls}'><span class='stage-num'>{num}</span>{s}</div>",
                    unsafe_allow_html=True,
                )
            if st.button("← 메뉴로 돌아가기", key="back_to_mode", use_container_width=True):
                st.session_state.phase = "mode"
                st.session_state.mode = None
                st.session_state.messages = []
                st.session_state.history = []
                st.session_state.report = {}
                st.session_state.post_receipts = []
                st.session_state.post_residency_change = None
                st.session_state.safety_alert = None
                st.rerun()
            st.divider()

        st.subheader("긴급 연락처")
        st.markdown(
            "- ☎️ **1393** 자살예방상담\n"
            "- ☎️ **1577-1391** 노인보호전문기관\n"
            "- ☎️ **1366** 여성긴급전화\n"
            "- ☎️ **112** 신고\n"
            "- ☎️ **120** 안심돌봄(서울)"
        )

        st.divider()
        if st.session_state.consent_timestamp:
            st.caption(f"🔐 동의 시각: {st.session_state.consent_timestamp}")
        if st.button("🔄 처음부터 다시", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()


# ======================================================================
# 5. 챗봇 화면
# ======================================================================
def render_chat() -> None:
    u = st.session_state.user
    is_pre = st.session_state.mode == "pre"
    title = "AI 사전 상담" if is_pre else "AI 사후 관리 (월간 돌봄기록서)"

    col_chat, col_side = st.columns([3, 2])

    with col_chat:
        st.markdown(
            f"<h2>{title}<span class='stage-now'>현재 단계 · {st.session_state.stage}</span></h2>",
            unsafe_allow_html=True,
        )
        st.caption("편하게 텍스트로 입력하세요. 이름·연령·거주지는 이미 알고 있어요.")

        if st.session_state.safety_alert:
            with st.container(border=True):
                st.error(
                    f"🚨 **위기 신호 감지**: {st.session_state.safety_alert}\n\n"
                    "지금 바로 아래 번호로 연락하시거나, 가까운 어른께 도움을 요청해주세요."
                )
                b1, b2, b3 = st.columns(3)
                b1.link_button("☎ 1393 자살예방", "tel:1393", use_container_width=True)
                b2.link_button("☎ 112 신고", "tel:112", use_container_width=True)
                b3.link_button("☎ 120 안심돌봄", "tel:120", use_container_width=True)
                if st.button("알림 닫기", key="dismiss_safety", use_container_width=True):
                    st.session_state.safety_alert = None
                    st.rerun()

        chat_box = st.container(height=440)
        with chat_box:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

        # 사후관리 모드: 영수증 업로드 옵션 — 텍스트 없이도 ‘보내기’ 버튼으로 즉시 전송 가능
        uploaded_receipt = None
        receipt_memo = ""
        receipt_send_clicked = False
        if not is_pre:
            with st.expander("🧾 영수증 사진 업로드 (선택)"):
                uploaded_receipt = st.file_uploader(
                    "병원비·약값 영수증 사진",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=f"receipt_{len(st.session_state.messages)}",
                )
                if uploaded_receipt is not None:
                    receipt_memo = st.text_input(
                        "메모 (선택)",
                        placeholder="예) 7월 약값 영수증입니다",
                        key=f"receipt_memo_{len(st.session_state.messages)}",
                    )
                    receipt_send_clicked = st.button(
                        "📤 이 영수증 보내기",
                        type="primary",
                        use_container_width=True,
                        key=f"receipt_send_{len(st.session_state.messages)}",
                    )

        text_input = st.chat_input("메시지를 입력하세요…")

        receipt_bytes, receipt_mime, receipt_name = (None, None, None)
        if uploaded_receipt is not None and receipt_send_clicked:
            receipt_bytes = uploaded_receipt.read()
            receipt_mime = uploaded_receipt.type or "image/jpeg"
            receipt_name = uploaded_receipt.name

        # 메시지를 보내는 트리거: (a) chat_input 엔터, (b) 영수증 보내기 버튼 클릭
        effective_text = text_input if text_input else (receipt_memo if receipt_bytes else "")
        if effective_text or receipt_bytes:
            display = effective_text or ""
            if receipt_name:
                display = (display + f"\n📎 {receipt_name} (영수증)").strip()
            st.session_state.messages.append({"role": "user", "content": display})

            if receipt_bytes:
                b64 = base64.b64encode(receipt_bytes).decode("ascii")
                content_parts: list = []
                if effective_text:
                    content_parts.append({"type": "text", "text": effective_text})
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{receipt_mime};base64,{b64}"},
                })
                content_parts.append({
                    "type": "text",
                    "text": "[시스템] 사용자가 영수증 사진을 첨부했습니다. 사진에서 항목/금액을 읽어 record_post_care 도구로 기록하세요.",
                })
                user_content = content_parts
            else:
                user_content = effective_text or ""

            with st.spinner("스프링이 듣고 있어요…"):
                try:
                    assistant_text = run_chat_turn(user_content) or "(상담사가 응답하지 않았습니다. 다시 말씀해주세요.)"
                except Exception as e:
                    assistant_text = format_chat_error(e)

            st.session_state.messages.append({"role": "assistant", "content": assistant_text})
            st.rerun()

    with col_side:
        if is_pre:
            _render_pre_report()
        else:
            _render_post_report()


def _rep_row(label: str, value: str | None) -> None:
    v = (value or "").strip()
    cls = "rep-row" if v else "rep-row empty"
    st.markdown(
        f"<div class='{cls}'><span class='rep-k'>{label}</span><span class='rep-v'>{v or '—'}</span></div>",
        unsafe_allow_html=True,
    )


def _render_pre_report() -> None:
    u = st.session_state.user
    r = st.session_state.report
    st.markdown("<h2>📋 사전 상담 레포트</h2>", unsafe_allow_html=True)
    st.caption("대화에 따라 자동으로 작성됩니다. 상담원에게 즉시 공유 가능한 ‘사전 맥락 보고서’.")

    with st.container(border=True):
        st.markdown("**기본 정보 (가입 시)**")
        _rep_row("이름", u.get("name"))
        _rep_row("만 나이", f"{u.get('age', '—')}세" if u.get("age") is not None else None)
        _rep_row("거주지", u.get("region"))
        _rep_row("간편인증", "가능" if u.get("simple_auth") else "불가")

    with st.container(border=True):
        st.markdown("**돌봄 정보**")
        _rep_row("대상자", r.get("care_target"))
        _rep_row("민법상 가족 여부", r.get("care_target_relation_legal"))
        _rep_row("상태", r.get("care_target_condition"))
        _rep_row("동거 여부", r.get("living_with_target"))
        _rep_row("함께 돌보는 분", r.get("cocaregivers"))
        _rep_row("돌봄 내용", r.get("care_content"))
        _rep_row("주당 돌봄 시간", r.get("care_hours_per_week"))

    with st.container(border=True):
        st.markdown("**본인 상황**")
        _rep_row("어려움", r.get("difficulties"))
        _rep_row("경제 상황", r.get("economic_status"))

    with st.container(border=True):
        st.markdown("**1차 판별 결과**")
        st.success(f"➡️ {u.get('route_label', '판별 진행 중…')}")
        if r.get("notes"):
            st.caption(f"메모: {r['notes']}")


def _render_post_report() -> None:
    u = st.session_state.user
    r = st.session_state.report
    st.markdown("<h2>🧾 월간 돌봄기록서 초안</h2>", unsafe_allow_html=True)
    st.caption("대화·영수증을 바탕으로 자동 작성됩니다. 익월 10일 제출 양식.")

    with st.container(border=True):
        st.markdown("**대상자**")
        st.write(f"{u.get('name', '—')} 님 · 만 {u.get('age', '—')}세 · {u.get('region', '—')}")

    with st.container(border=True):
        st.markdown("**1. 일상 돌봄 변화 (ADL/IADL)**")
        st.write(r.get("daily_change", "—"))

    with st.container(border=True):
        st.markdown("**2. 경제적 지출 내역**")
        if st.session_state.post_receipts:
            total = 0
            for it in st.session_state.post_receipts:
                st.write(f"- {it['item']}: {it['amount']:,}원")
                total += int(it["amount"])
            st.write(f"**합계**: {total:,}원")
            if total >= 400_000:
                st.success("✅ 고부담형(월 40만 원 이상) 유지 기준 충족")
            else:
                st.info("ℹ️ 고부담형 유지 기준 미달 — 일반형 유지 가능성")
        else:
            st.write("—")

    with st.container(border=True):
        st.markdown("**3. 자격 유지 / 거주지 확인**")
        rc = st.session_state.post_residency_change
        if not rc:
            st.write("—")
        elif "이사" in rc or "전출" in rc:
            st.warning(f"⚠️ {rc} — 서울시 외 전출 시 자격 상실 가능. 담당 공무원 검토 필요.")
        else:
            st.success(f"✅ {rc}")


# ======================================================================
# 라우터
# ======================================================================
phase = st.session_state.phase

if phase == "consent":
    render_consent()
    st.stop()

render_sidebar()

if phase == "signup":
    render_signup()
elif phase == "mode":
    render_mode_select()
elif phase == "chat":
    render_chat()
else:
    st.session_state.phase = "consent"
    st.rerun()
