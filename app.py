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
import json
import os
from datetime import date, datetime

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_MODEL = "gpt-4o-mini"

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
        font-size:0.55em; font-weight:700;
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
# 라우팅 판별 — 나이/지역 기반
# ======================================================================
SEOUL_KEYWORDS = ("서울",)
YOUTH_ON_KEYWORDS = ("인천", "울산", "충북", "전북", "충청북도", "전라북도")


def calc_age(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def determine_route(age: int, region: str, is_veteran: bool = False) -> dict:
    """이미지 스크립트 기준 분기.

    반환: {route, route_label, can_portal, branch_message}
    """
    in_seoul = any(k in region for k in SEOUL_KEYWORDS)
    in_youth_on = any(k in region for k in YOUTH_ON_KEYWORDS)

    if age < 9 or age > 39 + (5 if is_veteran else 0):
        return {
            "route": "OUT_OF_SCOPE",
            "route_label": "지원 대상 외 — 안심돌봄120(서울 ☎120) 또는 1577-1391(노인보호) 안내",
            "can_portal": False,
            "branch_message": (
                "안내드린 자료를 보니 가족돌봄청년 지원 사업의 연령 범위(만 9~39세)에는 "
                "해당하지 않으시는 것 같아요. 그래도 도움받으실 수 있는 다른 자원을 함께 찾아드릴게요."
            ),
        }

    if 9 <= age <= 13:
        return {
            "route": "ROUTE_A_OFFLINE_MINOR",
            "route_label": "Route A — 법정대리인과 동주민센터(또는 구청) 직접 방문 신청",
            "can_portal": False,
            "branch_message": (
                f"안녕하세요, OO님! 가입 정보를 보니 {age}살이시네요. 서울에 살고 계신 것도 확인했어요. "
                "OO님처럼 만 14세가 되지 않은 분들은 인터넷으로 직접 신청하는 것이 어려워요. "
                "그래서 ‘법정대리인’이라고 부르는, OO님을 소중하게 지켜주시고 보호해 주시는 어른"
                "(부모님·할머니 등)과 함께 직접 구청에 가서 신청하셔야 해요. "
                "신청하러 가시기 전에 OO님이 도움을 받으실 수 있는 상황인지 몇 가지만 여쭤볼게요. "
                "편안하게 말씀해 주세요."
            ),
        }

    if is_veteran or age > 39:
        return {
            "route": "ROUTE_A_OFFLINE_VETERAN",
            "route_label": "Route A — 간편인증 불가자, 구청 직접 방문 신청",
            "can_portal": False,
            "branch_message": (
                "안녕하세요, OO님! 가입 정보를 보니 39세 초과·제대군인에 해당하시네요. "
                "서울에 살고 계신 것도 확인했어요. OO님처럼 간편인증이 불가한 분들은 "
                "인터넷으로 직접 신청하는 것이 어렵고 직접 ‘구청’에 가서 신청하셔야 해요. "
                "신청하러 가시기 전에 OO님이 도움을 받으실 수 있는 상황인지 몇 가지만 "
                "여쭤볼게요. 편안하게 말씀해 주세요."
            ),
        }

    if in_seoul:
        return {
            "route": "ROUTE_B_SEOUL_PORTAL",
            "route_label": "Route B — 서울복지포털 온라인 신청",
            "can_portal": True,
            "branch_message": (
                "반가워요, OO님! 안녕하세요! 당신의 일상에 따뜻한 봄을 찾아줄 AI 비서 "
                "‘스프링’입니다. 본격적인 상담에 앞서, 한 가지만 확인해 주세요. "
                "본 서비스는 가족돌봄청년 지원 사업을 안내하고 신청을 돕기 위해 운영됩니다. "
                "상담 내용은 정확한 지원을 위해 기록되며, 나중에 복지 담당 선생님이 확인하실 "
                "수 있습니다. 계속 진행할까요?"
            ),
        }

    if in_youth_on:
        return {
            "route": "ROUTE_B_YOUTH_ON",
            "route_label": "Route B — 청년ON 온라인 신청",
            "can_portal": True,
            "branch_message": (
                "반가워요, OO님! 알려주신 정보를 보니 청년ON 관할 지역(인천·울산·충북·전북)에 "
                "살고 계시고 나이도 해당하시네요. 청년ON 기준으로 도와드릴게요. "
                "본 서비스는 가족돌봄청년 지원 사업을 안내하고 신청을 돕기 위해 운영됩니다. "
                "계속 진행할까요?"
            ),
        }

    return {
        "route": "ROUTE_B_OTHER_REGION",
        "route_label": "지원 지자체 외 — 거주지 기준 별도 안내 필요",
        "can_portal": False,
        "branch_message": (
            f"알려주신 거주지({region})는 현재 데모가 지원하는 지자체 범위(서울·인천·울산·충북·전북) "
            "밖이에요. 그래도 사전 상담은 진행해 드리고, 마지막에 가까운 자원을 안내해 드릴게요."
        ),
    }


# ======================================================================
# 도구 함수 — 모델이 호출하여 레포트/단계/위기경보 업데이트
# ======================================================================
def update_report(
    care_target: str = "",
    care_target_relation_legal: str = "",
    care_target_condition: str = "",
    living_with_target: str = "",
    cocaregivers: str = "",
    care_content: str = "",
    care_hours_per_week: str = "",
    difficulties: str = "",
    economic_status: str = "",
    notes: str = "",
) -> str:
    """사전 상담 중 사용자에게서 얻은 정보를 즉시 레포트에 기록합니다.

    Args:
        care_target: 돌봄 대상자와의 관계 (예: 할머니, 어머니, 형, 누나).
        care_target_relation_legal: 민법상 가족 여부 — "민법상 가족(친족)" / "비가족(친족 아님)".
            친족 범위에 포함되는 부모·조부모 등은 "민법상 가족(친족)", 삼촌·고모·이모·자녀 등은 "비가족"으로 표기.
        care_target_condition: 돌봄 대상자 상태 요약 (질병·연령·장애 등).
        living_with_target: 함께 살고 있는지 — "동거" / "별거" 등.
        cocaregivers: 함께 돌보는 다른 가족 구성원 요약.
        care_content: 일상 돌봄 내용 (식사·이동·위생·약 챙김 등).
        care_hours_per_week: 주당 돌봄 시간 대략 (예: "약 30시간", "거의 매일").
        difficulties: 본인이 겪는 어려움 (학업·일·건강·정서).
        economic_status: 경제 상황 — 수급 자격(기초생활수급/차상위/일반), 의료비·생활비 부담 등.
        notes: 그 외 메모할 사항.
    """
    r = st.session_state.report
    for key, val in {
        "care_target": care_target,
        "care_target_relation_legal": care_target_relation_legal,
        "care_target_condition": care_target_condition,
        "living_with_target": living_with_target,
        "cocaregivers": cocaregivers,
        "care_content": care_content,
        "care_hours_per_week": care_hours_per_week,
        "difficulties": difficulties,
        "economic_status": economic_status,
        "notes": notes,
    }.items():
        if val:
            r[key] = val
    return "OK"


def set_stage(stage: str) -> str:
    """현재 진행 단계를 업데이트합니다.

    Args:
        stage: "첫 인사" | "관계 확인" | "가족 구성원 확인" | "돌봄 실태" |
               "경제 상황" | "라우팅 안내" | "마무리" 중 하나.
    """
    st.session_state.stage = stage
    return "OK"


def trigger_safety_alert(reason: str) -> str:
    """위기 신호(자해·학대·방임 등) 감지 시 즉시 호출합니다.

    Args:
        reason: 한 문장 요약.
    """
    st.session_state.safety_alert = reason
    return "OK"


def record_post_care(
    daily_change: str = "",
    receipt_amount: int = 0,
    receipt_item: str = "",
    residency_change: str = "",
) -> str:
    """사후 관리 — 월간 돌봄기록 항목 기록.

    Args:
        daily_change: 지난 달 ADL/IADL 변화 요약.
        receipt_amount: 영수증 금액(원).
        receipt_item: 영수증 항목 (예: 약값, 병원비, 요양병원비).
        residency_change: 이사·전출 계획 ("없음" / "다음 달 경기도 이사 예정" 등).
    """
    if daily_change:
        st.session_state.report["daily_change"] = daily_change
    if receipt_amount and receipt_item:
        st.session_state.post_receipts.append({"amount": receipt_amount, "item": receipt_item})
    if residency_change:
        st.session_state.post_residency_change = residency_change
        st.session_state.report["residency_change"] = residency_change
    return "OK"


# ======================================================================
# OpenAI tool 스키마 — 위 4개 함수의 JSON schema
# ======================================================================
UPDATE_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "update_report",
        "description": "사전 상담 중 사용자에게서 얻은 정보를 즉시 레포트에 기록합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "care_target": {"type": "string", "description": "돌봄 대상자와의 관계 (예: 할머니, 어머니, 형, 누나)."},
                "care_target_relation_legal": {"type": "string", "description": "민법상 가족 여부 — '민법상 가족(친족)' / '비가족(친족 아님)'. 부모·조부모 등은 친족, 삼촌·고모·이모·자녀 등은 비가족."},
                "care_target_condition": {"type": "string", "description": "돌봄 대상자 상태 요약 (질병·연령·장애 등)."},
                "living_with_target": {"type": "string", "description": "함께 살고 있는지 — '동거' / '별거' 등."},
                "cocaregivers": {"type": "string", "description": "함께 돌보는 다른 가족 구성원 요약."},
                "care_content": {"type": "string", "description": "일상 돌봄 내용 (식사·이동·위생·약 챙김 등)."},
                "care_hours_per_week": {"type": "string", "description": "주당 돌봄 시간 대략 (예: '약 30시간', '거의 매일')."},
                "difficulties": {"type": "string", "description": "본인이 겪는 어려움 (학업·일·건강·정서)."},
                "economic_status": {"type": "string", "description": "경제 상황 — 수급 자격(기초생활수급/차상위/일반), 의료비·생활비 부담 등."},
                "notes": {"type": "string", "description": "그 외 메모할 사항."},
            },
        },
    },
}

