from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from .formatting import (
    contains_converted_money_unit as _contains_converted_money_unit,
    raw_amount as _raw_amount,
    won_amount as _won_amount,
)
from .guardrails import guarantee_phrase_issues, invalid_citation_issues, money_unit_issues
from .models import UserPensionInput
from .product_extractor import ProductExtractionAgent
from .qwen_client import QwenGateway
from .rag import PensionRAG
from .tools import finance_engine_tool, monte_carlo_tool, portfolio_optimizer_tool, profile_tool


# 코드가 _deterministic_critic_checks에서 실제로 수행하는 검증 항목의 이름표.
#
# 왜 상수로 빼는가: 이전에는 이 목록이 demo 분기 안에만 하드코딩돼 있었다. 그래서
#   (1) Qwen 모드에서는 LLM이 checks를 비워 보내면 화면의 'AI 전략 검증' 칸이 통째로 비었고,
#   (2) DB형에도 '상품 PDF 매칭' 같은, 하지도 않은 검사가 적혔다.
# 목록을 검사 로직 옆에 두고 운영유형으로 갈라서 두 문제를 함께 막는다.
#
# 여기 적힌 항목은 모두 코드가 실제로 돌리는 검사다. 통과 여부와 무관하게 '무엇을 봤는지'를
# 밝히는 용도이고, 실패한 항목은 issues로 따로 나간다.
CRITIC_CHECKS_DC_IRP = (
    '선택 상품 PDF 정확 매칭',
    'PDF 구성비중 구조화 가능 여부',
    '금융계산이 선택 상품 PDF 기준인지',
    '문서 위험등급 근거 검증',
    '포트폴리오 최적화 적용 여부',
    '추천 자산배분 합계 100%',
    '최적화 자산배분 방향 일관성',
    '수익 보장 표현 검증',
    '금액 단위 표기 검증',
    'RAG 근거 확보 및 ID 유효성',
)
CRITIC_CHECKS_DB = (
    'DB형을 개인 적립금·상품 구조로 표현했는지',
    '개인 포트폴리오 최적화 미적용 여부',
    '수익 보장 표현 검증',
    '금액 단위 표기 검증',
    'RAG 근거 ID 유효성',
)


def critic_check_labels(operation_type: str) -> list[str]:
    """이번 분석에서 코드가 실제로 돌린 결정론적 검증 항목."""
    return list(CRITIC_CHECKS_DB if operation_type == 'DB' else CRITIC_CHECKS_DC_IRP)


