from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np

from .models import UserPensionInput

# 투자유형별 값은 이제 '현재 가입상품 계산'에 쓰지 않는다.
# 포트폴리오 최적화 후보의 탐색범위/비교용 fallback에만 사용한다.
SAFE_RATIO_BY_TYPE = {
    '안정형': 0.90,
    '안정투자형': 0.72,
    '중립투자형': 0.50,
    '적극투자형': 0.28,
}

WITHDRAWAL_RATE = 0.04

# PDF에는 미래 기대수익률/변동성이 없는 경우가 많다.
# 따라서 '상품구성 비중'은 PDF에서 정확히 추출하고, 미래 시뮬레이션에 필요한
# 자산군별 수익률/변동성만 별도 CMA 가정으로 보완한다.
# 아래 숫자는 공모전 프로토타입용이며 공식 전망치가 아니다.
CMA = {
    '원리금보장/예금': {'return': 0.032, 'vol': 0.008, 'safe': True},
    '현금성': {'return': 0.028, 'vol': 0.005, 'safe': True},
    '국내채권': {'return': 0.040, 'vol': 0.050, 'safe': True},
    '해외채권': {'return': 0.045, 'vol': 0.070, 'safe': True},
    '채권형': {'return': 0.042, 'vol': 0.060, 'safe': True},
    '국내주식': {'return': 0.065, 'vol': 0.180, 'safe': False},
    '해외주식': {'return': 0.070, 'vol': 0.170, 'safe': False},
    '주식형': {'return': 0.068, 'vol': 0.175, 'safe': False},
    'TDF': {'return': 0.058, 'vol': 0.120, 'safe': False},
    'BF': {'return': 0.050, 'vol': 0.090, 'safe': False},
    '혼합형': {'return': 0.055, 'vol': 0.110, 'safe': False},
    '기타': {'return': 0.050, 'vol': 0.110, 'safe': False},
}

# 기존 후보 포트폴리오 탐색용 단순 2자산 CMA
SAFE_RETURN = CMA['원리금보장/예금']['return']
GROWTH_RETURN = CMA['주식형']['return']
SAFE_VOL = CMA['원리금보장/예금']['vol']
GROWTH_VOL = CMA['주식형']['vol']

# DB 임금상승률 추정용 prototype benchmark. 공식 통계값으로 주장하지 않음.
BASE_WAGE_GROWTH = {
    '대기업': 3.4,
    '중견기업': 3.1,
    '중소기업': 2.8,
    '공공/기타': 2.7,
    None: 3.0,
}


def allocation_from_safe_ratio(safe_ratio: float) -> dict[str, float]:
    safe_pct = round(safe_ratio * 100, 1)
    risky = 100 - safe_pct
    bond = round(risky * 0.34, 1)
    growth = round(100 - safe_pct - bond, 1)
    return {'원리금보장/현금성': safe_pct, '채권형': bond, '성장형': growth}


def expected_return(safe_ratio: float) -> float:
    return SAFE_RETURN * safe_ratio + GROWTH_RETURN * (1 - safe_ratio)


def expected_volatility(safe_ratio: float) -> float:
    return math.sqrt((SAFE_VOL * safe_ratio) ** 2 + (GROWTH_VOL * (1 - safe_ratio)) ** 2)


def optimizer_candidate_market_inputs(safe_ratio: float) -> dict[str, Any]:
    """Market inputs for the optimizer's displayed 3-bucket allocation.

    Unlike the legacy 2-asset shortcut, this uses the exact allocation shown to the user
    (principal-safe / bonds / growth) so the optimizer's return, volatility and chart are
    internally consistent.
    """
    alloc = allocation_from_safe_ratio(safe_ratio)
    weights = {
        '원리금보장/현금성': alloc['원리금보장/현금성'] / 100.0,
        '채권형': alloc['채권형'] / 100.0,
        '성장형': alloc['성장형'] / 100.0,
    }
    assumptions = {
        '원리금보장/현금성': CMA['원리금보장/예금'],
        '채권형': CMA['채권형'],
        '성장형': CMA['주식형'],
    }
    mu = sum(weights[k] * assumptions[k]['return'] for k in weights)

    variance = 0.0
    keys = list(weights)
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i == j:
                corr = 1.0
            elif '원리금보장/현금성' in {a, b}:
                corr = 0.10
            elif {a, b} == {'채권형', '성장형'}:
                corr = 0.20
            else:
                corr = 0.30
            variance += weights[a] * weights[b] * assumptions[a]['vol'] * assumptions[b]['vol'] * corr
    sigma = math.sqrt(max(0.0, variance))
    return {'allocation': alloc, 'expected_return': mu, 'volatility': sigma}


