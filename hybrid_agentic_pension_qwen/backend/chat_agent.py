"""Report Q&A Agent - 3단계 보고서 화면의 후속 질문 챗봇.

설계 원칙은 분석 파이프라인(agents.py)과 동일하다.

  숫자는 Python이 만들고, LLM은 검색·해석·설명만 한다. 문서에 없는 값은 만들지 않는다.

- 계산: tools.py의 기존 함수만 재사용한다. 새 수식을 만들지 않는다.
- 검색: rag.py의 search()를 그대로 쓰되 scope='all'로 전체 상품 코퍼스를 열 수 있다.
- 검증: Critic Agent와 같은 guardrails.py 규칙으로 답변을 검사하고 1회만 재생성한다.
- Qwen이 없어도(demo) 키워드 라우팅 폴백으로 동작한다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .formatting import won_amount
from .guardrails import answer_issues
from .models import UserPensionInput
from .qwen_client import QwenGateway
from .rag import PensionRAG
from .tools import (
    WITHDRAWAL_RATE,
    _required_contribution,
    finance_engine_tool,
    monte_carlo_tool,
    optimizer_candidate_market_inputs,
    product_market_inputs,
    project_assets_by_return,
)

MAX_TOOL_LOOPS = 4
ANALYSIS_SECTIONS = {'profile', 'finance', 'monte_carlo', 'optimizer', 'product_extraction', 'rag', 'report', 'critic'}

BLOCKED_ANSWER = (
    '방금 생성한 답변이 내부 검증 규칙(금액 표기 · 보장 표현 · 근거 인용)을 통과하지 못해 그대로 전달하지 않았습니다. '
    '질문을 조금 더 구체적으로 다시 적어주시면 보고서의 계산값과 PDF 근거를 기준으로 다시 답변드리겠습니다.'
)

CHAT_TOOL_DEFINITIONS = [
    {
        'type': 'function',
        'function': {
            'name': 'search_pension_documents',
            'description': (
                '퇴직연금 상품설명서 PDF 코퍼스에서 근거를 검색한다. '
                "scope='selected'는 사용자가 가입한 그 상품 PDF 안에서만, "
                "scope='all'은 전체 상품 DB에서 검색한다. 다른 상품과 비교하는 질문에만 'all'을 쓴다."
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'scope': {'type': 'string', 'enum': ['selected', 'all'], 'default': 'selected'},
                    'top_k': {'type': 'integer', 'minimum': 3, 'maximum': 8, 'default': 5},
                },
                'required': ['query'],
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'simulate_what_if',
            'description': (
                '납입액·자산배분·은퇴나이를 바꿨을 때의 결과를 Python 금융엔진으로 재계산한다. '
                '가정을 바꾼 질문에는 반드시 이 도구를 쓰고 직접 계산하지 마라. '
                '금액 단위는 내부 계산 스케일인 만원이다. 예: 연 1,200만원 납입 → annual_contribution=1200. '
                'DB형은 retirement_age와 wage_growth_rate만 적용된다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'annual_contribution': {'type': 'number', 'description': 'DC/IRP 연간 납입액, 만원 단위'},
                    'safe_ratio': {'type': 'number', 'minimum': 0, 'maximum': 1, 'description': 'DC/IRP 안전자산 비중 0~1'},
                    'retirement_age': {'type': 'integer', 'description': '변경할 은퇴 나이'},
                    'wage_growth_rate': {'type': 'number', 'description': 'DB형 임금상승률 %'},
                },
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_analysis_section',
            'description': '이미 수행된 분석 결과의 특정 섹션 원본 JSON을 가져온다.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'section': {'type': 'string', 'enum': sorted(ANALYSIS_SECTIONS)},
                },
                'required': ['section'],
                'additionalProperties': False,
            },
        },
    },
]

SYSTEM_PROMPT = (
    '너는 한국 퇴직연금 의사결정 지원 서비스 깨움의 Report Q&A Agent다. '
    '이미 완료된 분석 보고서를 사용자에게 설명하고 후속 질문에 답한다.\n'
    '규칙:\n'
    '1. 모든 계산값은 제공된 fact_sheet와 도구 반환 JSON에서만 인용한다. 숫자를 직접 계산하거나 지어내지 마라.\n'
    '2. 모든 금액은 fact_sheet의 amount_display 또는 도구가 돌려준 금액 문자열을 그대로 복사한다. '
    '이 값은 이미 실제 원화 숫자다. 억/만원 형태로 다시 축약하거나 환산하지 말고 통화 단위도 붙이지 마라. '
    '예: amount_display에 50,000,000이면 반드시 50,000,000이라고 쓴다.\n'
    '3. 가정을 바꾼 질문(납입액을 늘리면, 은퇴를 늦추면, 안전자산을 줄이면 등)은 반드시 simulate_what_if를 호출하고, '
    '그 결과를 현재 입력과 prototype 가정 아래 재계산된 시나리오로 설명한다. 예측이나 권유로 표현하지 마라.\n'
    '4. PDF 내용이 필요하면 search_pension_documents를 호출하고, 인용할 때 E1, E2 형식의 근거 ID를 문장에 넣는다. '
    '도구가 돌려주지 않은 근거 ID는 절대 쓰지 마라.\n'
    '5. 사용자가 가입한 상품이 아닌 다른 PDF를 인용할 때는 그 상품명을 문장 안에 반드시 밝힌다.\n'
    '6. optimizer의 allocation_direction과 반대되는 설명을 하지 마라. '
    '안전자산 비중이 늘었는데 성장형을 확대했다고 말하면 안 된다.\n'
    '7. product_extraction.risk_level_verified가 false이면 그 포트폴리오 자체의 위험등급을 단정하지 마라.\n'
    '8. 수익 보장, 확실히 달성, 무조건 달성 같은 표현을 쓰지 마라.\n'
    '9. DB형 사용자에게 개인 상품 운용을 권하지 마라. DB는 임금·근속·급여 Gap 중심으로 설명한다.\n'
    '10. 답변은 한국어 평문 3~6문장. 마크다운 기호, 표, 코드블록을 쓰지 마라.'
)


class ReportChatAgent:
    def __init__(self, qwen: QwenGateway, rag: PensionRAG):
        self.qwen = qwen
        self.rag = rag

    # ------------------------------------------------------------------ fact sheet

    def _fact_sheet(self, user: UserPensionInput, result: dict[str, Any]) -> dict[str, Any]:
        finance = result.get('finance') or {}
        mc = result.get('monte_carlo') or {}
        opt = result.get('optimizer') or {}
        ext = result.get('product_extraction') or {}
        report = result.get('report') or {}
        profile = result.get('profile') or {}

        # agents.py의 Recommendation/Report Agent와 동일한 amount_display 규약을 따른다.
        amount_display = {
            'annual_income': won_amount(user.annual_income),
            'desired_monthly_income': won_amount(user.desired_monthly_income),
            'current_savings': won_amount(user.current_savings),
            'annual_contribution': won_amount(user.annual_contribution),
            'target_retirement_asset': won_amount(finance.get('target_retirement_asset')),
            'future_asset': won_amount(finance.get('future_asset')),
            'gap': won_amount(finance.get('gap')),
            'required_annual_contribution': won_amount(opt.get('required_annual_contribution_for_expected_target')),
            'additional_annual_contribution': won_amount(opt.get('additional_annual_contribution_needed')),
            'monte_carlo_p10': won_amount(mc.get('p10')),
            'monte_carlo_p50': won_amount(mc.get('p50')),
            'monte_carlo_p90': won_amount(mc.get('p90')),
        }
        if user.operation_type == 'DB':
            amount_display['estimated_db_benefit'] = won_amount(finance.get('estimated_db_benefit'))
            amount_display['supplementary_asset_gap'] = won_amount(opt.get('supplementary_asset_gap'))
            amount_display['supplementary_monthly_saving_needed'] = won_amount(opt.get('supplementary_monthly_saving_needed'))

        return {
            'user': {
                'operation_type': user.operation_type,
                'age': user.age,
                'retirement_age': user.retirement_age,
                'years_to_retirement': user.years_to_retirement,
                'investment_type': user.investment_type,
                'provider': user.provider,
                'product_name': user.product_name,
                'current_tenure_years': user.current_tenure_years,
                'total_expected_tenure_years': user.total_expected_tenure_years,
            },
            'amount_display': amount_display,
            'profile': {
                'risk_capacity': profile.get('risk_capacity'),
                'diagnosis_hint': profile.get('diagnosis_hint'),
                'saving_rate_pct': profile.get('saving_rate_pct'),
                'desired_income_replacement_pct': profile.get('desired_income_replacement_pct'),
                'wage_growth': profile.get('wage_growth'),
            },
            'finance': {
                'engine_type': finance.get('engine_type'),
                'calculation_basis': finance.get('calculation_basis'),
                'goal_rate_pct': finance.get('goal_rate_pct'),
                'expected_return': finance.get('expected_return'),
                'expected_volatility': finance.get('expected_volatility'),
                'return_basis': finance.get('return_basis'),
                'wage_growth_rate_pct': finance.get('wage_growth_rate_pct'),
                'product_components': finance.get('product_components'),
                'withdrawal_rate_assumption': finance.get('withdrawal_rate_assumption'),
                'calculation_note': finance.get('calculation_note'),
            },
            'monte_carlo': {
                'simulation_type': mc.get('simulation_type'),
                'simulations': mc.get('simulations'),
                'success_probability_pct': mc.get('success_probability_pct'),
            },
            'optimizer': {
                'analysis_type': opt.get('analysis_type'),
                'portfolio_optimization_applicable': opt.get('portfolio_optimization_applicable'),
                'recommended_allocation': opt.get('recommended_allocation'),
                'allocation_direction': opt.get('allocation_direction'),
                'current_product_safe_ratio_proxy': opt.get('current_product_safe_ratio_proxy'),
                'recommended_safe_ratio': opt.get('recommended_safe_ratio'),
                'optimization_objective': opt.get('optimization_objective'),
                'goal_rate_pct': opt.get('goal_rate_pct'),
                'success_probability_pct': opt.get('success_probability_pct'),
                'counterfactuals': opt.get('counterfactuals'),
                'note': opt.get('note'),
            },
            'product_extraction': {
                'source_filename': ext.get('source_filename'),
                'product_name': ext.get('product_name'),
                'asset_allocation': ext.get('asset_allocation'),
                'principal_guaranteed_ratio_pct': ext.get('principal_guaranteed_ratio_pct'),
                'portfolio_fee_pct': ext.get('portfolio_fee_pct'),
                'risk_level_document': ext.get('risk_level_document'),
                'risk_level_verified': ext.get('risk_level_verified'),
                'calculation_ready': ext.get('calculation_ready'),
                'missing_for_projection': ext.get('missing_for_projection'),
            },
            'report': {
                'executive_summary': report.get('executive_summary'),
                'strategy': report.get('strategy'),
                'risk_notes': report.get('risk_notes'),
            },
        }

    # ------------------------------------------------------------------ evidence

    @staticmethod
    def _selected_file_id(result: dict[str, Any]) -> str | None:
        ext = result.get('product_extraction') or {}
        return ext.get('source_file_id')

    def _register_evidence(self, state: dict[str, Any], result: dict[str, Any], rows: list[dict]) -> list[dict]:
        """한 번의 대화 턴 안에서 근거 ID를 E1부터 전역으로 다시 매긴다.

        search_pension_documents를 두 번 호출하면 rag.search가 매번 E1부터 다시 매기므로
        그대로 두면 ID가 충돌한다. 인용 검증을 하려면 턴 단위로 유일해야 한다.
        """
        selected = self._selected_file_id(result)
        out = []
        for row in rows:
            key = (row.get('file_id'), row.get('page'), (row.get('snippet') or '')[:60])
            if key in state['evidence_keys']:
                out.append(state['evidence_keys'][key])
                continue
            entry = dict(row)
            entry['evidence_id'] = f"E{len(state['evidence']) + 1}"
            entry['is_selected_product'] = bool(selected) and row.get('file_id') == selected
            state['evidence'].append(entry)
            state['evidence_keys'][key] = entry
            out.append(entry)
        return out

    # ------------------------------------------------------------------ what-if

    @staticmethod
    def _normalize_manwon(value: Any, user: UserPensionInput, field: str) -> tuple[float | None, list[str]]:
        """실제 원화 숫자를 그대로 넣은 경우 내부 만원 스케일로 되돌린다.

        화면에는 V18 규칙에 따라 실제 원화가 표시되므로 사용자와 LLM 모두
        12,000,000 같은 값을 그대로 넘길 수 있다.
        """
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None, []
        notes: list[str] = []
        ceiling = max(float(user.annual_income or 0) * 3, 100000.0)
        if v > ceiling and v / 10000 <= ceiling:
            notes.append(f'{field}에 실제 원화 숫자가 들어와 내부 계산 스케일로 환산했습니다.')
            v = v / 10000
        return v, notes

    def _what_if(
        self,
        user: UserPensionInput,
        result: dict[str, Any],
        annual_contribution: Any = None,
        safe_ratio: Any = None,
        retirement_age: Any = None,
        wage_growth_rate: Any = None,
    ) -> dict[str, Any]:
        notes: list[str] = []
        clamped = False
        updates: dict[str, Any] = {}
        changes: dict[str, Any] = {}

        if retirement_age is not None:
            try:
                new_age = int(retirement_age)
            except (TypeError, ValueError):
                new_age = None
            if new_age is not None:
                low, high = max(40, user.age + 1), 85
                if new_age < low or new_age > high:
                    clamped = True
                    notes.append(f'은퇴 나이 {new_age}세는 허용 범위({low}~{high}세)를 벗어나 조정했습니다.')
                    new_age = min(high, max(low, new_age))
                updates['retirement_age'] = new_age
                changes['retirement_age'] = new_age

        if user.operation_type == 'DB':
            if annual_contribution is not None or safe_ratio is not None:
                return {
                    'applicable': False,
                    'reason': (
                        'DB형은 근로자가 개인 적립금을 납입하거나 운용상품을 고르는 구조가 아니라서 '
                        '납입액·자산배분 시나리오를 적용하지 않습니다. 은퇴 나이나 임금상승률 시나리오만 계산할 수 있습니다.'
                    ),
                }
            if wage_growth_rate is not None:
                try:
                    rate = float(wage_growth_rate)
                except (TypeError, ValueError):
                    rate = None
                if rate is not None:
                    if rate < -5 or rate > 20:
                        clamped = True
                        notes.append(f'임금상승률 {rate}%는 허용 범위(-5~20%)를 벗어나 조정했습니다.')
                        rate = min(20.0, max(-5.0, rate))
                    updates['wage_growth_rate'] = rate
                    changes['wage_growth_rate_pct'] = rate
        else:
            if wage_growth_rate is not None:
                notes.append('임금상승률은 DB형 전용 입력이라 DC/IRP 시나리오에서는 무시했습니다.')
            if annual_contribution is not None:
                value, conv_notes = self._normalize_manwon(annual_contribution, user, '연간 납입액')
                notes += conv_notes
                if value is None:
                    annual_contribution = None
                else:
                    ceiling = float(user.annual_income) * 0.6
                    if value < 0 or value > ceiling:
                        clamped = True
                        notes.append('연간 납입액은 0부터 현재 연소득의 60%까지만 시나리오로 계산합니다.')
                        value = min(ceiling, max(0.0, value))
                    changes['annual_contribution'] = won_amount(value)
                    annual_contribution = value
            if safe_ratio is not None:
                try:
                    ratio = float(safe_ratio)
                except (TypeError, ValueError):
                    ratio = None
                if ratio is not None:
                    if ratio > 1:  # 60 처럼 퍼센트로 넣은 경우
                        ratio = ratio / 100.0
                    if ratio < 0 or ratio > 1:
                        clamped = True
                        notes.append('안전자산 비중은 0~100% 범위로 조정했습니다.')
                        ratio = min(1.0, max(0.0, ratio))
                    changes['safe_ratio_pct'] = round(ratio * 100, 1)
                safe_ratio = ratio

        if not changes:
            return {
                'applicable': False,
                'reason': '변경할 가정이 지정되지 않았습니다. 납입액, 안전자산 비중, 은퇴 나이 중 하나를 지정해주세요.',
            }

        try:
            scenario_user = UserPensionInput.model_validate({**user.model_dump(), **updates}) if updates else user
        except Exception as e:
            return {'applicable': False, 'reason': f'변경한 조건이 입력 제약을 만족하지 않습니다: {e}'}

        finance = result.get('finance') or {}
        mc_base = result.get('monte_carlo') or {}
        baseline = {
            'retirement_age': user.retirement_age,
            'annual_contribution': won_amount(user.annual_contribution),
            'future_asset': won_amount(finance.get('future_asset')),
            'goal_rate_pct': finance.get('goal_rate_pct'),
            'success_probability_pct': mc_base.get('success_probability_pct'),
        }
        target = scenario_user.desired_monthly_income * 12 / WITHDRAWAL_RATE

        if scenario_user.operation_type == 'DB':
            baseline['wage_growth_rate_pct'] = finance.get('wage_growth_rate_pct')
            scenario_finance = finance_engine_tool(scenario_user)
            scenario_mc = monte_carlo_tool(scenario_user, simulations=1500)
            future_value = scenario_finance.get('estimated_db_benefit') or 0
            scenario = {
                'retirement_age': scenario_user.retirement_age,
                'wage_growth_rate_pct': scenario_finance.get('wage_growth_rate_pct'),
                'total_expected_tenure_years': scenario_finance.get('total_expected_tenure_years'),
                'future_asset': won_amount(future_value),
                'goal_rate_pct': scenario_finance.get('goal_rate_pct'),
                'success_probability_pct': scenario_mc.get('success_probability_pct'),
            }
            base_value = finance.get('estimated_db_benefit') or 0
            calculation_basis = 'DB_BENEFIT_ENGINE'
        else:
            extraction = result.get('product_extraction')
            if safe_ratio is not None:
                market = optimizer_candidate_market_inputs(safe_ratio)
                mu = market['expected_return']
                allocation = market['allocation']
                calculation_basis = 'optimizer_candidate_allocation_cma'
            else:
                market = product_market_inputs(extraction, scenario_user.investment_type)
                mu = market['expected_return']
                allocation = None
                calculation_basis = market['calculation_basis']
            contribution = scenario_user.annual_contribution if annual_contribution is None else annual_contribution
            future_value, _series = project_assets_by_return(scenario_user, mu, contribution)
            scenario_mc = monte_carlo_tool(
                scenario_user,
                product_extraction=extraction,
                safe_ratio=safe_ratio,
                annual_contribution=contribution,
                simulations=1500,
            )
            scenario = {
                'retirement_age': scenario_user.retirement_age,
                'annual_contribution': won_amount(contribution),
                'allocation': allocation,
                'expected_return_pct': round(mu * 100, 2),
                'future_asset': won_amount(future_value),
                'goal_rate_pct': round(future_value / target * 100, 1),
                'success_probability_pct': scenario_mc.get('success_probability_pct'),
            }
            if safe_ratio is not None:
                # 후보 자산배분이 지정된 경우에만 optimizer와 같은 정의로 필요 납입액을 구할 수 있다.
                scenario['required_annual_contribution_for_expected_target'] = won_amount(
                    _required_contribution(scenario_user, safe_ratio, target)
                )
            base_value = finance.get('future_asset') or 0

        base_goal = baseline.get('goal_rate_pct')
        base_prob = baseline.get('success_probability_pct')
        scenario_goal = scenario.get('goal_rate_pct')
        scenario_prob = scenario.get('success_probability_pct')
        return {
            'applicable': True,
            'operation_type': scenario_user.operation_type,
            'changes': changes,
            'clamped': clamped,
            'notes': notes,
            'calculation_basis': calculation_basis,
            'baseline': baseline,
            'scenario': scenario,
            'delta': {
                'future_asset': won_amount(float(future_value) - float(base_value)),
                'goal_rate_pct': round(float(scenario_goal) - float(base_goal), 1) if None not in (scenario_goal, base_goal) else None,
                'success_probability_pct': round(float(scenario_prob) - float(base_prob), 1) if None not in (scenario_prob, base_prob) else None,
            },
            'note': '현재 입력과 prototype 자본시장가정 아래 재계산된 시나리오입니다. 미래 수익이나 실제 퇴직급여를 보장하지 않습니다.',
        }

    # ------------------------------------------------------------------ tools

    def _execute_tool(self, name: str, args: dict[str, Any], user: UserPensionInput, result: dict[str, Any], state: dict[str, Any]):
        if name == 'search_pension_documents':
            scope = args.get('scope') or 'selected'
            payload = self.rag.search(
                query=str(args.get('query') or '')[:300],
                provider=user.provider,
                product_name=user.product_name,
                risk_type=user.investment_type,
                top_k=int(args.get('top_k') or 5),
                scope=scope,
            )
            rows = self._register_evidence(state, result, payload.get('results', []))
            state['search_scope'] = payload.get('search_scope', scope)
            return {
                'search_scope': payload.get('search_scope'),
                'mode': payload.get('mode'),
                'results': [
                    {
                        'evidence_id': r['evidence_id'],
                        'provider': r.get('provider'),
                        'title': r.get('title'),
                        'page': r.get('page'),
                        'is_selected_product': r.get('is_selected_product'),
                        'snippet': r.get('snippet'),
                    }
                    for r in rows
                ],
            }
        if name == 'simulate_what_if':
            outcome = self._what_if(
                user,
                result,
                annual_contribution=args.get('annual_contribution'),
                safe_ratio=args.get('safe_ratio'),
                retirement_age=args.get('retirement_age'),
                wage_growth_rate=args.get('wage_growth_rate'),
            )
            if outcome.get('applicable'):
                state['what_if'] = outcome
            return outcome
        if name == 'get_analysis_section':
            section = args.get('section')
            if section not in ANALYSIS_SECTIONS:
                return {'error': f'알 수 없는 섹션: {section}'}
            return result.get(section) or {}
        return {'error': f'알 수 없는 도구: {name}'}

    # ------------------------------------------------------------------ main entry

    def answer(self, session: dict[str, Any], message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        user: UserPensionInput = session['user']
        result: dict[str, Any] = session['result']
        state: dict[str, Any] = {
            'evidence': [],
            'evidence_keys': {},
            'what_if': None,
            'tool_trace': [],
            'search_scope': 'selected',
        }
        fact_sheet = self._fact_sheet(user, result)

        if not self.qwen.enabled:
            answer_text = self._fallback_answer(user, result, fact_sheet, message, state)
            return self._response(answer_text, state, {'passed': True, 'issues': [], 'mode': 'demo fallback'})

        messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': SYSTEM_PROMPT + '\n\nfact_sheet:\n' + json.dumps(fact_sheet, ensure_ascii=False)},
        ]
        for turn in (history or []):
            role = turn.get('role')
            if role in {'user', 'assistant'} and turn.get('content'):
                messages.append({'role': role, 'content': str(turn['content'])[:2000]})
        messages.append({'role': 'user', 'content': message})

        try:
            answer_text = self._run_llm(messages, user, result, state)
        except Exception as e:
            state['tool_trace'].append({'tool': 'qwen_chat', 'status': 'error', 'error': str(e)})
            answer_text = self._fallback_answer(user, result, fact_sheet, message, state)
            return self._response(answer_text, state, {'passed': True, 'issues': [], 'mode': 'qwen 오류 후 폴백'})

        issues = answer_issues(answer_text, [e['evidence_id'] for e in state['evidence']])
        if issues:
            # Critic Agent와 같은 방식으로 1회만 재생성한다.
            messages.append({'role': 'assistant', 'content': answer_text})
            messages.append({
                'role': 'user',
                'content': '이전 답변이 내부 검증을 통과하지 못했다. 다음 문제를 모두 고쳐 같은 질문에 다시 답하라: ' + '; '.join(issues),
            })
            answer_text = self._run_llm(messages, user, result, state)
            issues = answer_issues(answer_text, [e['evidence_id'] for e in state['evidence']])
            if issues:
                return self._response(BLOCKED_ANSWER, state, {'passed': False, 'issues': issues, 'blocked': True})

        if not answer_text.strip():
            answer_text = self._fallback_answer(user, result, fact_sheet, message, state)
        return self._response(answer_text, state, {'passed': True, 'issues': []})

    def _run_llm(self, messages: list[dict[str, Any]], user: UserPensionInput, result: dict[str, Any], state: dict[str, Any]) -> str:
        content = ''
        for _ in range(MAX_TOOL_LOOPS):
            response = self.qwen.chat(messages, tools=CHAT_TOOL_DEFINITIONS, tool_choice='auto', temperature=0.2)
            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            content = msg.content or content
            if not msg.tool_calls:
                break
            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or '{}')
                except Exception:
                    args = {}
                try:
                    payload = self._execute_tool(name, args, user, result, state)
                    state['tool_trace'].append({'tool': name, 'args': args, 'status': 'done'})
                except Exception as e:
                    payload = {'error': str(e)}
                    state['tool_trace'].append({'tool': name, 'args': args, 'status': 'error', 'error': str(e)})
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': json.dumps(payload, ensure_ascii=False)})
        return (content or '').strip()

    def _response(self, answer_text: str, state: dict[str, Any], guardrail: dict[str, Any]) -> dict[str, Any]:
        return {
            'answer': answer_text,
            'evidence': [
                {
                    'evidence_id': e['evidence_id'],
                    'provider': e.get('provider'),
                    'title': e.get('title'),
                    'filename': e.get('filename'),
                    'page': e.get('page'),
                    'snippet': e.get('snippet'),
                    'is_selected_product': e.get('is_selected_product', False),
                }
                for e in state['evidence']
            ],
            'what_if': state['what_if'],
            'tool_trace': state['tool_trace'],
            'search_scope': state['search_scope'],
            'guardrail': guardrail,
        }

    # ------------------------------------------------------------------ demo fallback

    def _fallback_what_if(self, user: UserPensionInput, result: dict[str, Any], message: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """Qwen 없이도 what-if가 동작하도록 질문에서 숫자를 직접 뽑는다."""
        text = message.replace(' ', '')
        args: dict[str, Any] = {}

        m = re.search(r'([\d,]+)\s*만?\s*원?[^0-9]{0,6}(?:납입|불입|넣)', text)
        if m:
            args['annual_contribution'] = float(m.group(1).replace(',', ''))
        else:
            m = re.search(r'(?:납입|불입)[^0-9]{0,8}([\d,]+)\s*만?\s*원?', text)
            if m:
                args['annual_contribution'] = float(m.group(1).replace(',', ''))

        m = re.search(r'은퇴[^0-9]{0,8}(\d{2})\s*세', text)
        if m:
            args['retirement_age'] = int(m.group(1))

        m = re.search(r'(?:안전자산|원리금보장)[^0-9]{0,8}(\d{1,3})\s*%', text)
        if m:
            args['safe_ratio'] = float(m.group(1)) / 100.0

        m = re.search(r'임금상승률[^0-9\-]{0,8}(-?\d+(?:\.\d+)?)\s*%', text)
        if m:
            args['wage_growth_rate'] = float(m.group(1))

        if not args:
            return None
        outcome = self._what_if(user, result, **args)
        state['tool_trace'].append({'tool': 'simulate_what_if', 'args': args, 'status': 'done'})
        if outcome.get('applicable'):
            state['what_if'] = outcome
        return outcome

    def _fallback_answer(
        self,
        user: UserPensionInput,
        result: dict[str, Any],
        fact_sheet: dict[str, Any],
        message: str,
        state: dict[str, Any],
    ) -> str:
        a = fact_sheet['amount_display']
        finance = fact_sheet['finance']
        mc = fact_sheet['monte_carlo']
        opt = fact_sheet['optimizer']
        ext = fact_sheet['product_extraction']
        is_db = user.operation_type == 'DB'
        text = message.replace(' ', '')

        what_if = self._fallback_what_if(user, result, message, state)
        if what_if is not None:
            if not what_if.get('applicable'):
                return what_if.get('reason', '해당 조건으로는 재계산할 수 없습니다.')
            s, b = what_if['scenario'], what_if['baseline']
            parts = [
                f"바꾼 조건으로 Python 금융엔진이 다시 계산했습니다. 예상 자산은 {b['future_asset']}에서 {s['future_asset']}으로, "
                f"목표달성률은 {b['goal_rate_pct']}%에서 {s['goal_rate_pct']}%로, "
                f"몬테카를로 목표달성확률은 {b['success_probability_pct']}%에서 {s['success_probability_pct']}%로 바뀝니다."
            ]
            if what_if.get('notes'):
                parts.append(' '.join(what_if['notes']))
            parts.append(what_if['note'])
            return ' '.join(parts)

        def has(*words: str) -> bool:
            return any(w in text for w in words)

        if has('목표달성률', '달성률', '왜낮', '왜부족'):
            return (
                f"현재 기준 목표달성률은 {finance['goal_rate_pct']}%입니다. "
                f"희망 월소득을 4% 인출률로 환산한 목표자산이 {a['target_retirement_asset']}인데 "
                f"예상 자산은 {a['future_asset']}이라 차이가 {a['gap']}입니다. "
                + ('DB형은 임금상승률과 근속기간이 이 값을 좌우합니다.' if is_db else '납입액과 자산배분이 이 값을 좌우합니다.')
            )
        if has('확률', '몬테카를로', '시뮬레이션'):
            return (
                f"몬테카를로 {mc['simulations']}회 시뮬레이션에서 목표자산 이상에 도달한 비율은 {mc['success_probability_pct']}%입니다. "
                f"하위 10% 시나리오는 {a['monte_carlo_p10']}, 중앙값은 {a['monte_carlo_p50']}, 상위 10%는 {a['monte_carlo_p90']}입니다. "
                '이 분포는 prototype 자본시장가정에 따른 계산 결과이며 미래 수익을 보장하지 않습니다.'
            )
        if has('목표자산', '얼마필요', '얼마있어야'):
            return (
                f"희망 월소득 {a['desired_monthly_income']}을 4% 인출률 가정으로 환산한 목표 은퇴자산은 {a['target_retirement_asset']}입니다. "
                f"현재 계산상 예상 자산은 {a['future_asset']}이고 차이는 {a['gap']}입니다."
            )
        if not is_db and has('구성', '비중', '어떤상품', '포트폴리오'):
            alloc = ', '.join(
                f"{x.get('component_name')} {x.get('weight_pct')}%" for x in (ext.get('asset_allocation') or [])[:8]
            ) or '구성비중을 추출하지 못했습니다'
            risk_note = (
                f" 문서상 위험등급은 {ext['risk_level_document']}로 확인했습니다."
                if ext.get('risk_level_verified')
                else ' 이 상품 자체의 위험등급은 PDF에서 명확히 검증되지 않아 단정하지 않습니다.'
            )
            return f"{ext.get('source_filename') or '선택한 상품 PDF'}에서 추출한 구성은 {alloc}입니다.{risk_note}"
        if has('자산배분', '추천비중', '최적화'):
            if is_db:
                return opt.get('note') or 'DB형은 개인 자산배분 최적화 대신 예상 급여와 희망 노후소득의 Gap을 분석합니다.'
            return (
                f"최적화가 제시한 후보 자산배분은 {opt.get('recommended_allocation')}이고 방향은 {opt.get('allocation_direction')}입니다. "
                f"현재 상품의 안전자산 비중 추정치 {opt.get('current_product_safe_ratio_proxy')}%와 비교한 값입니다. "
                '이 비중은 확정 처방이 아니라 사용한 가정 아래의 후보 최적해입니다.'
            )
        if has('납입', '더내', '얼마내'):
            if is_db:
                return (
                    f"DB형은 개인 납입 구조가 아닙니다. 대신 예상 급여와 목표자산의 차이 {a.get('supplementary_asset_gap', '-')}를 "
                    f"별도 저축으로 메운다면 단순 추정 월 {a.get('supplementary_monthly_saving_needed', '-')} 수준입니다."
                )
            return (
                f"기대값 기준으로 목표자산에 도달하려면 연간 납입액이 {a['required_annual_contribution']} 수준으로 추정됩니다. "
                f"현재 납입액 {a['annual_contribution']} 대비 {a['additional_annual_contribution']}이 추가로 필요합니다."
            )
        if has('위험', '주의', '리스크', '가정'):
            notes = fact_sheet['report'].get('risk_notes') or []
            return ' '.join(notes[:3]) or '보고서 하단의 위험요인 및 계산 가정 섹션을 확인해주세요.'
        if is_db and has('퇴직급여', '급여', '임금', '근속'):
            return (
                f"임금상승률 {finance.get('wage_growth_rate_pct')}%와 예상 총 근속 {fact_sheet['user']['total_expected_tenure_years']}년을 적용해 "
                f"예상 DB 퇴직급여를 {a.get('estimated_db_benefit', '-')}로 계산했습니다. {finance.get('calculation_note') or ''}"
            )
        if has('근거', '출처', 'pdf', '어디에'):
            payload = self._execute_tool(
                'search_pension_documents',
                {'query': message, 'scope': 'selected', 'top_k': 4},
                user,
                result,
                state,
            )
            state['tool_trace'].append({'tool': 'search_pension_documents', 'args': {'scope': 'selected'}, 'status': 'done'})
            rows = payload.get('results') or []
            if rows:
                cited = ', '.join(f"{r['evidence_id']}({r.get('title')} p.{r.get('page')})" for r in rows[:3])
                return f"관련 근거를 상품설명서에서 찾았습니다: {cited}. 아래 근거 카드를 눌러 원문을 확인할 수 있습니다."
            return '해당 질문과 연결되는 PDF 근거를 찾지 못했습니다.'

        return (
            'Qwen API 키가 설정되지 않아 정해진 항목만 안내할 수 있습니다. '
            f"현재 목표달성률은 {finance['goal_rate_pct']}%, 예상 자산은 {a['future_asset']}, 목표자산은 {a['target_retirement_asset']}입니다. "
            '목표달성률, 몬테카를로 확률, 상품 구성, 자산배분, 납입액, 위험요인, 근거 출처를 물어보거나 '
            '납입액을 1200만원으로 늘리면 어떻게 되는지처럼 조건을 바꿔 질문하시면 다시 계산해 드립니다.'
        )
