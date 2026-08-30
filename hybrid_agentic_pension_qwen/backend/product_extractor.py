from __future__ import annotations

import json
import re
from typing import Any

from .qwen_client import QwenGateway

ALLOWED_ASSET_CLASSES = {
    '원리금보장/예금', '현금성', '국내채권', '해외채권', '채권형',
    '국내주식', '해외주식', '주식형', 'TDF', 'BF', '혼합형', '기타'
}


def _num(v: Any) -> float | None:
    if v is None or v == '':
        return None
    try:
        return float(v)
    except Exception:
        return None




def _risk_level_support(raw_value: Any, document: dict[str, Any]) -> tuple[str | None, list[int]]:
    """Accept a product risk level only when the PDF text supports it unambiguously.

    OCR/text extraction often flattens risk tables (all grades + a circle marker), so a
    grade appearing somewhere on the page is not enough. We only accept the value when
    it appears in a local context containing a product-risk cue and no competing grade.
    """
    if raw_value is None:
        return None, []
    value = str(raw_value).strip()
    m = re.search(r'([1-6])\s*등급(?:\s*\(([^)]*)\))?', value)
    if not m:
        return None, []
    grade = m.group(1)
    supported_pages: list[int] = []
    cue_re = re.compile(r'(?:^|\n)[ \t]*(?:본[ \t]*상품(?:의)?[ \t]*(?:위험도|위험등급)|해당[ \t]*상품(?:의)?[ \t]*(?:위험도|위험등급)|상품[ \t]*(?:위험도|위험등급))[ \t]*[:：]?', re.M)
    grade_re = re.compile(r'([1-6])\s*등급')

    for page in document.get('pages') or []:
        text = str(page.get('text') or '')
        for hit in re.finditer(rf'{grade}\s*등급(?:\s*\([^)]*\))?', text):
            start = max(0, hit.start() - 180)
            end = min(len(text), hit.end() + 180)
            ctx = text[start:end]
            grades = set(grade_re.findall(ctx))
            # Reject flattened tables containing multiple grade options around the hit.
            if grades != {grade}:
                continue
            if not cue_re.search(ctx):
                continue
            try:
                page_no = int(page.get('page'))
            except Exception:
                page_no = None
            if page_no and page_no > 0:
                supported_pages.append(page_no)

    if not supported_pages:
        return None, []
    return value, sorted(set(supported_pages))

def _normalize_asset_class(value: str | None, name: str = '') -> str:
    raw = (value or '').strip()
    if raw in ALLOWED_ASSET_CLASSES:
        return raw
    text = f'{raw} {name}'.lower()
    if any(k in text for k in ['정기예금', '예금', '이율보증', '원리금보장', 'gic']):
        return '원리금보장/예금'
    if any(k in text for k in ['mmf', '현금', '단기금융']):
        return '현금성'
    if 'tdf' in text or 'target date' in text or '타깃데이트' in text:
        return 'TDF'
    if re.search(r'\bbf\b', text) or '밸런스' in text or 'balanced' in text:
        return 'BF'
    if any(k in text for k in ['해외주식', '글로벌주식', '미국주식', 'world equity', 'global equity']):
        return '해외주식'
    if any(k in text for k in ['국내주식', '코스피', 'korea equity']):
        return '국내주식'
    if any(k in text for k in ['주식', 'equity']):
        return '주식형'
    if any(k in text for k in ['해외채권', '글로벌채권', 'global bond']):
        return '해외채권'
    if any(k in text for k in ['국내채권', '국공채', 'korea bond']):
        return '국내채권'
    if any(k in text for k in ['채권', 'bond']):
        return '채권형'
    if any(k in text for k in ['혼합', '멀티에셋', 'multi asset']):
        return '혼합형'
    return '기타'