def _history_cagr(history: list[float]) -> float | None:
    vals = [float(x) for x in history if x and float(x) > 0]
    if len(vals) < 2 or vals[0] <= 0:
        return None
    periods = len(vals) - 1
    return ((vals[-1] / vals[0]) ** (1 / periods) - 1) * 100


def estimate_wage_growth(user: UserPensionInput) -> dict[str, Any]:
    if user.wage_growth_rate is not None:
        return {
            'rate_pct': round(float(user.wage_growth_rate), 2),
            'source': 'user_input',
            'explanation': '사용자가 직접 입력한 예상 임금상승률을 사용했습니다.',
        }

    benchmark = BASE_WAGE_GROWTH.get(user.company_size, 3.0)
    job = (user.industry_job or '').lower()
    adjustment = 0.0
    if any(k in job for k in ['it', '개발', '소프트웨어', 'ai', '금융', '반도체']):
        adjustment += 0.35
    if any(k in job for k in ['공무원', '공공', '교육']):
        adjustment -= 0.25
    benchmark = max(1.0, min(6.0, benchmark + adjustment))

    personal = _history_cagr(user.salary_history)
    if personal is not None:
        personal = max(-2.0, min(10.0, personal))
        rate = 0.70 * personal + 0.30 * benchmark
        return {
            'rate_pct': round(rate, 2),
            'source': 'salary_history+benchmark',
            'personal_cagr_pct': round(personal, 2),
            'benchmark_pct': round(benchmark, 2),
            'explanation': '최근 연봉 이력의 CAGR을 중심으로 회사규모·직군 기반 prototype benchmark를 보조적으로 반영했습니다.',
        }

    return {
        'rate_pct': round(benchmark, 2),
        'source': 'benchmark',
        'benchmark_pct': round(benchmark, 2),
        'explanation': '개인 연봉 이력이 부족하여 회사규모·직군 기반 prototype benchmark를 사용했습니다.',
    }


def profile_tool(user: UserPensionInput) -> dict[str, Any]:
    years = user.years_to_retirement
    current_monthly_income = user.annual_income / 12
    replacement_target = user.desired_monthly_income / current_monthly_income * 100
    capacity = '높음' if years >= 20 else '중간' if years >= 10 else '낮음'

    if user.operation_type == 'DB':
        wage = estimate_wage_growth(user)
        return {
            'years_to_retirement': years,
            'expected_additional_tenure_years': user.expected_additional_tenure_years,
            'current_tenure_years': user.current_tenure_years,
            'total_expected_tenure_years': user.total_expected_tenure_years,
            'risk_capacity': 'DB 급여분석 중심',
            'investment_type': None,
            'saving_rate_pct': None,
            'desired_income_replacement_pct': round(replacement_target, 1),
            'operation_type': 'DB',
            'wage_growth': wage,
            'industry_job': user.industry_job,
            'company_size': user.company_size,
            'diagnosis_hint': 'DB형은 개인 적립금 수익률보다 임금·근속기간이 예상 퇴직급여에 직접적인 영향을 줍니다.',
            'current_allocation_proxy': None,
        }

    saving_rate = (user.annual_contribution or 0) / user.annual_income * 100
    if years >= 20 and user.investment_type in {'안정형', '안정투자형'}:
        mismatch = '장기 투자기간 대비 보수적 성향이 강한 편'
    elif years < 10 and user.investment_type == '적극투자형':
        mismatch = '은퇴 임박 기간 대비 공격적 성향이 강한 편'
    else:
        mismatch = '투자기간과 투자유형의 큰 불일치 없음'

    return {
        'years_to_retirement': years,
        'risk_capacity': capacity,
        'investment_type': user.investment_type,
        'saving_rate_pct': round(saving_rate, 1),
        'desired_income_replacement_pct': round(replacement_target, 1),
        'operation_type': user.operation_type,
        'diagnosis_hint': mismatch,
        # 실제 현재 자산배분은 Product Extraction Agent 결과를 사용한다.
        'current_allocation_proxy': None,
        'current_allocation_source': 'selected_product_pdf',
    }


