"""Recommendation Agent / Critic Agent / Report Q&A Agent가 공유하는 결정론적 검사.

LLM이 무엇을 출력하든 이 검사는 Python이 항상 수행한다. Critic Agent(agents.py)와
챗봇(chat_agent.py)이 같은 규칙을 쓰도록 한 곳에 모아둔다.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .formatting import contains_converted_money_unit

GUARANTEE_PHRASES = ['수익 보장', '확실히 달성', '무조건 달성']

MONEY_UNIT_ISSUE = '금액을 억/만원 형태로 축약하지 말고 amount_display의 실제 원화 숫자를 그대로 표시해야 함'
GUARANTEE_ISSUE = '수익 또는 목표달성을 보장하는 표현 금지'

# 한글 조사가 바로 뒤에 붙는 경우(E1을, E2에)에도 잡아야 한다.
# 한글은 word character라서 를 쓰면 "E7을"이 매칭되지 않는다.
EVIDENCE_ID_RE = re.compile('(?<![0-9A-Za-z가-힣])E([0-9]{1,3})(?![0-9])')


def guarantee_phrase_issues(text: str) -> list[str]:
    return [GUARANTEE_ISSUE] if any(word in (text or '') for word in GUARANTEE_PHRASES) else []


def money_unit_issues(payload: Any) -> list[str]:
    return [MONEY_UNIT_ISSUE] if contains_converted_money_unit(payload) else []


def invalid_citation_issues(citations: Iterable[str] | None, valid_evidence_ids: Iterable[str]) -> list[str]:
    valid = set(valid_evidence_ids or [])
    return [f'존재하지 않는 근거 ID 인용: {c}' for c in (citations or []) if c not in valid]


def cited_evidence_ids(text: str) -> list[str]:
    """자유 문장 안에서 언급된 E1, E2 ... 형태의 근거 ID를 순서대로 추출한다."""
    seen: list[str] = []
    for m in EVIDENCE_ID_RE.finditer(text or ''):
        eid = f'E{int(m.group(1))}'
        if eid not in seen:
            seen.append(eid)
    return seen


def answer_issues(answer: str, valid_evidence_ids: Iterable[str]) -> list[str]:
    """챗봇 답변 한 건에 대한 공통 검사 묶음."""
    issues = money_unit_issues(answer)
    issues += guarantee_phrase_issues(answer)
    issues += invalid_citation_issues(cited_evidence_ids(answer), valid_evidence_ids)
    return list(dict.fromkeys(issues))