def _validate_extraction(raw: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    meta = document.get('product', {})
    allocations = []
    for i, item in enumerate(raw.get('asset_allocation') or []):
        if not isinstance(item, dict):
            continue
        weight = _num(item.get('weight_pct'))
        if weight is None or weight <= 0 or weight > 100:
            continue
        name = str(item.get('component_name') or f'구성상품 {i+1}').strip()
        pages = []
        for p in item.get('evidence_pages') or []:
            try:
                p = int(p)
                if p > 0:
                    pages.append(p)
            except Exception:
                pass
        allocations.append({
            'component_name': name,
            'weight_pct': round(weight, 4),
            'asset_class': _normalize_asset_class(item.get('asset_class'), name),
            'principal_guaranteed': bool(item.get('principal_guaranteed')) if item.get('principal_guaranteed') is not None else None,
            'stated_rate_pct': _num(item.get('stated_rate_pct')),
            'evidence_pages': sorted(set(pages)),
        })

    weight_sum = sum(x['weight_pct'] for x in allocations)
    if allocations and 98 <= weight_sum <= 102 and abs(weight_sum - 100) > 1e-8:
        for x in allocations:
            x['weight_pct'] = round(x['weight_pct'] / weight_sum * 100, 4)
        weight_sum = 100.0

    risk_level_document, risk_level_evidence_pages = _risk_level_support(raw.get('risk_level_document'), document)

    pg_ratio = _num(raw.get('principal_guaranteed_ratio_pct'))
    if pg_ratio is None and allocations:
        pg_ratio = sum(
            x['weight_pct'] for x in allocations
            if x['principal_guaranteed'] is True or x['asset_class'] == '원리금보장/예금'
        )

    calc_ready = bool(allocations) and 98 <= weight_sum <= 102
    missing = list(raw.get('missing_for_projection') or [])
    if not allocations:
        missing.append('포트폴리오 구성비중')
    if _num(raw.get('document_expected_return_pct')) is None:
        missing.append('미래 기대수익률')
    if _num(raw.get('document_volatility_pct')) is None:
        missing.append('미래 변동성')

    return {
        'source': raw.get('source') or 'qwen_pdf_extraction',
        'source_file_id': meta.get('file_id'),
        'source_filename': meta.get('filename'),
        'source_pages': meta.get('pages'),
        'provider': meta.get('provider'),
        'product_name': raw.get('product_name') or meta.get('title'),
        'risk_type': raw.get('risk_type') or meta.get('risk_type'),
        'subtype': raw.get('subtype') or meta.get('subtype'),
        'risk_level_document': risk_level_document,
        'risk_level_verified': bool(risk_level_document),
        'risk_level_evidence_pages': risk_level_evidence_pages,
        'principal_guaranteed': raw.get('principal_guaranteed'),
        'principal_guaranteed_ratio_pct': round(pg_ratio, 4) if pg_ratio is not None else None,
        'portfolio_fee_pct': _num(raw.get('portfolio_fee_pct')),
        'document_expected_return_pct': _num(raw.get('document_expected_return_pct')),
        'document_volatility_pct': _num(raw.get('document_volatility_pct')),
        'strategy': raw.get('strategy') or '',
        'asset_allocation': allocations,
        'allocation_weight_sum_pct': round(weight_sum, 4),
        'calculation_ready': calc_ready,
        'missing_for_projection': list(dict.fromkeys(str(x) for x in missing if x)),
        'extraction_notes': list(raw.get('extraction_notes') or []) + ([
            'risk_level_document는 PDF 텍스트에서 해당 상품의 단일 위험등급으로 명확히 검증되지 않아 null 처리했습니다.'
        ] if raw.get('risk_level_document') and not risk_level_document else []),
    }


def _fallback_extract(document: dict[str, Any]) -> dict[str, Any]:
    """API 장애/데모용 최소 추출기.

    핵심 계산에서는 Qwen 추출이 우선이며, fallback은 문서에 아주 명시적인
    `정기예금 100%` 또는 비중 행이 있을 때만 사용한다.
    """
    text = document.get('text', '')
    meta = document.get('product', {})
    raw: dict[str, Any] = {
        'source': 'deterministic_pdf_fallback',
        'product_name': meta.get('title'),
        'risk_type': meta.get('risk_type'),
        'subtype': meta.get('subtype'),
        'asset_allocation': [],
        'extraction_notes': ['Qwen을 사용할 수 없어 PDF 텍스트의 명시적 수치만 최소 추출했습니다.'],
    }

    # 명확한 100% 단일 자산 표현
    if re.search(r'정기예금\s*(?:에\s*)?100\s*%', text):
        raw['asset_allocation'] = [{
            'component_name': '정기예금', 'weight_pct': 100,
            'asset_class': '원리금보장/예금', 'principal_guaranteed': True,
            'stated_rate_pct': None, 'evidence_pages': [1, 2],
        }]
        raw['principal_guaranteed'] = True
        raw['principal_guaranteed_ratio_pct'] = 100
        raw['strategy'] = '정기예금 100% 투자'

    # 표 텍스트에 비중(%) 뒤로 숫자들이 연속 등장하는 경우
    if not raw['asset_allocation']:
        m = re.search(r'비중\(%\)\s*((?:\d+(?:\.\d+)?\s+){1,8})', text)
        if m:
            weights = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', m.group(1))]
            if 98 <= sum(weights) <= 102:
                guaranteed_table = '상품유형' in text and text.count('원리금보장상품') >= len(weights)
                raw['asset_allocation'] = [
                    {
                        'component_name': f'구성상품 {i+1}',
                        'weight_pct': w,
                        'asset_class': '원리금보장/예금' if guaranteed_table else '기타',
                        'principal_guaranteed': True if guaranteed_table else None,
                        'stated_rate_pct': None,
                        'evidence_pages': [2],
                    }
                    for i, w in enumerate(weights)
                ]
                if guaranteed_table:
                    raw['principal_guaranteed_ratio_pct'] = 100

    return _validate_extraction(raw, document)


class ProductExtractionAgent:
    def __init__(self, qwen: QwenGateway):
        self.qwen = qwen

    def extract(self, document: dict[str, Any]) -> dict[str, Any]:
        if not document.get('product'):
            return {
                'source': 'not_found', 'calculation_ready': False,
                'asset_allocation': [], 'missing_for_projection': ['선택 상품 PDF'],
                'extraction_notes': ['선택한 상품과 정확히 일치하는 PDF를 찾지 못했습니다.'],
            }
        if not self.qwen.enabled:
            return _fallback_extract(document)

        meta = document['product']
        system = (
            '너는 퇴직연금 상품설명서 전용 Product Extraction Agent다. '
            '주어진 단 하나의 공식 PDF에서 계산에 필요한 사실만 구조화한다. '
            '문서에 없는 숫자는 절대로 추측하지 말고 null로 둔다. '
            'risk_level_document는 해당 상품 자체의 위험등급이 문장 또는 독립된 필드로 명확히 적힌 경우에만 추출한다. '
            '여러 등급이 나열된 위험등급 표, 위치가 깨진 ○ 표시, 상품명/투자유형을 근거로 위험등급을 추론하지 말고 그런 경우 null로 둔다. '
            '일반 위험등급 표의 임계값을 이 상품의 실제 변동성/수익률로 오인하지 마라. '
            '포트폴리오 구성상품과 비중은 표에 적힌 값을 정확히 추출하고, 비중 합계는 원문 기준이어야 한다. '
            'asset_class는 구성상품명/상품유형을 근거로 원리금보장/예금, 현금성, 국내채권, 해외채권, 채권형, 국내주식, 해외주식, 주식형, TDF, BF, 혼합형, 기타 중 하나로 분류한다. '
            'evidence_pages에는 해당 수치를 확인한 PDF 페이지 번호를 넣는다. JSON만 출력한다. '
            '스키마: {'
            '"product_name":str,"risk_type":str|null,"subtype":str|null,"risk_level_document":str|null,'
            '"principal_guaranteed":bool|null,"principal_guaranteed_ratio_pct":number|null,'
            '"portfolio_fee_pct":number|null,"document_expected_return_pct":number|null,"document_volatility_pct":number|null,'
            '"strategy":str,'
            '"asset_allocation":[{"component_name":str,"weight_pct":number,"asset_class":str,"principal_guaranteed":bool|null,"stated_rate_pct":number|null,"evidence_pages":[int]}],'
            '"missing_for_projection":[str],"extraction_notes":[str]}'
        )
        payload = {
            'selected_product': meta,
            'instruction': '아래 페이지별 PDF 텍스트만 사용해 상품 정보를 추출하라.',
            'pages': document.get('pages', []),
        }
        try:
            resp = self.qwen.chat([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ], temperature=0.0)
            parsed = self.qwen.parse_json(resp.choices[0].message.content or '', {})
            if parsed:
                parsed['source'] = 'qwen_pdf_extraction'
                validated = _validate_extraction(parsed, document)
                if validated['asset_allocation']:
                    return validated
        except Exception as e:
            fallback = _fallback_extract(document)
            fallback['extraction_notes'].append(f'Qwen 상품정보 추출 실패로 최소 fallback 사용: {type(e).__name__}')
            return fallback

        fallback = _fallback_extract(document)
        fallback['extraction_notes'].append('Qwen 응답에서 유효한 구성비중을 확인하지 못해 최소 fallback을 사용했습니다.')
        return fallback
