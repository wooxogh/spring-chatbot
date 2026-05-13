"""모델이 호출하는 사이드 이펙트 도구.

OpenAI Chat Completions의 function-calling 패턴:
- 4개 Python 함수가 실제 동작 — 모두 st.session_state를 직접 수정.
- 각 함수에 대응하는 JSON schema(`*_TOOL`) — 모델에게 "이런 도구가 있다"고 알려준다.
- `TOOL_DISPATCH`: 모델이 부른 함수 이름 → Python callable 매핑.
- `build_pre_tools()` / `build_post_tools()`: 모드별 도구 목록 (stage enum이 다르므로 빌더로).

도구 시그니처를 바꿀 때는 (1) 함수 시그니처, (2) JSON 스키마, (3) 시스템 프롬프트(prompts.py)의
사용 설명 세 군데를 함께 갱신해야 한다.
"""
from __future__ import annotations

import streamlit as st

# ----- 도구 함수 (사이드 이펙트) ----------------------------------------------------


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
    """사전 상담 중 사용자에게서 얻은 정보를 즉시 레포트에 기록합니다."""
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
    """현재 진행 단계를 업데이트합니다."""
    st.session_state.stage = stage
    return "OK"


def trigger_safety_alert(reason: str) -> str:
    """위기 신호(자해·학대·방임 등) 감지 시 즉시 호출합니다."""
    st.session_state.safety_alert = reason
    return "OK"


def record_post_care(
    daily_change: str = "",
    receipt_amount: int = 0,
    receipt_item: str = "",
    residency_change: str = "",
) -> str:
    """사후 관리 — 월간 돌봄기록 항목 기록."""
    if daily_change:
        st.session_state.report["daily_change"] = daily_change
    if receipt_amount and receipt_item:
        st.session_state.post_receipts.append({"amount": receipt_amount, "item": receipt_item})
    if residency_change:
        st.session_state.post_residency_change = residency_change
        st.session_state.report["residency_change"] = residency_change
    return "OK"


# ----- stage 라벨 + JSON 스키마 ------------------------------------------------------

PRE_STAGES = ["첫 인사", "관계 확인", "가족 구성원 확인", "돌봄 실태", "경제 상황", "라우팅 안내", "마무리"]
POST_STAGES = ["월간 기록", "지출 확인", "거주지 확인", "마무리"]


UPDATE_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "update_report",
        "description": "사전 상담 중 사용자에게서 얻은 정보를 즉시 레포트에 기록합니다.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "care_target": {"type": "string", "description": "돌봄 대상자와의 관계 (예: 할머니, 어머니, 형, 누나)."},
                "care_target_relation_legal": {
                    "type": "string",
                    "enum": ["민법상 가족(친족)", "비가족(친족 아님)"],
                    "description": "민법상 가족 여부. 부모·조부모 등은 친족, 삼촌·고모·이모·자녀 등은 비가족.",
                },
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


TRIGGER_SAFETY_ALERT_TOOL = {
    "type": "function",
    "function": {
        "name": "trigger_safety_alert",
        "description": "위기 신호(자해·학대·방임 등) 감지 시 즉시 호출합니다.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
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
            "additionalProperties": False,
            "properties": {
                "daily_change": {"type": "string", "description": "지난 달 ADL/IADL 변화 요약."},
                "receipt_amount": {
                    "type": "integer",
                    "description": "영수증 금액(원). 영수증 사진을 직접 읽어 숫자만 입력.",
                    "minimum": 0,
                },
                "receipt_item": {"type": "string", "description": "영수증 항목 (예: 약값, 병원비, 요양병원비)."},
                "residency_change": {"type": "string", "description": "이사·전출 계획 ('없음' / '다음 달 경기도 이사 예정' 등)."},
            },
        },
    },
}


def _set_stage_tool_for(stages: list[str]) -> dict:
    """모드별 stage enum이 다르므로, 호출 시점에 enum을 채워 스키마를 생성한다.

    Why: 사전상담·사후관리 stage 라벨이 서로 다른데, enum을 합쳐 주면 모델이 다른 모드의
    라벨을 호출하는 사고가 잦다. 모드별로 enum을 좁혀 그 사고를 차단한다.
    """
    return {
        "type": "function",
        "function": {
            "name": "set_stage",
            "description": "현재 진행 단계를 업데이트합니다. 아래 enum 중 하나만 사용할 것.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stage": {"type": "string", "enum": stages, "description": "현재 단계 라벨."},
                },
                "required": ["stage"],
            },
        },
    }


TOOL_DISPATCH = {
    "update_report": update_report,
    "set_stage": set_stage,
    "trigger_safety_alert": trigger_safety_alert,
    "record_post_care": record_post_care,
}


def build_pre_tools() -> list[dict]:
    return [UPDATE_REPORT_TOOL, _set_stage_tool_for(PRE_STAGES), TRIGGER_SAFETY_ALERT_TOOL]


def build_post_tools() -> list[dict]:
    return [RECORD_POST_CARE_TOOL, _set_stage_tool_for(POST_STAGES), TRIGGER_SAFETY_ALERT_TOOL]