SET_STAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "set_stage",
        "description": "현재 진행 단계를 업데이트합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "description": "'첫 인사' | '관계 확인' | '가족 구성원 확인' | '돌봄 실태' | '경제 상황' | '라우팅 안내' | '마무리' 중 하나.",
                },
            },
            "required": ["stage"],
        },
    },
}

TRIGGER_SAFETY_ALERT_TOOL = {
    "type": "function",
    "function": {
        "name": "trigger_safety_alert",
        "description": "위기 신호(자해·학대·방임 등) 감지 시 즉시 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "한 문장 요약."}},
            "required": ["reason"],
        },
    },
}

RECORD_POST_CARE_TOOL = {
    "type": "function",
    "function": {
        "name": "record_post_care",
        "description": "사후 관리 — 월간 돌봄기록 항목 기록.",
        "parameters": {
            "type": "object",
            "properties": {
                "daily_change": {"type": "string", "description": "지난 달 ADL/IADL 변화 요약."},
                "receipt_amount": {"type": "integer", "description": "영수증 금액(원)."},
                "receipt_item": {"type": "string", "description": "영수증 항목 (예: 약값, 병원비, 요양병원비)."},
                "residency_change": {"type": "string", "description": "이사·전출 계획 ('없음' / '다음 달 경기도 이사 예정' 등)."},
            },
        },
    },
}