def project_assets_by_return(user: UserPensionInput, annual_return: float, annual_contribution: float | None = None):
    annual_contribution = (user.annual_contribution or 0) if annual_contribution is None else annual_contribution
    value = float(user.current_savings or 0)
    series = [{'year': 0, 'age': user.age, 'value': round(value, 2)}]
    for year in range(1, user.years_to_retirement + 1):
        value = value * (1 + annual_return) + annual_contribution
        series.append({'year': year, 'age': user.age + year, 'value': round(value, 2)})
    return value, series


def project_assets(user: UserPensionInput, safe_ratio: float, annual_contribution: float | None = None):
    market = optimizer_candidate_market_inputs(safe_ratio)
    r = market['expected_return']
    value, series = project_assets_by_return(user, r, annual_contribution)
    return value, series, r


def _db_benefit_projection(user: UserPensionInput, wage_growth_rate_pct: float):
    g = wage_growth_rate_pct / 100.0
    series = []
    for year in range(0, user.years_to_retirement + 1):
        annual_income = user.annual_income * ((1 + g) ** year)
        monthly_wage_proxy = annual_income / 12
        tenure = float(user.current_tenure_years or 0) + year
        estimated_benefit = monthly_wage_proxy * tenure
        series.append({
            'year': year,
            'age': user.age + year,
            'value': round(estimated_benefit, 2),
            'annual_income': round(annual_income, 2),
            'tenure_years': round(tenure, 2),
        })
    return series[-1]['value'], series


def _component_market_input(component: dict[str, Any]) -> dict[str, Any]:
    cls = component.get('asset_class') or '기타'
    assumption = CMA.get(cls, CMA['기타'])
    stated = component.get('stated_rate_pct')
    # 문서에 원리금보장상품의 적용금리가 명시되어 있을 때만 그 수치를 우선 사용.
    if stated is not None and (component.get('principal_guaranteed') is True or cls == '원리금보장/예금'):
        r = float(stated) / 100.0
        source = 'pdf_stated_rate'
    else:
        r = assumption['return']
        source = 'cma_assumption'
    return {
        'component_name': component.get('component_name'),
        'weight_pct': float(component.get('weight_pct') or 0),
        'asset_class': cls,
        'expected_return': r,
        'volatility': assumption['vol'],
        'return_source': source,
        'evidence_pages': component.get('evidence_pages') or [],
    }


