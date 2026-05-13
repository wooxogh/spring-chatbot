"""라우트 판별 — 가입 정보(나이·거주지·간편인증) 기반 분기.

가족돌봄청년 사업의 흐름을 image.png 스크립트 기준으로 구현:
  - Route A (오프라인 방문): 9~13세 / 39세 초과·제대군인 / 간편인증 불가(force_route_a)
  - Route B (포털 온라인): 14~39세 + 서울 또는 청년ON 관할 지역
  - 범위 외: 9세 미만, 또는 39+5(제대군인 시) 초과 → 다른 자원 안내
"""
from __future__ import annotations

from datetime import date

SEOUL_KEYWORDS = ("서울",)
YOUTH_ON_KEYWORDS = ("인천", "울산", "충북", "전북", "충청북도", "전라북도")


def calc_age(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def determine_route(
    age: int,
    region: str,
    is_veteran: bool = False,
    force_route_a: bool = False,
) -> dict:
    """이미지 스크립트 기준 분기.

    Args:
        age: 만 나이.
        region: 거주지 문자열 (시/도 키워드로 매칭).
        is_veteran: 제대군인 여부 (연령 상한 +5세).
        force_route_a: 간편인증 불가 등 사유로 포털 사용이 불가능한 경우 강제로 오프라인 라우트로.

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

    if is_veteran or age > 39 or force_route_a:
        why = "39세 초과·제대군인" if (is_veteran or age > 39) else "간편인증 사용이 어려운"
        return {
            "route": "ROUTE_A_OFFLINE_VETERAN",
            "route_label": "Route A — 포털 사용 불가, 구청 직접 방문 신청",
            "can_portal": False,
            "branch_message": (
                f"안녕하세요, OO님! 가입 정보를 보니 {why} 상황에 해당하시네요. "
                "이 경우 인터넷 포털로 직접 신청이 어렵고, 직접 ‘구청’에 가서 신청하셔야 해요. "
                "방문 전에 OO님이 도움을 받으실 수 있는 상황인지 몇 가지만 여쭤볼게요. "
                "편안하게 말씀해 주세요."
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


def routing_guidance_for(user: dict) -> str:
    """라우트별로 H. 라우팅 안내 단계에서 모델이 말할 문장 한 줄.

    PRE_SYSTEM_TEMPLATE의 {routing_guidance} 자리에 들어간다. 라우트 분기 추가 시
    여기 한 군데만 손보면 시스템 프롬프트는 그대로 둘 수 있다.
    """
    route = user.get("route")
    if user.get("can_portal"):
        if route == "ROUTE_B_YOUTH_ON":
            return "‘준비되시면 청년ON 포털에서 가족돌봄청년 지원 신청을 도와드릴게요’ 라고 안내한다."
        return "‘준비되시면 서울복지포털에서 가족돌봄정보 등록부터 도와드릴게요’ 라고 안내한다."
    if route == "ROUTE_A_OFFLINE_MINOR":
        return "‘법정대리인(부모·보호자)과 함께 동주민센터에 직접 방문해서 신청하시면 됩니다. 지금 정리한 사전 맥락 보고서를 보여드리면 상담원이 바로 상황을 파악할 수 있어요’ 라고 안내한다."
    if route == "ROUTE_A_OFFLINE_VETERAN":
        return "‘직접 구청에 방문해서 신청하시면 됩니다. 지금 정리한 사전 맥락 보고서를 보여드리면 상담원이 바로 상황을 파악할 수 있어요’ 라고 안내한다."
    return "‘이번 데모는 서울·인천·울산·충북·전북 외 지역은 직접 안내 범위 밖이라, 거주지 동주민센터에서 가족돌봄청년 사업을 문의해 보시도록 권유하고, 지금까지의 상담 내용을 정리한 사전 맥락 보고서를 챙겨가시도록 안내한다."