TOOL_DISPATCH = {
    "update_report": update_report,
    "set_stage": set_stage,
    "trigger_safety_alert": trigger_safety_alert,
    "record_post_care": record_post_care,
}

PRE_TOOLS = [UPDATE_REPORT_TOOL, SET_STAGE_TOOL, TRIGGER_SAFETY_ALERT_TOOL]
POST_TOOLS = [RECORD_POST_CARE_TOOL, SET_STAGE_TOOL, TRIGGER_SAFETY_ALERT_TOOL]


# ======================================================================
# 시스템 프롬프트
# ======================================================================
PRE_SYSTEM_TEMPLATE = """당신은 가족돌봄청소년·청년 원스톱 공공복지 AI 안내 서비스 ‘스프링(Spring)’의 사전 상담 AI 상담사입니다.

【슬로건】 “가족을 품은 온기로 피워낸 봄, 이제는 그대의 봄을 향해 도약하도록, Spring”
【기준】 서울시 거주(서울시복지재단 기준) — 만 9~39세, 법적 가족(민법상)을 돌보는 사람

【사용자 가입 정보】
- 이름: {name}
- 만 나이: {age}세
- 거주지: {region}
- 적용 라우트: {route_label}

【대화 원칙 — 매우 중요】
1. 친근하고 따뜻한 톤. 통화하듯 자연스럽게.
2. **한 번에 질문 하나만**. 묶어서 묻지 말 것.
3. **메타 설명/제목/번호/괄호 라벨 금지** (예: "1. 일상 돌봄 현황 파악(ADL/IADL)" 같이 하면 안 됨).
   사용자에게는 자연스러운 질문 한 문장만. 항목 분류는 도구로 기록.
4. 채팅 메시지이므로 **2~3문장 이내, 짧게**. 목록·숫자 나열·이모지 금지.
5. 9세 어린이도 이해할 쉬운 단어. 필요한 정서적 공감 한 마디 ("그러셨군요", "잘 견뎌오셨네요").
6. 도구만 호출하고 끝내지 말 것 — 항상 사용자에게 전달할 한국어 문장 포함.

【시작 인사 — 반드시 이 문장으로 첫 응답 시작】
{branch_intro}

【대화 흐름 — 한 번에 하나씩】
A. 동의 확인 (위 인사 직후 사용자가 "네"·"응"·"좋아요"라고 답하면 다음으로)
B. 대상자와의 관계 확인 (민법상 친족 여부)
   · 부모·조부모(외조부모) 등 → "민법상 가족(친족)" 으로 update_report 기록
   · 삼촌·고모·이모·본인의 자녀 등 → "비가족" 기록 후, 이번 사업 대상이 아님을 부드럽게 안내
   질문 예: "OO님이 돌봄을 맡고 계신 분은 누구신가요?"
C. 가족 구성원 확인
   질문 예: "OO님과 함께 살고 계신가요? 같이 돌보고 계신 분이 있다면 알려주세요."
D. 돌봄 내용 (식사·이동·위생·약 — 한 문장)
   질문 예: "OO님은 어떻게 도와드리고 있어요? 식사나 씻는 거, 약 챙기는 거처럼요."
E. 주당 돌봄 시간 (대략)
F. 본인의 어려움 (학업·일·건강·정서 중 하나로 부드럽게)
G. 경제 상황 — 수급 자격(기초생활수급/차상위/일반), 의료비·생활비 부담
   · 민감 항목이므로 "답하기 어려우시면 건너뛰셔도 돼요" 한마디 곁들이기.
H. 라우팅 안내
   · 위 적용 라우트({route_label})에 맞춰 다음 단계 안내
   · 포털 가능 → "준비되시면 서울복지포털에서 ‘가족돌봄정보 등록’부터 도와드릴게요"
   · 포털 불가(미성년/제대군인) → "법정대리인 또는 본인이 동주민센터/구청에 직접 방문하시면 됩니다. 사전 맥락 보고서를 만들어 두면 상담원이 즉시 상황을 파악할 수 있어요."
I. 마무리 — 사전 상담 레포트가 정리되었음을 알림.

【도구 사용】
- 정보(돌봄 대상자·관계·돌봄 내용·시간·어려움·경제 상황 등)를 들으면 **즉시** update_report 호출.
- 단계 진행 시 set_stage 호출 (첫 인사 → 관계 확인 → 가족 구성원 확인 → 돌봄 실태 → 경제 상황 → 라우팅 안내 → 마무리).
- 자해·자살·학대·폭력·방임·심한 우울 신호 감지 시 **즉시** trigger_safety_alert 호출 후, 답변에 1393(자살예방)·1577-1391(노인보호)·1366(여성긴급)·112 안내.

이름은 사용자 이름({name})으로 부르고, 위 인사 문장 안의 'OO님'은 실제 이름으로 자연스럽게 바꿔 말하세요.
"""