def product_market_inputs(product_extraction: dict[str, Any] | None, investment_type: str | None = None) -> dict[str, Any]:
    """PDF 추출값을 Python 계산 입력으로 변환한다.

    구성비중은 PDF 추출값을 사용한다. 미래 기대수익률/변동성이 PDF에 없으면
    자산군 CMA만 보완한다. 따라서 '상품구성' 자체를 투자유형 임의비율로 대체하지 않는다.
    """
    ext = product_extraction or {}
    allocations = ext.get('asset_allocation') or []
    valid = bool(ext.get('calculation_ready')) and bool(allocations)

    if valid:
        components = [_component_market_input(x) for x in allocations]
        total_weight = sum(x['weight_pct'] for x in components) or 100.0
        for x in components:
            x['weight'] = x['weight_pct'] / total_weight

        document_return = ext.get('document_expected_return_pct')
        document_vol = ext.get('document_volatility_pct')
        if document_return is not None:
            mu = float(document_return) / 100.0
            return_basis = 'pdf_document_expected_return'
        else:
            mu = sum(x['weight'] * x['expected_return'] for x in components)
            return_basis = 'pdf_allocation_plus_cma'

        if document_vol is not None:
            sigma = float(document_vol) / 100.0
            vol_basis = 'pdf_document_volatility'
        else:
            # 간이 공분산: 동일 계열 0.55, 안전-위험 0.10, 그 외 0.30
            variance = 0.0
            for i, a in enumerate(components):
                for j, b in enumerate(components):
                    ai_safe = CMA.get(a['asset_class'], CMA['기타'])['safe']
                    bj_safe = CMA.get(b['asset_class'], CMA['기타'])['safe']
                    if i == j:
                        corr = 1.0
                    elif ai_safe != bj_safe:
                        corr = 0.10
                    elif a['asset_class'] == b['asset_class']:
                        corr = 0.55
                    else:
                        corr = 0.30
                    variance += a['weight'] * b['weight'] * a['volatility'] * b['volatility'] * corr
            sigma = math.sqrt(max(0.0, variance))
            vol_basis = 'pdf_allocation_plus_cma_covariance'

        fee_pct = ext.get('portfolio_fee_pct')
        if fee_pct is not None and fee_pct > 0:
            mu = mu - float(fee_pct) / 100.0

        safe_proxy = 0.0
        for x in components:
            if x['asset_class'] in {'원리금보장/예금', '현금성'}:
                safe_proxy += x['weight']
            elif x['asset_class'] in {'국내채권', '해외채권', '채권형'}:
                safe_proxy += x['weight'] * 0.70

        return {
            'calculation_basis': 'selected_product_pdf',
            'source_file_id': ext.get('source_file_id'),
            'source_filename': ext.get('source_filename'),
            'extraction_source': ext.get('source'),
            'expected_return': mu,
            'volatility': sigma,
            'return_basis': return_basis,
            'volatility_basis': vol_basis,
            'safe_ratio_proxy': min(1.0, max(0.0, safe_proxy)),
            'components': components,
            'pdf_missing_for_projection': ext.get('missing_for_projection') or [],
            'note': '상품 구성비중은 선택한 공식 PDF에서 추출했습니다. PDF에 없는 미래 기대수익률·변동성은 자산군 CMA 가정으로 보완했습니다.',
        }

    # 추출 실패 시 앱 전체가 죽지 않게 하는 안전 fallback. 보고서/critic에 명확히 표시한다.
    safe_ratio = SAFE_RATIO_BY_TYPE.get(investment_type or '', 0.50)
    return {
        'calculation_basis': 'risk_type_fallback',
        'source_file_id': ext.get('source_file_id'),
        'source_filename': ext.get('source_filename'),
        'extraction_source': ext.get('source'),
        'expected_return': expected_return(safe_ratio),
        'volatility': expected_volatility(safe_ratio),
        'return_basis': 'prototype_risk_type_fallback',
        'volatility_basis': 'prototype_risk_type_fallback',
        'safe_ratio_proxy': safe_ratio,
        'components': [],
        'pdf_missing_for_projection': ext.get('missing_for_projection') or ['상품 구성비중'],
        'note': '상품 PDF 구조화 추출에 실패하여 투자유형 기반 안전 fallback을 사용했습니다. 최종 제출 전 해결이 필요합니다.',
    }