TOOL_DEFINITIONS = [
    {
        'type': 'function',
        'function': {
            'name': 'analyze_profile',
            'description': '운영유형을 판별하고 사용자 프로필을 구조화한다.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_product_rag',
            'description': 'DC/IRP이면 사용자가 선택한 정확한 상품 PDF 내부에서만 근거를 검색한다. DB이면 비적용 여부를 반환한다.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'top_k': {'type': 'integer', 'minimum': 3, 'maximum': 8, 'default': 6},
                },
                'required': ['query'],
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'extract_selected_product_pdf',
            'description': 'DC/IRP 사용자가 선택한 상품명을 catalog에서 정확히 매칭한 뒤 해당 공식 PDF 전체 텍스트를 Qwen이 읽고 구성상품·비중·수수료·위험정보를 구조화한다. 금융계산 전에 반드시 수행한다.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'run_finance_engine',
            'description': 'DB는 임금·근속으로 급여를 계산하고, DC/IRP는 Product Extraction Agent가 PDF에서 추출한 실제 상품구성 비중을 Python 계산 입력으로 사용한다.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'run_monte_carlo',
            'description': 'DB는 임금경로, DC/IRP는 선택 상품 PDF에서 추출한 구성비중으로 산출한 상품별 수익률·변동성 입력을 이용해 시뮬레이션한다.',
            'parameters': {
                'type': 'object',
                'properties': {'simulations': {'type': 'integer', 'minimum': 500, 'maximum': 5000, 'default': 2500}},
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'optimize_retirement_strategy',
            'description': '현재 상품의 기준선은 PDF 추출값으로 계산하고, 별도 후보 자산배분과 납입액을 비교한다.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
]


class TimedTrace(list):
    """단계별 소요시간을 자동으로 기록하는 trace 리스트.

    - append(entry): 직전 mark 이후 경과시간을 entry['elapsed_seconds']에 기록한다.
      (작업을 먼저 하고 결과를 append 하는 단계용)
    - stamp_last(): 이미 append된 마지막 entry의 소요시간을 지금 시점 기준으로 다시 기록한다.
      ('running' 상태로 먼저 append 하고 작업 후 상태를 갱신하는 단계용)
    시간은 Python이 측정하며 LLM이 만들어내지 않는다.

    on_event를 주면 단계가 시작·완료될 때마다 그 entry를 그대로 통보한다.
    /api/analyze/stream이 이 통보를 SSE로 흘려보내 화면의 단계 표시를 실제 진행에 맞춘다.
    통보는 부수효과일 뿐이라 trace의 내용과 소요시간 측정에는 영향을 주지 않는다.
    on_event가 던진 예외가 분석을 중단시키지 않도록 삼킨다. 진행 표시 실패가 보고서
    생성을 망치면 안 된다.
    """

    def __init__(self, on_event: Any = None) -> None:
        super().__init__()
        self.started_at = datetime.now()
        self._start = time.perf_counter()
        self._mark = self._start
        self.on_event = on_event

    def _notify(self, entry: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            self.on_event(dict(entry))
        except Exception:
            pass

    def begin(self, stage: str, tool: str | None = None, selected_by: str | None = None) -> None:
        """단계를 시작했다는 신호만 보낸다. trace에는 남기지 않는다.

        _fallback_run_tools처럼 '작업을 끝내고 결과를 append'하는 단계는 완료 시점에만
        기록이 남는다. 그러면 화면은 이미 끝난 단계만 알게 되어 '지금 무엇을 하는 중인지'를
        보여줄 수 없다. 시작 신호를 따로 보내되, 소요시간 측정 기준(mark)은 건드리지 않는다.
        """
        self._notify({'stage': stage, 'tool': tool, 'selected_by': selected_by, 'status': 'running'})

    def append(self, entry: dict[str, Any]) -> None:  # type: ignore[override]
        now = time.perf_counter()
        entry['elapsed_seconds'] = round(now - self._mark, 2)
        self._mark = now
        super().append(entry)
        self._notify(entry)

    def stamp_last(self) -> None:
        now = time.perf_counter()
        if self:
            self[-1]['elapsed_seconds'] = round(now - self._mark, 2)
        self._mark = now
        if self:
            self._notify(self[-1])

    @property
    def total_seconds(self) -> float:
        return round(time.perf_counter() - self._start, 2)


class HybridAgenticWorkflow:
    def __init__(self, qwen: QwenGateway, rag: PensionRAG):
        self.qwen = qwen
        self.rag = rag
        self.product_extractor = ProductExtractionAgent(qwen)

    def _rag_search(self, user: UserPensionInput, query: str, top_k: int = 6):
        if user.operation_type == 'DB':
            return {
                'mode': 'DB: 개인 상품 RAG 비적용',
                'query': query,
                'results': [],
                'note': 'DB형은 근로자가 개인 운용상품을 선택하는 구조가 아니므로 현재 업로드된 디폴트옵션 상품 PDF 검색을 개인 분석에 적용하지 않습니다.',
            }
        return self.rag.search(
            query=query,
            provider=user.provider,
            product_name=user.product_name,
            risk_type=user.investment_type,
            top_k=top_k,
        )

    def _extract_selected_product(self, user: UserPensionInput) -> dict[str, Any]:
        if user.operation_type == 'DB':
            return {
                'source': 'not_applicable',
                'calculation_ready': False,
                'asset_allocation': [],
                'missing_for_projection': [],
                'extraction_notes': ['DB형에는 개인 선택 상품 PDF 추출을 적용하지 않습니다.'],
            }
        document = self.rag.exact_product_document(user.provider, user.product_name)
        extracted = self.product_extractor.extract(document)
        extracted['matched_exact_product_pdf'] = bool(document.get('matched_exactly'))
        extracted['document_chunk_count'] = document.get('chunk_count', 0)
        return extracted

    def _execute_tool(self, name: str, args: dict[str, Any], user: UserPensionInput, context: dict[str, Any]):
        if name == 'analyze_profile':
            return profile_tool(user)
        if name == 'search_product_rag':
            default_query = (
                'DB형 개인 상품 RAG 적용 여부 확인'
                if user.operation_type == 'DB'
                else f'{user.product_name} 포트폴리오 구성상품 비중 상품유형 보수 위험도 적용이율 유의사항'
            )
            return self._rag_search(user, args.get('query') or default_query, int(args.get('top_k', 6)))
        if name == 'extract_selected_product_pdf':
            return self._extract_selected_product(user)
        if name == 'run_finance_engine':
            if user.operation_type != 'DB' and 'product_extraction' not in context:
                raise RuntimeError('DC/IRP 금융계산 전에 선택 상품 PDF 구조화 추출이 필요합니다.')
            return finance_engine_tool(user, context.get('product_extraction'))
        if name == 'run_monte_carlo':
            if user.operation_type != 'DB' and 'product_extraction' not in context:
                raise RuntimeError('DC/IRP Monte Carlo 전에 선택 상품 PDF 구조화 추출이 필요합니다.')
            return monte_carlo_tool(user, context.get('product_extraction'), simulations=int(args.get('simulations', 2500)))
        if name == 'optimize_retirement_strategy':
            if user.operation_type != 'DB' and 'product_extraction' not in context:
                raise RuntimeError('DC/IRP 최적화 전에 선택 상품 PDF 구조화 추출이 필요합니다.')
            return portfolio_optimizer_tool(user, context.get('product_extraction'))
        raise ValueError(f'Unknown tool: {name}')

    def _fallback_run_tools(self, user: UserPensionInput, context: dict, trace: list):
        query = (
            'DB형 개인 상품 RAG 적용 여부 확인'
            if user.operation_type == 'DB'
            else f'{user.product_name} 포트폴리오 구성상품 비중 상품유형 보수 위험도 적용이율 유의사항'
        )
        sequence = [('analyze_profile', {})]
        if user.operation_type != 'DB':
            sequence += [
                ('search_product_rag', {'query': query, 'top_k': 8}),
                ('extract_selected_product_pdf', {}),
            ]
        else:
            sequence += [('search_product_rag', {'query': query, 'top_k': 3})]
        sequence += [
            ('run_finance_engine', {}),
            ('run_monte_carlo', {'simulations': 2500}),
            ('optimize_retirement_strategy', {}),
        ]
        for name, args in sequence:
            key = self._context_key(name)
            if key in context:
                continue
            # 이 단계는 작업을 끝낸 뒤에야 trace에 남는다. 화면이 '지금 무엇을 하는 중인지'를
            # 보여주려면 시작 시점에도 신호가 필요하다.
            trace.begin(key, name, 'fallback-orchestrator')
            result = self._execute_tool(name, args, user, context)
            context[key] = result
            trace.append({'stage': key, 'tool': name, 'status': 'done', 'selected_by': 'fallback-orchestrator'})

    @staticmethod
    def _context_key(tool_name: str) -> str:
        return {
            'analyze_profile': 'profile',
            'search_product_rag': 'rag',
            'extract_selected_product_pdf': 'product_extraction',
            'run_finance_engine': 'finance',
            'run_monte_carlo': 'monte_carlo',
            'optimize_retirement_strategy': 'optimizer',
        }[tool_name]

    def _run_qwen_planner(self, user: UserPensionInput, context: dict, trace: list):
        system = (
            '너는 한국 퇴직연금 의사결정 지원 서비스 깨움의 Pension AI Agent다. '
            '운영유형을 먼저 확인한다. DB는 임금·근속기간 중심으로 분석한다. '
            'DC/IRP는 반드시 1) 정확한 가입상품 PDF 근거검색, 2) extract_selected_product_pdf로 공식 PDF의 상품구성/비중 구조화, '
            '3) 그 추출결과를 사용하는 금융계산, 4) Monte Carlo, 5) 최적화 순서로 핵심 결과를 확보한다. '
            '상품 PDF의 내용이나 숫자를 직접 만들어내지 말고 도구를 호출한다. 숫자 계산도 직접 하지 않는다. '
            '상품명만 비슷한 다른 PDF를 섞지 말고 선택된 정확한 PDF를 기준으로 분석한다.'
        )
        messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': '다음 사용자에 대해 필요한 tool을 호출해 분석하라.\n' + json.dumps(user.model_dump(), ensure_ascii=False)},
        ]

        for _ in range(12):
            response = self.qwen.chat(messages, tools=TOOL_DEFINITIONS, tool_choice='auto', temperature=0.1)
            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                break
            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or '{}')
                except Exception:
                    args = {}
                try:
                    trace.begin(self._context_key(name), name, 'Qwen function calling')
                    result = self._execute_tool(name, args, user, context)
                    key = self._context_key(name)
                    if key == 'rag' and key in context and user.operation_type != 'DB':
                        old = context[key].get('results', [])
                        new = result.get('results', [])
                        seen = {(x.get('filename'), x.get('page'), x.get('snippet')) for x in old}
                        merged = old[:]
                        for x in new:
                            k = (x.get('filename'), x.get('page'), x.get('snippet'))
                            if k not in seen:
                                merged.append(x)
                                seen.add(k)
                        context[key] = {**result, 'results': merged[:10]}
                    else:
                        context[key] = result
                    trace.append({'stage': key, 'tool': name, 'status': 'done', 'selected_by': 'Qwen function calling', 'args': args})
                    tool_payload = result
                except Exception as e:
                    trace.append({'stage': name, 'tool': name, 'status': 'error', 'selected_by': 'Qwen function calling', 'error': str(e)})
                    tool_payload = {'error': str(e)}
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': json.dumps(tool_payload, ensure_ascii=False)})
        return messages

    def _fallback_recommendation(self, user: UserPensionInput, context: dict) -> dict:
        finance = context['finance']
        opt = context['optimizer']
        rag_results = context['rag'].get('results', [])
        citations = [x['evidence_id'] for x in rag_results[:4]]

        if user.operation_type == 'DB':
            wage_rate = finance.get('wage_growth_rate_pct', 0)
            gap = max(0, finance.get('gap', 0))
            supplement = opt.get('supplementary_monthly_saving_needed', 0)
            summary = (
                f"DB형은 현재 연소득과 근속기간을 기준으로 은퇴 시 예상 DB 퇴직급여 계산값을 {_won_amount(finance['estimated_db_benefit'])}로 추정했습니다. "
                f"적용 임금상승률은 연 {wage_rate:.2f}%이며 예상 총 근속기간은 {finance['total_expected_tenure_years']:.1f}년입니다."
            )
            actions = [
                f"현재 근속 {finance['current_tenure_years']:.1f}년 + 예상 추가 근속 {finance['additional_tenure_years']}년 기준으로 DB 급여를 정기 재추정",
                f"희망 월소득 기준 목표자산과의 Gap: {_won_amount(gap)}",
                (f"Gap 보완을 위한 별도 장기저축 단순 추정값은 월 {_won_amount(supplement)}입니다." if supplement > 0 else '현재 간이 추정 기준으로 별도 자산 Gap이 크지 않습니다.'),
            ]
            product_analysis = 'DB형은 개인 선택 상품 PDF 분석을 적용하지 않습니다.'
            reason_codes = ['R_DB_WAGE', 'R_DB_TENURE', 'R_DB_GAP']
        else:
            ext = context.get('product_extraction', {})
            allocation_text = ', '.join(f"{x['component_name']} {x['weight_pct']:.0f}%" for x in ext.get('asset_allocation', [])[:6]) or '구성비중 추출 실패'
            basis = finance.get('calculation_basis')
            summary = (
                f"선택한 공식 상품 PDF를 직접 식별해 구성비중을 구조화한 뒤 Python 금융엔진으로 계산했습니다. "
                f"현재 상품 기준 목표달성률은 {finance['goal_rate_pct']}%입니다."
            )
            actions = [
                f"현재 상품 PDF 구성: {allocation_text}",
                f"AI 후보 자산배분: {opt['recommended_allocation']}",
                f"기대값 기준 목표달성에 필요한 연간 납입액 추정: {_won_amount(opt['required_annual_contribution_for_expected_target'])}",
            ]
            product_analysis = (
                f"'{user.product_name}' → {ext.get('source_filename') or 'PDF'}를 정확 매칭했습니다. "
                f"계산 기준은 {basis}이며, PDF에 미래 기대수익률/변동성이 없으면 상품구성은 PDF 그대로 두고 자산군 CMA만 보완합니다."
            )
            reason_codes = ['R_PDF_EXACT_MATCH', 'R_PDF_STRUCTURED_EXTRACTION', 'R_PYTHON_CALCULATION']

        return {
            'summary': summary,
            'diagnosis': [
                f"은퇴까지 {user.years_to_retirement}년 남음",
                f"희망 월소득을 4% 인출률 가정으로 환산한 목표자산 계산값은 {_won_amount(finance['target_retirement_asset'])}",
                ('DB는 임금·근속 기반 급여분석 적용' if user.operation_type == 'DB' else 'DC/IRP는 선택 상품 PDF 기반 분석 적용'),
            ],
            'actions': actions,
            'product_analysis': product_analysis,
            'reason_codes': reason_codes,
            'citations': citations,
            'disclaimer': '본 결과는 의사결정 지원 시뮬레이션입니다. PDF에 없는 미래 수익률·변동성은 별도 자산군 가정으로 보완되며 실제 미래수익을 보장하지 않습니다.',
        }

    def _recommendation_agent(self, user: UserPensionInput, context: dict, critique: str | None = None) -> dict:
        if not self.qwen.enabled:
            return self._fallback_recommendation(user, context)
        payload = {
            'user': user.model_dump(),
            'profile': context['profile'],
            'selected_product_pdf_extraction': context.get('product_extraction'),
            'finance_engine': context['finance'],
            'monte_carlo': context['monte_carlo'],
            'optimizer_or_gap_analyzer': context['optimizer'],
            'amount_display': {
                'annual_income': _won_amount(user.annual_income),
                'desired_monthly_income': _won_amount(user.desired_monthly_income),
                'current_savings': _won_amount(user.current_savings),
                'annual_contribution': _won_amount(user.annual_contribution),
                'target_retirement_asset': _won_amount(context['finance'].get('target_retirement_asset')),
                'future_asset': _won_amount(context['finance'].get('future_asset')),
                'gap': _won_amount(context['finance'].get('gap')),
                'required_annual_contribution': _won_amount(context['optimizer'].get('required_annual_contribution_for_expected_target')),
                'additional_annual_contribution': _won_amount(context['optimizer'].get('additional_annual_contribution_needed')),
            },
            'evidence': context['rag'].get('results', [])[:10],
            'previous_critique': critique,
        }
        system = (
            '너는 깨움의 Recommendation Agent다. 계산값은 제공된 Python 도구 JSON만 사용하고 임의 숫자를 만들지 마라. '
            'DC/IRP는 selected_product_pdf_extraction을 실제 가입상품의 공식 PDF 구조화 결과로 취급하라. '
            '상품구성·비중은 이 추출값과 evidence에 근거해서만 설명하라. 미래 기대수익률/변동성이 PDF에 없어서 CMA로 보완된 경우 이를 명시하라. '
            'optimizer_or_gap_analyzer의 allocation_direction, recommended_allocation, optimization_objective를 그대로 해석하라. '
            '안전자산 비중이 증가했는데 성장형 비중을 확대했다고 말하는 등 자산배분 방향을 반대로 설명하지 마라. '
            'selected_product_pdf_extraction.risk_level_verified가 false이면 해당 포트폴리오 자체의 위험등급을 단정하지 마라. 구성상품별 위험등급과 포트폴리오 위험등급을 구분하라. '
            '추천 비중은 확정 처방이 아니라 현재 입력과 prototype CMA 아래의 후보 최적해라고 표현하라. '
            'DB는 임금·근속·급여 Gap 중심으로 설명한다. 수익 보장 표현을 금지한다. '
            '모든 금액은 amount_display의 문자열을 그대로 복사해 사용한다. amount_display는 내부 만원 값을 실제 원화 숫자(×10,000)로 변환한 값이다. 억/만원 형태로 다시 축약하거나 환산하지 말고 통화 단위도 붙이지 마라. 예: 내부값 5,000은 amount_display에서 50,000,000이므로 반드시 50,000,000으로 쓴다. JSON만 출력한다. '
            '스키마: {"summary":str,"diagnosis":[str],"actions":[str],"product_analysis":str,"reason_codes":[str],"citations":["E1"],"disclaimer":str}'
        )
        resp = self.qwen.chat([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ], temperature=0.2)
        parsed = self.qwen.parse_json(resp.choices[0].message.content or '', {})
        return parsed if parsed.get('summary') else self._fallback_recommendation(user, context)

    def _deterministic_critic_checks(self, user: UserPensionInput, recommendation: dict, context: dict) -> list[str]:
        issues = []
        text = json.dumps(recommendation, ensure_ascii=False)
        if user.operation_type == 'DB':
            if re.search(r'(현재 적립금|연간 납입액|개인.{0,10}(상품|비중).{0,20}(변경|교체))', text):
                issues.append('DB형을 개인 적립금/상품 운용 구조처럼 표현한 문장을 제거해야 함')
            if (context.get('optimizer') or {}).get('portfolio_optimization_applicable') is not False:
                issues.append('DB형인데 개인 포트폴리오 최적화 적용 여부가 false가 아님')
        else:
            ext = context.get('product_extraction') or {}
            if not ext.get('matched_exact_product_pdf'):
                issues.append('사용자가 선택한 상품과 정확히 일치하는 PDF를 식별하지 못함')
            if not ext.get('calculation_ready'):
                issues.append('선택 상품 PDF에서 포트폴리오 구성비중을 계산 가능한 형태로 추출하지 못함')
            if context.get('finance', {}).get('calculation_basis') != 'selected_product_pdf':
                issues.append('현재 상품 계산이 selected_product_pdf 기준이 아니고 fallback을 사용함')
            if ext.get('risk_level_document') is not None and not ext.get('risk_level_verified'):
                issues.append('PDF에서 명확히 검증되지 않은 상품 위험등급이 결과에 포함됨')

            opt = context.get('optimizer') or {}
            if opt.get('portfolio_optimization_applicable') is not True:
                issues.append('DC/IRP인데 포트폴리오 최적화 적용 여부가 true가 아님')
            allocation = opt.get('recommended_allocation') or {}
            if allocation:
                alloc_sum = sum(float(v) for v in allocation.values())
                if abs(alloc_sum - 100.0) > 0.2:
                    issues.append(f'추천 자산배분 합계가 100%가 아님: {alloc_sum:.2f}%')
            current_safe = opt.get('current_product_safe_ratio_proxy')
            recommended_safe = opt.get('recommended_safe_ratio')
            direction = opt.get('allocation_direction')
            if current_safe is not None and recommended_safe is not None:
                expected_direction = (
                    '안전자산 비중 확대' if recommended_safe > current_safe + 0.05
                    else '안전자산 비중 축소' if recommended_safe < current_safe - 0.05
                    else '안전자산 비중 유지'
                )
                if direction != expected_direction:
                    issues.append(f'최적화 자산배분 방향 불일치: 계산상 {expected_direction}, 출력 {direction}')
                if expected_direction == '안전자산 비중 확대' and re.search(r'(성장형|위험자산).{0,18}(확대|증가|늘리|높이)', text):
                    issues.append('추천 설명이 계산과 반대임: 안전자산 비중이 증가했는데 성장형/위험자산 확대라고 표현함')
                if expected_direction == '안전자산 비중 축소' and re.search(r'(안전자산|원리금보장).{0,18}(확대|증가|늘리|높이)', text):
                    issues.append('추천 설명이 계산과 반대임: 안전자산 비중이 감소했는데 안전자산 확대라고 표현함')
        issues += guarantee_phrase_issues(text)
        issues += money_unit_issues(recommendation)
        valid_e = {x['evidence_id'] for x in context['rag'].get('results', [])}
        issues += invalid_citation_issues(recommendation.get('citations'), valid_e)
        if user.operation_type != 'DB' and not context['rag'].get('results'):
            issues.append('DC/IRP 상품 분석에 필요한 선택 상품 PDF RAG 근거가 없음')
        return issues

    def _critic_agent(self, user: UserPensionInput, context: dict, recommendation: dict) -> dict:
        deterministic_issues = self._deterministic_critic_checks(user, recommendation, context)
        base_checks = critic_check_labels(user.operation_type)
        if not self.qwen.enabled:
            return {
                'passed': not deterministic_issues,
                'issues': deterministic_issues,
                'checks': base_checks,
                'revision_instructions': '; '.join(deterministic_issues),
            }
        payload = {
            'user': user.model_dump(),
            'product_extraction': context.get('product_extraction'),
            'recommendation': recommendation,
            'finance': context['finance'],
            'optimizer': context['optimizer'],
            'evidence': context['rag'].get('results', [])[:10],
            'hard_issues': deterministic_issues,
        }
        system = (
            '너는 Critic Agent다. 금융 안전성, 사용자 적합성, 정확한 PDF 매칭, 상품구성 추출, Python 숫자 일치성, RAG 근거성을 검증한다. '
            'DC/IRP에서 hard_issues가 하나라도 있으면 passed=false다. PDF에 없는 수익률이나 위험등급을 PDF 기재값처럼 표현하면 안 된다. '
            'optimizer의 현재/추천 안전자산 비중, allocation_direction, recommended_allocation을 대조해 추천 문장이 계산 방향과 모순되는지 확인한다. '
            '추천 자산배분 합계는 100%여야 하며, 추천치는 확정적 처방이 아니라 사용한 가정 아래 후보값으로 설명되어야 한다. '
            '수익 보장 표현을 허용하지 마라. JSON만 출력한다. '
            '스키마: {"passed":bool,"issues":[str],"checks":[str],"revision_instructions":str}'
        )
        resp = self.qwen.chat([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ], temperature=0.1)
        parsed = self.qwen.parse_json(resp.choices[0].message.content or '', {})
        if not parsed:
            parsed = {'passed': not deterministic_issues, 'issues': deterministic_issues, 'revision_instructions': '; '.join(deterministic_issues)}
        # LLM이 checks를 비워 보내거나 아예 빼먹으면 화면의 검증 칸이 통째로 빈다. 그런데
        # 코드가 돌린 결정론적 검사는 LLM 응답과 무관하게 실제로 실행됐으므로, 그 목록을
        # 항상 앞에 두고 LLM이 추가한 항목만 뒤에 이어 붙인다. 중복은 순서를 지키며 제거한다.
        llm_checks = [str(x) for x in (parsed.get('checks') or []) if str(x).strip()]
        parsed['checks'] = list(dict.fromkeys(base_checks + llm_checks))
        if deterministic_issues:
            parsed['passed'] = False
            parsed['issues'] = list(dict.fromkeys((parsed.get('issues') or []) + deterministic_issues))
            parsed['revision_instructions'] = '; '.join(parsed['issues'])
        return parsed

    def _fallback_report(self, user: UserPensionInput, context: dict, recommendation: dict, critic: dict) -> dict:
        f = context['finance']
        mc = context['monte_carlo']
        o = context['optimizer']
        if user.operation_type == 'DB':
            current_status = [
                f"현재 근속연수 {user.current_tenure_years:.1f}년 / 예상 추가 근속 {user.expected_additional_tenure_years}년",
                f"적용 임금상승률 {f['wage_growth_rate_pct']:.2f}% / 은퇴시 예상 연소득 {_won_amount(f['estimated_retirement_annual_income'])}",
                f"예상 DB 퇴직급여 {_won_amount(f['estimated_db_benefit'])} / 목표자산 {_won_amount(f['target_retirement_asset'])}",
            ]
            simulation_comment = f"임금상승률 불확실성을 반영한 몬테카를로에서 목표자산 이상 비율은 {mc['success_probability_pct']}%입니다."
            risk_notes = [
                f.get('calculation_note', ''),
                '임금상승률 benchmark는 prototype 추정치이며 공식 통계 예측값이 아닙니다.',
                '희망 월소득의 목표자산 환산에는 4% 인출률을 사용했습니다.',
                recommendation.get('disclaimer', ''),
            ]
        else:
            ext = context.get('product_extraction', {})
            current_status = [
                f"현재 적립금 {_won_amount(user.current_savings)} / 연간 납입액 {_won_amount(user.annual_contribution)}",
                f"선택 PDF: {ext.get('source_filename') or '-'}",
                f"예상 은퇴자산 {_won_amount(f['future_asset'])} / 목표자산 {_won_amount(f['target_retirement_asset'])}",
                f"현재 상품 몬테카를로 목표달성확률 {mc['success_probability_pct']}%",
            ]
            simulation_comment = (
                f"현재 가입상품은 선택한 PDF에서 추출한 구성비중으로 계산했습니다. "
                f"AI 후보 전략의 기대값 목표달성률은 {o['goal_rate_pct']}%, 몬테카를로 성공확률은 {o['success_probability_pct']}%입니다."
            )
            risk_notes = [
                f.get('calculation_note', ''),
                '상품구성 비중은 공식 PDF에서 추출합니다. PDF에 미래 기대수익률·변동성이 없으면 자산군별 CMA 가정으로 보완합니다.',
                'CMA 수익률·변동성, 4% 인출률은 미래를 보장하는 값이 아닙니다.',
                '세금, 수수료, 국민연금·개인연금 등 기타 현금흐름은 현재 입력에 포함되지 않을 수 있습니다.',
                recommendation.get('disclaimer', ''),
            ]
        return {
            'title': '깨움 AI 퇴직연금 분석 보고서',
            'executive_summary': recommendation.get('summary', ''),
            'current_status': current_status,
            'product_analysis': recommendation.get('product_analysis', ''),
            'strategy': recommendation.get('actions', []),
            'simulation_comment': simulation_comment,
            'risk_notes': [x for x in risk_notes if x],
            'evidence_ids': recommendation.get('citations', []),
            'critic_status': 'PASS' if critic.get('passed') else 'REVISED',
        }

    def _report_agent(self, user: UserPensionInput, context: dict, recommendation: dict, critic: dict) -> dict:
        if not self.qwen.enabled:
            return self._fallback_report(user, context, recommendation, critic)
        payload = {
            'user': user.model_dump(),
            'context': context,
            'recommendation': recommendation,
            'critic': critic,
            'amount_display': {
                'annual_income': _won_amount(user.annual_income),
                'desired_monthly_income': _won_amount(user.desired_monthly_income),
                'current_savings': _won_amount(user.current_savings),
                'annual_contribution': _won_amount(user.annual_contribution),
                'target_retirement_asset': _won_amount(context['finance'].get('target_retirement_asset')),
                'future_asset': _won_amount(context['finance'].get('future_asset')),
                'gap': _won_amount(context['finance'].get('gap')),
                'required_annual_contribution': _won_amount(context['optimizer'].get('required_annual_contribution_for_expected_target')),
                'additional_annual_contribution': _won_amount(context['optimizer'].get('additional_annual_contribution_needed')),
            },
        }
        system = (
            '너는 깨움의 퇴직연금 보고서 생성 Agent다. 제공된 구조화 데이터만 사용해 한국어 보고서를 JSON으로 만든다. '
            'DC/IRP에서는 선택한 공식 PDF에서 추출된 상품구성 → Python 계산 흐름을 명시하고, PDF에 없는 미래값을 CMA로 보완했다면 숨기지 않는다. '
            'product_extraction.risk_level_verified가 false이면 해당 포트폴리오 자체의 위험등급을 단정하지 않는다. '
            'optimizer의 allocation_direction과 추천비중을 서로 모순되게 설명하지 않는다. 추천치는 사용한 가정 아래 후보값임을 분명히 한다. '
            '수익 또는 실제 퇴직급여를 보장하지 않는다. '
            '모든 금액은 amount_display에 들어있는 실제 원화 숫자 문자열을 그대로 사용한다. 내부 계산값은 만원 스케일이지만 amount_display에서 이미 ×10,000 변환되었다. 억/만원 형태로 재계산·축약하지 말고 통화 단위도 붙이지 마라. 예: 내부값 75,000은 amount_display에서 750,000,000이므로 반드시 750,000,000으로 쓴다. JSON만 출력한다. '
            '스키마: {"title":str,"executive_summary":str,"current_status":[str],"product_analysis":str,"strategy":[str],"simulation_comment":str,"risk_notes":[str],"evidence_ids":[str],"critic_status":str}'
        )
        resp = self.qwen.chat([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ], temperature=0.2)
        parsed = self.qwen.parse_json(resp.choices[0].message.content or '', {})
        if parsed.get('title') and not _contains_converted_money_unit(parsed):
            return parsed
        return self._fallback_report(user, context, recommendation, critic)

    @staticmethod
    def _stage_durations(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """trace를 단계 단위로 합산해 보고서에 보여줄 소요시간 breakdown을 만든다."""
        totals: dict[str, dict[str, Any]] = {}
        for entry in trace:
            stage = entry.get('stage') or 'unknown'
            slot = totals.setdefault(stage, {'stage': stage, 'tool': entry.get('tool'), 'seconds': 0.0, 'runs': 0})
            slot['seconds'] += float(entry.get('elapsed_seconds') or 0.0)
            slot['runs'] += 1
        for slot in totals.values():
            slot['seconds'] = round(slot['seconds'], 2)
        return list(totals.values())

    def run(self, user: UserPensionInput, on_event: Any = None) -> dict[str, Any]:
        """on_event를 주면 단계가 시작·완료될 때마다 그 entry를 통보한다.

        통보는 부수효과일 뿐이고 반환값은 on_event 유무와 무관하게 동일하다.
        /api/analyze(기존 blocking)와 /api/analyze/stream(SSE)이 같은 코드를 쓴다.
        """
        trace = TimedTrace(on_event=on_event)
        context: dict[str, Any] = {}
        planner_mode = 'Qwen function calling' if self.qwen.enabled else 'demo fallback orchestrator'

        if self.qwen.enabled:
            try:
                self._run_qwen_planner(user, context, trace)
            except Exception as e:
                trace.append({'stage': 'planner', 'tool': 'Pension AI Agent', 'status': 'error', 'selected_by': 'Qwen', 'error': str(e)})

        # Agent가 누락해도 금융계산의 필수 선행조건과 순서를 코드가 보장한다.
        self._fallback_run_tools(user, context, trace)

        trace.append({'stage': 'recommendation', 'tool': 'Recommendation Agent', 'status': 'running', 'selected_by': 'Qwen' if self.qwen.enabled else 'fallback'})
        recommendation = self._recommendation_agent(user, context)
        trace[-1]['status'] = 'done'
        trace.stamp_last()

        trace.append({'stage': 'critic', 'tool': 'Critic Agent', 'status': 'running', 'selected_by': 'Qwen' if self.qwen.enabled else 'fallback'})
        critic = self._critic_agent(user, context, recommendation)
        trace[-1]['status'] = 'done' if critic.get('passed') else 'retry'
        trace.stamp_last()
        iterations = 1

        if not critic.get('passed'):
            iterations = 2
            revision = critic.get('revision_instructions') or '; '.join(critic.get('issues') or [])
            trace.append({'stage': 'recommendation', 'tool': 'Recommendation Agent', 'status': 'retry', 'selected_by': 'Qwen' if self.qwen.enabled else 'fallback', 'reason': revision})
            recommendation = self._recommendation_agent(user, context, critique=revision)
            trace.stamp_last()
            trace.begin('critic', 'Critic Agent', 'Qwen' if self.qwen.enabled else 'fallback')
            critic = self._critic_agent(user, context, recommendation)
            trace.append({'stage': 'critic', 'tool': 'Critic Agent', 'status': 'done' if critic.get('passed') else 'revised-with-warnings', 'selected_by': 'Qwen' if self.qwen.enabled else 'fallback'})

        trace.begin('report', 'Report Generator', 'Qwen' if self.qwen.enabled else 'template')
        report = self._report_agent(user, context, recommendation, critic)
        trace.append({'stage': 'report', 'tool': 'Report Generator', 'status': 'done', 'selected_by': 'Qwen' if self.qwen.enabled else 'template'})

        finished_at = datetime.now()
        timing = {
            'total_seconds': trace.total_seconds,
            'started_at': trace.started_at.isoformat(timespec='seconds'),
            'finished_at': finished_at.isoformat(timespec='seconds'),
            # 단계별 소요시간. 같은 단계가 재시도로 두 번 실행되면 합산한다.
            'stages': self._stage_durations(trace),
        }

        return {
            'timing': timing,
            'mode': {'planner': planner_mode, 'qwen_enabled': self.qwen.enabled, 'rag': context.get('rag', {}).get('mode', self.rag.mode), 'iterations': iterations},
            'user': user.model_dump() | {
                'years_to_retirement': user.years_to_retirement,
                'expected_additional_tenure_years': user.expected_additional_tenure_years if user.operation_type == 'DB' else None,
                'total_expected_tenure_years': user.total_expected_tenure_years,
            },
            'trace': trace,
            **context,
            'recommendation': recommendation,
            'critic': critic,
            'report': report,
        }