POST_SYSTEM_TEMPLATE = """당신은 ‘스프링(Spring)’의 사후 관리 AI 상담사입니다. 이미 가족돌봄청년 지원에 선정된 분께 매월 돌봄기록서 작성을 도와드립니다.

【사용자 정보】 이름: {name} / 만 {age}세 / 거주지: {region}

【대화 원칙】
1. 따뜻하고 짧게. 한 번에 한 가지만 묻기.
2. **메타 설명/제목/번호 절대 금지** (예: "1. 일상 돌봄 현황 파악(ADL/IADL)" → 안 됨).
3. 채팅 톤 — 2~3문장 이내, 이모지·목록·숫자 나열 금지.
4. 도구 호출 후 반드시 사용자에게 전달할 한 문장 포함.

【시작 인사 — 반드시 이 문장으로】
"안녕하세요, {name}님! 벌써 두 달이 지나 돌봄기록서를 제출할 시기가 다가왔어요. 기한(익월 10일) 내에 미제출이 2회 누적되면 지원금이 중지될 수 있으니 저랑 지금 바로 작성해 볼까요? 이번 달 돌봄 대상자분과의 일상은 어떠셨나요? 편하게 이야기해 주시면 제가 기록서 작성을 도와드릴게요!"

【대화 흐름】
1) 일상 돌봄 변화(ADL/IADL): "지난 두 달 동안 식사나 씻는 걸 도와드리는 데 변화가 있었나요?"
   → record_post_care(daily_change=…) 로 기록.
2) 경제적 지출(병원비·약값·요양병원비 등). 영수증을 사진으로 보내달라고 부드럽게 권유.
   → 사용자가 사진을 올렸다고 말하면, AI가 자동으로 텍스트를 추출했다고 가정하고 항목/금액을 record_post_care(receipt_amount=…, receipt_item=…) 로 기록. 고부담형(월 40만 원) 유지 기준 자동 체크.
3) 거주지 변동: "혹시 최근에 이사를 하셨거나 계획 중이신가요?"
   → 서울 외 전출 시 자격 상실 가능성을 부드럽게 안내. record_post_care(residency_change=…).
4) 마무리 — 수집한 내용으로 돌봄기록서 초안이 정리되었음을 알리고 검토 요청.
"""


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
            region = st.text_input(
                "거주지",
                placeholder="예) 서울특별시 강남구 / 인천광역시 미추홀구 등",
                help="시/도 단위 키워드(서울·인천·울산·충북·전북)로 라우팅이 결정됩니다.",
            )
            simple_auth = st.checkbox("간편인증(공동인증서·PASS 등) 사용 가능", value=True)

        submitted = st.form_submit_button("✨ 사전 상담 시작", type="primary", use_container_width=True)

    if submitted:
        if not name.strip() or not region.strip():
            st.error("이름과 거주지는 필수입니다.")
            return
        age = calc_age(birth)
        # 간편인증 불가도 Route A로 보냄 (스크립트의 ‘제대군인·간편인증 불가’ 분기 반영)
        is_no_simple_auth = not simple_auth
        route = determine_route(age, region.strip(), is_veteran=is_veteran or is_no_simple_auth)
        st.session_state.user = {
            "name": name.strip(),
            "birth": birth.isoformat(),
            "age": age,
            "region": region.strip(),
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
                _start_chat_session()
                st.rerun()
    with col2:
        with st.container(border=True):
            st.subheader("🍃 사후 관리 (월간 돌봄기록서)")
            st.caption("이미 지원에 선정된 분이 매월 돌봄기록서를 작성합니다.")
            st.markdown(
                "- 지난 달 돌봄 변화 (ADL/IADL)\n"
                "- 경제적 지출 (영수증 업로드)\n"
                "- 거주지·자격 유지 확인"
            )
            if st.button("사후 관리 시작 →", key="mode_post", use_container_width=True):
                st.session_state.mode = "post"
                st.session_state.phase = "chat"
                st.session_state.stage = "월간 기록"
                _start_chat_session()
                st.rerun()

    st.divider()
    if st.button("← 회원 정보 다시 입력"):
        st.session_state.phase = "signup"
        st.rerun()


# ======================================================================
# 3. 챗봇 세션 시작
# ======================================================================
def _format_chat_error(e: Exception, prefix: str = "오류") -> str:
    """OpenAI 호출 실패를 한국어 메시지로 변환. 429/quota는 안내 분리."""
    raw = str(e)
    status = getattr(e, "status_code", None) or getattr(e, "code", None)
    is_quota = (
        status == 429
        or "429" in raw
        or "rate_limit" in raw.lower()
        or "quota" in raw.lower()
        or "insufficient_quota" in raw.lower()
    )
    if is_quota:
        return (
            f"({prefix}: OpenAI API 사용량 한도를 초과했어요 — HTTP 429.\n"
            "잠시 뒤 다시 시도하거나, https://platform.openai.com/usage 에서 사용량/결제 상태를 확인해주세요.)"
        )
    if status == 401 or "invalid_api_key" in raw.lower():
        return f"({prefix}: OpenAI API 키가 유효하지 않습니다. .env의 OPENAI_API_KEY 확인.)"
    return f"({prefix}: {raw})"


def _run_chat_turn(user_content) -> str:
    """history에 user 메시지 추가 → tool-call 루프 실행 → assistant 텍스트 반환.

    user_content: OpenAI Chat Completions의 user content (문자열 또는 멀티모달 list).
    히스토리는 st.session_state.history 에 누적된다 (system 포함).
    """
    history = st.session_state.history
    tools = st.session_state.tools
    history.append({"role": "user", "content": user_content})

    for _ in range(6):  # tool-call 최대 6회까지 허용
        resp = st.session_state.client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=history,
            tools=tools,
            temperature=0.7,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            history.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                fn = TOOL_DISPATCH.get(tc.function.name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = fn(**args) if fn else f"unknown tool: {tc.function.name}"
                history.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
            continue
        history.append({"role": "assistant", "content": msg.content or ""})
        return (msg.content or "").strip()

    return "(상담사가 도구 호출을 너무 많이 시도했어요. 다시 말씀해주세요.)"


def _start_chat_session() -> None:
    u = st.session_state.user
    if st.session_state.mode == "pre":
        sys = PRE_SYSTEM_TEMPLATE.format(
            name=u["name"],
            age=u["age"],
            region=u["region"],
            route_label=u["route_label"],
            branch_intro=u["branch_message"],
        )
        tools = PRE_TOOLS
        kickoff = "[시스템] 사용자가 입장했습니다. 위에 적힌 ‘시작 인사’ 문장으로 첫 응답을 시작해 주세요."
    else:
        sys = POST_SYSTEM_TEMPLATE.format(name=u["name"], age=u["age"], region=u["region"])
        tools = POST_TOOLS
        kickoff = "[시스템] 사용자가 입장했습니다. 위에 적힌 시작 인사 문장으로 대화를 열어주세요."

    st.session_state.messages = []
    st.session_state.report = {}
    st.session_state.safety_alert = None
    st.session_state.tools = tools
    st.session_state.history = [{"role": "system", "content": sys}]

    try:
        first_text = _run_chat_turn(kickoff) or "안녕하세요. 스프링 AI 상담사예요."
    except Exception as e:
        first_text = _format_chat_error(e, prefix="시작 오류")
    st.session_state.messages.append({"role": "assistant", "content": first_text})


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
            if st.session_state.mode == "pre":
                stages = ["첫 인사", "관계 확인", "가족 구성원 확인", "돌봄 실태", "경제 상황", "라우팅 안내", "마무리"]
            else:
                stages = ["월간 기록", "지출 확인", "거주지 확인", "마무리"]
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

        # 사후관리 모드: 영수증 업로드 옵션
        uploaded_receipt = None
        if not is_pre:
            with st.expander("🧾 영수증 사진 업로드 (선택)"):
                uploaded_receipt = st.file_uploader(
                    "병원비·약값 영수증 사진",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=f"receipt_{len(st.session_state.messages)}",
                )

        text_input = st.chat_input("메시지를 입력하세요…")

        receipt_bytes, receipt_mime, receipt_name = (None, None, None)
        if uploaded_receipt is not None:
            receipt_bytes = uploaded_receipt.read()
            receipt_mime = uploaded_receipt.type or "image/jpeg"
            receipt_name = uploaded_receipt.name

        if text_input or receipt_bytes:
            display = text_input or ""
            if receipt_name:
                display = (display + f"\n📎 {receipt_name} (영수증)").strip()
            st.session_state.messages.append({"role": "user", "content": display})

            if receipt_bytes:
                b64 = base64.b64encode(receipt_bytes).decode("ascii")
                content_parts: list = []
                if text_input:
                    content_parts.append({"type": "text", "text": text_input})
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
                user_content = text_input or ""

            with st.spinner("스프링이 듣고 있어요…"):
                try:
                    assistant_text = _run_chat_turn(user_content) or "(상담사가 응답하지 않았습니다. 다시 말씀해주세요.)"
                except Exception as e:
                    assistant_text = _format_chat_error(e)

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
