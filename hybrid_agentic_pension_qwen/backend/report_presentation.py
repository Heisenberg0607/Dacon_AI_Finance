"""Keep internal evidence metadata out of user-facing report prose."""

import re


REPORT_PROSE_RULES = (
    '사용자에게 보여주는 모든 설명 문장에는 E1, E7 등의 근거 ID를 괄호, 대괄호 또는 본문에 쓰지 마라. '
    '근거 ID는 citations 또는 evidence_ids 배열에만 기록하라. '
    'JSON 내부 필드명, 경로, 상태값(예: risk_level_verified: false)을 설명에 노출하지 마라. '
    '검증 여부는 자연스러운 한국어로 설명하라. 예: 포트폴리오 자체의 위험등급은 문서에서 확인되지 않았습니다. '
    '상품 비중, 만기, 위험 설명 등 사용자에게 필요한 일반 괄호 내용은 유지하라. '
)

_FIELDS = {
    'summary', 'diagnosis', 'actions', 'product_analysis', 'disclaimer',
    'title', 'executive_summary', 'current_status', 'strategy',
    'simulation_comment', 'risk_notes',
}
_CITATION = r'E\d{1,3}'
_CITATION_GROUP = re.compile(
    rf'[（(\[]\s*(?:(?:근거|출처)\s*:?\s*)?{_CITATION}'
    rf'(?:\s*[,;·/]\s*{_CITATION})*\s*[）)\]]'
)
_FLAG = re.compile(
    r'`?[\w.]*[A-Za-z]\w*_(?:\w+_)*\w+\s*[=:]\s*(?:false|true|null)\b`?',
    re.IGNORECASE,
)


def clean_report_text(text: str) -> str:
    text = _CITATION_GROUP.sub('', text)
    # Markdown may escape underscores in JSON field names.
    text = _FLAG.sub('', text.replace(r'\_', '_'))
    text = re.sub(r'[（(\[]\s*[）)\]]', '', text)
    text = re.sub(r'(?<![A-Za-z0-9])E\d{1,3}(?![A-Za-z0-9])', '', text)
    text = re.sub(r'[ \t]+([,.;:!?])', r'\1', text)
    return re.sub(r'[ \t]{2,}', ' ', text).strip()


def clean_report_prose(payload: dict) -> dict:
    """Clean only display fields; preserve citations and other structured data."""
    result = dict(payload)
    for key in _FIELDS & result.keys():
        value = result[key]
        if isinstance(value, str):
            result[key] = clean_report_text(value)
        elif isinstance(value, list):
            result[key] = [clean_report_text(x) if isinstance(x, str) else x for x in value]
    return result