def finance_engine_tool(user: UserPensionInput, product_extraction: dict[str, Any] | None = None) -> dict[str, Any]:
    target = user.desired_monthly_income * 12 / WITHDRAWAL_RATE

    if user.operation_type == 'DB':
        wage = estimate_wage_growth(user)
        benefit, series = _db_benefit_projection(user, wage['rate_pct'])
        retirement_income = user.annual_income * ((1 + wage['rate_pct'] / 100) ** user.years_to_retirement)
        gap = target - benefit
        return {
            'engine_type': 'DB_BENEFIT_ENGINE',
            'target_retirement_asset': round(target, 2),
            'future_asset': round(benefit, 2),
            'estimated_db_benefit': round(benefit, 2),
            'estimated_retirement_annual_income': round(retirement_income, 2),
            'estimated_retirement_monthly_wage_proxy': round(retirement_income / 12, 2),
            'wage_growth_rate_pct': wage['rate_pct'],
            'wage_growth_source': wage['source'],
            'current_tenure_years': user.current_tenure_years,
            'additional_tenure_years': user.expected_additional_tenure_years,
            'total_expected_tenure_years': user.total_expected_tenure_years,
            'gap': round(gap, 2),
            'goal_rate_pct': round(benefit / target * 100, 1),
            'withdrawal_rate_assumption': WITHDRAWAL_RATE,
            'series': series,
            'calculation_note': 'DB 예상급여 간이식: 은퇴시점 월평균임금 proxy × 예상 총 근속연수. 실제 지급액은 평균임금·퇴직연금규약 등에 따라 달라질 수 있습니다.',
        }

    market = product_market_inputs(product_extraction, user.investment_type)
    future, series = project_assets_by_return(user, market['expected_return'])
    gap = target - future
    return {
        'engine_type': 'DC_IRP_PRODUCT_PDF_ENGINE',
        'calculation_basis': market['calculation_basis'],
        'source_file_id': market['source_file_id'],
        'source_filename': market['source_filename'],
        'expected_return': round(market['expected_return'], 6),
        'expected_volatility': round(market['volatility'], 6),
        'return_basis': market['return_basis'],
        'volatility_basis': market['volatility_basis'],
        'product_components': market['components'],
        'target_retirement_asset': round(target, 2),
        'future_asset': round(future, 2),
        'gap': round(gap, 2),
        'goal_rate_pct': round(future / target * 100, 1),
        'withdrawal_rate_assumption': WITHDRAWAL_RATE,
        'series': series,
        'calculation_note': market['note'],
    }


