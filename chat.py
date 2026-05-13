"""OpenAI Chat Completions 호출 루프 + 세션 시작 + 에러 처리.

매 턴마다 `_run_chat_turn()`이 st.session_state.history에 user 메시지를 append하고,
모델 응답에 tool_calls가 있으면 TOOL_DISPATCH로 실제 함수를 실행한 뒤 결과를
`role: "tool"` 메시지로 history에 넣고 다시 호출한다 (최대 MAX_TOOL_LOOPS 라운드).

설계 노트:
- 영수증 이미지는 base64 data URL로 user 메시지에 한 번 들어가고, 다음 턴 진입 시
  `_strip_images_in_place()`가 placeholder 텍스트로 치환해 토큰 비용을 막는다.
- 호출 실패(429, network 등)는 user 메시지를 rollback해서 다음 재시도에서 중복 누적을 피한다.
"""
from __future__ import annotations

import json

import streamlit as st

from prompts import POST_SYSTEM_TEMPLATE, PRE_SYSTEM_TEMPLATE
from routing import routing_guidance_for
from tools import TOOL_DISPATCH, build_post_tools, build_pre_tools

OPENAI_MODEL = "gpt-4o-mini"
MAX_TOOL_LOOPS = 6  # 한 턴에 허용되는 모델 ↔ 도구 라운드트립 상한


def format_chat_error(e: Exception, prefix: str = "오류") -> str:
    """OpenAI 호출 실패를 한국어 메시지로 변환. 429/quota·401은 안내 분리."""
    raw = str(e)
    status = getattr(e, "status_code", None)  # int — HTTP status
    code = getattr(e, "code", None)            # str — OpenAI error code (e.g. "insufficient_quota")
    raw_l = raw.lower()
    is_quota = (
        status == 429
        or code in ("insufficient_quota", "rate_limit_exceeded")
        or "rate_limit" in raw_l
        or "quota" in raw_l
    )
    if is_quota:
        return (
            f"({prefix}: OpenAI API 사용량 한도를 초과했어요 — HTTP 429.\n"
            "잠시 뒤 다시 시도하거나, https://platform.openai.com/usage 에서 사용량/결제 상태를 확인해주세요.)"
        )
    if status == 401 or code == "invalid_api_key" or "invalid_api_key" in raw_l:
        return f"({prefix}: OpenAI API 키가 유효하지 않습니다. .env의 OPENAI_API_KEY 확인.)"
    return f"({prefix}: {raw})"


def _strip_images_in_place(messages: list[dict]) -> None:
    """이미 한 턴 끝난 user 메시지의 image_url 파트를 placeholder 텍스트로 치환.

    Why: 영수증 base64 이미지가 history에 영구 잔류하면 매 턴마다 다시 전송되어
    토큰 비용이 폭주한다. 모델은 이미 사진을 읽어 record_post_care로 기록했으므로,
    이후 턴에서는 사진 원본을 가지고 있을 필요가 없다.
    """
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        m["content"] = [
            ({"type": "text", "text": "[영수증 이미지 — 이미 처리 완료]"} if p.get("type") == "image_url" else p)
            for p in content
        ]


def _dispatch_tool(tc) -> str:
    """tool_call 1건을 실행해 결과 문자열을 반환. 모든 예외를 잡아서 모델이 회복할 수 있게 함."""
    name = tc.function.name
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return f"ERROR: unknown tool '{name}'"
    try:
        args = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError as e:
        return f"ERROR: invalid JSON in arguments — {e}. 인자를 다시 확인하고 도구를 재호출하세요."
    if not isinstance(args, dict):
        return "ERROR: arguments must be a JSON object."
    try:
        return str(fn(**args))
    except TypeError as e:
        return f"ERROR: 인자 타입/이름이 맞지 않음 — {e}. 스키마를 다시 확인하고 재호출하세요."
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


def run_chat_turn(user_content) -> str:
    """history에 user 메시지 추가 → tool-call 루프 실행 → assistant 텍스트 반환.

    user_content: OpenAI Chat Completions의 user content (문자열 또는 멀티모달 list).
    히스토리는 st.session_state.history 에 누적된다 (system 포함).
    실패 시 user 메시지를 rollback해서 중복 누적을 막는다.
    """
    history = st.session_state.history
    tools = st.session_state.tools
    # 이번 턴 user 메시지를 넣기 전, 이전 턴의 영수증 이미지는 placeholder로 치환.
    _strip_images_in_place(history)
    rollback_idx = len(history)
    history.append({"role": "user", "content": user_content})

    try:
        for _ in range(MAX_TOOL_LOOPS):
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
                    history.append({"role": "tool", "tool_call_id": tc.id, "content": _dispatch_tool(tc)})
                continue
            history.append({"role": "assistant", "content": msg.content or ""})
            return (msg.content or "").strip()
    except Exception:
        del history[rollback_idx:]
        raise

    return "(상담사가 도구 호출을 너무 많이 시도했어요. 다시 말씀해주세요.)"


def start_chat_session() -> None:
    """모드(pre/post)에 맞춰 시스템 프롬프트·도구·히스토리를 초기화하고 kickoff 메시지로 첫 발화를 강제."""
    u = st.session_state.user
    if st.session_state.mode == "pre":
        sys = PRE_SYSTEM_TEMPLATE.format(
            name=u["name"],
            age=u["age"],
            region=u["region"],
            route_label=u["route_label"],
            branch_intro=u["branch_message"],
            routing_guidance=routing_guidance_for(u),
        )
        tools = build_pre_tools()
        kickoff = "[시스템] 사용자가 입장했습니다. 위에 적힌 ‘시작 인사’ 문장으로 첫 응답을 시작해 주세요."
    else:
        sys = POST_SYSTEM_TEMPLATE.format(name=u["name"], age=u["age"], region=u["region"])
        tools = build_post_tools()
        kickoff = "[시스템] 사용자가 입장했습니다. 위에 적힌 시작 인사 문장으로 대화를 열어주세요."

    st.session_state.messages = []
    st.session_state.report = {}
    st.session_state.safety_alert = None
    st.session_state.tools = tools
    st.session_state.history = [{"role": "system", "content": sys}]

    try:
        first_text = run_chat_turn(kickoff) or "안녕하세요. 스프링 AI 상담사예요."
    except Exception as e:
        first_text = format_chat_error(e, prefix="시작 오류")
    st.session_state.messages.append({"role": "assistant", "content": first_text})