def monte_carlo_tool(
    user: UserPensionInput,
    product_extraction: dict[str, Any] | None = None,
    safe_ratio: float | None = None,
    annual_contribution: float | None = None,
    simulations: int = 2500,
) -> dict[str, Any]:
    target = user.desired_monthly_income * 12 / WITHDRAWAL_RATE
    n = int(max(500, min(simulations, 8000)))

    if user.operation_type == 'DB':
        wage = estimate_wage_growth(user)
        mu = wage['rate_pct'] / 100.0
        sigma = 0.015
        seed_text = f'DB|{user.age}|{user.retirement_age}|{user.annual_income}|{user.current_tenure_years}|{mu}'
        seed = int(hashlib.sha256(seed_text.encode('utf-8')).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        incomes = np.full(n, user.annual_income, dtype=np.float64)
        for _ in range(user.years_to_retirement):
            growth = np.clip(rng.normal(mu, sigma, size=n), -0.05, 0.12)
            incomes *= (1 + growth)
        total_tenure = float(user.total_expected_tenure_years or 0)
        values = (incomes / 12) * total_tenure
        return {
            'simulation_type': 'DB_WAGE_GROWTH',
            'simulations': n,
            'success_probability_pct': round(float(np.mean(values >= target) * 100), 1),
            'p10': round(float(np.percentile(values, 10)), 2),
            'p50': round(float(np.percentile(values, 50)), 2),
            'p90': round(float(np.percentile(values, 90)), 2),
            'assumed_wage_growth_pct': wage['rate_pct'],
            'wage_growth_volatility_pct': round(sigma * 100, 2),
        }

    annual_contribution = (user.annual_contribution or 0) if annual_contribution is None else annual_contribution
    if safe_ratio is None:
        market = product_market_inputs(product_extraction, user.investment_type)
        mu = market['expected_return']
        sigma = market['volatility']
        calculation_basis = market['calculation_basis']
        source_file_id = market['source_file_id']
    else:
        candidate_market = optimizer_candidate_market_inputs(safe_ratio)
        mu = candidate_market['expected_return']
        sigma = candidate_market['volatility']
        calculation_basis = 'optimizer_candidate_allocation_cma'
        source_file_id = None

    seed_text = f'{user.age}|{user.retirement_age}|{user.current_savings}|{annual_contribution}|{mu}|{sigma}|{user.provider}|{user.product_name}'
    seed = int(hashlib.sha256(seed_text.encode('utf-8')).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    values = np.full(n, float(user.current_savings or 0), dtype=np.float64)
    for _ in range(user.years_to_retirement):
        annual_returns = np.clip(rng.normal(mu, sigma, size=n), -0.65, 0.65)
        values = values * (1 + annual_returns) + annual_contribution

    return {
        'simulation_type': 'DC_IRP_MARKET_RETURN',
        'calculation_basis': calculation_basis,
        'source_file_id': source_file_id,
        'simulations': n,
        'success_probability_pct': round(float(np.mean(values >= target) * 100), 1),
        'p10': round(float(np.percentile(values, 10)), 2),
        'p50': round(float(np.percentile(values, 50)), 2),
        'p90': round(float(np.percentile(values, 90)), 2),
        'assumed_return': round(mu, 6),
        'assumed_volatility': round(sigma, 6),
    }


def _allowed_safe_ratio_range(user: UserPensionInput) -> tuple[float, float]:
    base = {
        '안정형': (0.72, 0.97),
        '안정투자형': (0.55, 0.88),
        '중립투자형': (0.32, 0.70),
        '적극투자형': (0.12, 0.50),
    }[user.investment_type]
    low, high = base
    if user.years_to_retirement < 10:
        low = max(low, 0.55)
    elif user.years_to_retirement < 15:
        low = max(low, 0.42)
    return low, high


def _required_contribution(user: UserPensionInput, safe_ratio: float, target: float) -> float:
    current = float(user.annual_contribution or 0)
    lo, hi = 0.0, max(user.annual_income * 0.6, current * 5 + 1)
    for _ in range(50):
        mid = (lo + hi) / 2
        future, _, _ = project_assets(user, safe_ratio, mid)
        if future >= target:
            hi = mid
        else:
            lo = mid
    return hi


def _monthly_saving_for_gap(gap: float, years: int, annual_rate: float = 0.04) -> float:
    if gap <= 0 or years <= 0:
        return 0.0
    monthly_rate = annual_rate / 12
    months = years * 12
    factor = ((1 + monthly_rate) ** months - 1) / monthly_rate if monthly_rate else months
    return gap / factor


def portfolio_optimizer_tool(user: UserPensionInput, product_extraction: dict[str, Any] | None = None) -> dict[str, Any]:
    target = user.desired_monthly_income * 12 / WITHDRAWAL_RATE

    if user.operation_type == 'DB':
        finance = finance_engine_tool(user)
        mc = monte_carlo_tool(user, simulations=1200)
        gap = max(0.0, finance['gap'])
        monthly_supplement = _monthly_saving_for_gap(gap, user.years_to_retirement, 0.04)
        wage = finance['wage_growth_rate_pct']
        scenarios = []
        for label, rate in [('보수적', max(0.5, wage - 1.0)), ('기준', wage), ('낙관적', min(8.0, wage + 1.0))]:
            benefit, _ = _db_benefit_projection(user, rate)
            scenarios.append({'label': label, 'wage_growth_rate_pct': round(rate, 2), 'estimated_db_benefit': round(benefit, 2), 'goal_rate_pct': round(benefit / target * 100, 1)})
        return {
            'analysis_type': 'DB_GAP_ANALYZER',
            'portfolio_optimization_applicable': False,
            'recommended_safe_ratio': None,
            'recommended_allocation': {},
            'future_asset': finance['future_asset'],
            'goal_rate_pct': finance['goal_rate_pct'],
            'success_probability_pct': mc['success_probability_pct'],
            'supplementary_asset_gap': round(gap, 2),
            'supplementary_monthly_saving_needed': round(monthly_supplement, 2),
            'series': finance['series'],
            'counterfactuals': scenarios,
            'note': 'DB형은 개인 포트폴리오 최적화 대신 예상 DB 급여와 희망 노후소득의 Gap을 분석합니다.',
        }

    current_market = product_market_inputs(product_extraction, user.investment_type)
    current_future, _ = project_assets_by_return(user, current_market['expected_return'])
    current_safe = current_market['safe_ratio_proxy']

    low, high = _allowed_safe_ratio_range(user)
    candidates = []
    for safe_ratio in np.arange(low, high + 0.001, 0.05):
        safe_ratio = float(round(safe_ratio, 4))
        market = optimizer_candidate_market_inputs(safe_ratio)
        future, series, r = project_assets(user, safe_ratio)
        mc = monte_carlo_tool(user, product_extraction=product_extraction, safe_ratio=safe_ratio, simulations=900)
        success = mc['success_probability_pct']
        vol = market['volatility']
        distance_penalty = abs(safe_ratio - current_safe) * 18
        shortfall_penalty = max(0, 75 - success) * 0.9
        risk_penalty = vol * 70
        score = shortfall_penalty + risk_penalty + distance_penalty
        candidates.append({
            'score': score, 'safe_ratio': safe_ratio, 'future': future, 'series': series,
            'expected_return': r, 'volatility': vol, 'mc': mc,
            'score_breakdown': {
                'shortfall_penalty': shortfall_penalty,
                'risk_penalty': risk_penalty,
                'distance_penalty': distance_penalty,
            },
            'allocation': market['allocation'],
        })

    candidates.sort(key=lambda x: x['score'])
    best = candidates[0]
    safe_ratio = best['safe_ratio']
    future = best['future']
    series = best['series']
    r = best['expected_return']
    mc = best['mc']
    required = _required_contribution(user, safe_ratio, target)
    current_safe_pct = round(current_safe * 100, 1)
    recommended_safe_pct = round(safe_ratio * 100, 1)
    if recommended_safe_pct > current_safe_pct + 0.05:
        allocation_direction = '안전자산 비중 확대'
    elif recommended_safe_pct < current_safe_pct - 0.05:
        allocation_direction = '안전자산 비중 축소'
    else:
        allocation_direction = '안전자산 비중 유지'

    return {
        'analysis_type': 'DC_IRP_PORTFOLIO_OPTIMIZER',
        'portfolio_optimization_applicable': True,
        'current_product_calculation_basis': current_market['calculation_basis'],
        'current_product_safe_ratio_proxy': current_safe_pct,
        'recommended_safe_ratio': recommended_safe_pct,
        'recommended_allocation': best['allocation'],
        'allocation_direction': allocation_direction,
        'candidate_safe_ratio_range_pct': [round(low * 100, 1), round(high * 100, 1)],
        'optimization_objective': '목표달성확률 부족, 후보 변동성, 현재 상품과의 자산배분 거리의 가중 패널티를 최소화',
        'selected_score': round(best['score'], 4),
        'selected_score_breakdown': {k: round(v, 4) for k, v in best['score_breakdown'].items()},
        'expected_return': round(r, 5),
        'expected_volatility': round(best['volatility'], 6),
        'future_asset': round(future, 2),
        'goal_rate_pct': round(future / target * 100, 1),
        'success_probability_pct': mc['success_probability_pct'],
        'required_annual_contribution_for_expected_target': round(required, 2),
        'additional_annual_contribution_needed': round(max(0, required - (user.annual_contribution or 0)), 2),
        'expected_asset_improvement_vs_current': round(future - current_future, 2),
        'series': series,
        'counterfactuals': [
            {'label': '현재 납입액 유지 + AI 후보 자산배분', 'future_asset': round(future, 2), 'goal_rate_pct': round(future / target * 100, 1)},
            {'label': 'AI 후보 자산배분 + 목표 기대값 달성 납입액', 'annual_contribution': round(required, 2), 'additional_annual_contribution': round(max(0, required - (user.annual_contribution or 0)), 2)},
        ],
        'note': '현재 가입상품의 기준선은 선택 PDF에서 추출한 실제 구성비중으로 계산합니다. 최적화 후보는 화면에 표시되는 원리금보장/현금성·채권형·성장형 비중과 동일한 CMA 입력으로 기대수익률·변동성·몬테카를로를 계산합니다.',
    }
