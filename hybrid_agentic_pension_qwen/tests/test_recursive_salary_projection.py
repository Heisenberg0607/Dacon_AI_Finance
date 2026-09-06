from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import app
from backend.models import UserPensionInput
from backend.tools import finance_engine_tool, monte_carlo_tool, portfolio_optimizer_tool
from services.salary_growth.predictor import SalaryGrowthArtifactError, SalaryGrowthPredictor, SalaryGrowthRequiredError
from services.salary_growth.projector import SalaryGrowthProjectionUnsupported, SalaryGrowthProjector


@pytest.fixture
def predictions(monkeypatch):
    calls = []

    def predict(self, current_age, current_salary, occupation):
        calls.append((current_age, current_salary, occupation))
        return {'predicted_growth_rate': 4.0 if current_age == 32 else 2.0}

    monkeypatch.setattr(SalaryGrowthPredictor, 'predict', predict)
    return calls


@pytest.mark.parametrize('override', [None, 3.123, -1.234])
def test_recursive_inputs_and_first_block_override(predictions, override):
    projector = SalaryGrowthProjector()
    result = projector.project(32, 40, 5000, '213.0', override)
    blocks = result['blocks']
    assert [c[0] for c in predictions] == [32, 35, 38]
    weight = projector.config.model_weight(0, first_block=True)
    anchor = projector.predictor.age_curve.growth_for_age(32)['growth']
    first_rate = weight * 4.0 + (1 - weight) * anchor if override is None else override
    assert blocks[0]['final_growth'] == pytest.approx(first_rate, abs=1e-6)
    assert predictions[1][1] == pytest.approx(5000 * (1 + first_rate / 100) ** 3)
    assert predictions[2][1] == pytest.approx(blocks[1]['end_salary'], abs=0.005)
    assert blocks[1]['catboost_growth'] == 2.0
    assert blocks[1]['growth_source'] == 'catboost_m3'
    assert blocks[1]['model_weight'] == pytest.approx(projector.config.model_weight(3), abs=5e-5)
    assert blocks[-1]['block_years'] == 2
    assert len(result['salary_path']) == 9
    assert result['salary_path'][-1]['age'] == 40
    assert result['projection_supported'] is True
    assert result['provisional'] is True


def test_unknown_target_still_rejected(predictions):
    predictor = SalaryGrowthPredictor()
    predictor.metadata = deepcopy(predictor.metadata)
    predictor.metadata['target']['name'] = 'unknown'
    with pytest.raises(SalaryGrowthProjectionUnsupported):
        SalaryGrowthProjector(predictor).project(32, 40, 5000, '213.0')
    assert predictions == []


def test_project_api_accepts_manual_first_block(predictions):
    response = TestClient(app).post('/api/salary-growth/project', json={
        'current_age': 32, 'retirement_age': 40, 'current_salary': 5000,
        'occupation': '213.0', 'initial_growth_override': 3.123,
    })
    assert response.status_code == 200
    assert response.json()['blocks'][0]['final_growth'] == 3.123
    assert response.json()['blocks'][1]['growth_source'] == 'catboost_m3'
    assert response.json()['projection_method'] == 'recursive_constant_annual_rate_scenario'


def test_project_api_accepts_three_decimal_override_and_recursively_repredicts(predictions):
    response = TestClient(app).post('/api/salary-growth/project', json={
        'current_age': 32, 'retirement_age': 41, 'current_salary': 5000,
        'occupation': '213.0', 'initial_growth_override': 5.321,
    })
    assert response.status_code == 200
    result = response.json()
    assert result['blocks'][0]['final_growth'] == 5.321
    assert [call[0] for call in predictions] == [32, 35, 38]
    assert result['blocks'][0]['growth_source'] == 'user_first_block_override'
    assert result['blocks'][1]['growth_source'] == 'catboost_m3'
    assert result['blocks'][2]['growth_source'] == 'catboost_m3'
    assert result['blocks'][1]['start_salary'] == pytest.approx(
        5000 * (1 + 5.321 / 100) ** 3,
        abs=0.01,
    )


@pytest.mark.parametrize('operation', ['DB', 'DC'])
def test_finance_uses_recursive_salary_path(predictions, operation):
    user = UserPensionInput.model_validate({
        'age': 32, 'retirement_age': 40, 'annual_income': 5000,
        'desired_monthly_income': 250, 'operation_type': operation,
        'current_tenure_years': 3, 'wage_growth_rate': 3.123,
        'current_savings': 1000, 'annual_contribution': 360,
        'personal_additional_contribution': 120,
        'provider': '삼성증권', 'product_name': '테스트 상품',
        'investment_type': '중립투자형', 'industry_job': '분석가',
    })
    result = finance_engine_tool(user)
    assert len(result['salary_projection']['blocks']) == 3
    if operation == 'DB':
        assert result['calculation_basis'] == 'salary_growth_projector'
        assert result['first_3y_wage_growth_rate_pct'] == 3.123
        assert result['estimated_retirement_annual_income'] == result['salary_projection']['projected_salary_at_retirement']
    else:
        assert result['contribution_projection_basis'] == 'salary_path'
        assert result['series'][-1]['annual_contribution'] > result['series'][1]['annual_contribution']


def make_user(operation='DC'):
    return UserPensionInput.model_validate({
        'age': 32, 'retirement_age': 62, 'annual_income': 5000,
        'desired_monthly_income': 250, 'operation_type': operation,
        'current_tenure_years': 3, 'wage_growth_rate': 3.123,
        'current_savings': 1000, 'annual_contribution': 360,
        'personal_additional_contribution': 120,
        'provider': '삼성증권', 'product_name': '테스트 상품',
        'investment_type': '중립투자형', 'industry_job': '분석가',
    })


@pytest.mark.parametrize('operation', ['DB', 'DC'])
def test_analysis_reuses_one_salary_path(predictions, operation):
    user = make_user(operation)
    original_payload = user.model_dump()
    finance = finance_engine_tool(user)
    mc = monte_carlo_tool(user)
    optimized = portfolio_optimizer_tool(user)
    assert len(predictions) == 10  # All tools and optimizer candidates combined.
    assert user.model_dump() == original_payload
    # Reuse must preserve numerical outputs.
    assert finance_engine_tool(user) == finance
    assert monte_carlo_tool(user) == mc
    assert portfolio_optimizer_tool(user) == optimized
    assert len(predictions) == 10


@pytest.mark.parametrize('field, value', [
    ('age', 33), ('retirement_age', 65), ('annual_income', 6000),
    ('industry_job', '개발자'), ('wage_growth_rate', 2.345),
])
def test_salary_inputs_invalidate_cache(predictions, field, value):
    user = make_user()
    finance_engine_tool(user)
    previous_count = len(predictions)
    scenario = user.model_copy(update={field: value})
    finance_engine_tool(scenario)
    assert len(predictions) > previous_count


def test_separate_requests_do_not_share_salary_cache(predictions):
    finance_engine_tool(make_user())
    finance_engine_tool(make_user())
    assert len(predictions) == 20


def test_dc_chart_series_uses_age_adjusted_salary_in_both_lines(predictions):
    user = make_user()
    finance = finance_engine_tool(user)
    optimized = portfolio_optimizer_tool(user)
    projection = finance['salary_projection']
    assert 0 <= projection['blocks'][1]['model_weight'] <= 1
    assert projection['blocks'][1]['final_growth'] == pytest.approx(
        projection['blocks'][1]['model_weight'] * 2.0
        + (1 - projection['blocks'][1]['model_weight']) * projection['blocks'][1]['age_curve_growth'],
        abs=0.001,
    )
    for result in (finance, optimized):
        assert result['contribution_projection_basis'] == 'salary_path'
        points = result['series']
        for previous, point, salary in zip(points, points[1:], projection['salary_path'][1:]):
            contribution = 360 * salary['salary'] / 5000 + 120
            assert point['annual_contribution'] == round(contribution, 2)
            assert point['value'] == pytest.approx(
                previous['value'] * (1 + result['expected_return']) + contribution,
                # Optimizer serializes return to 5 decimals; assets to 2 decimals.
                abs=abs(previous['value']) * 0.000005 + 0.02,
            )
        increments = [b['value'] - a['value'] for a, b in zip(points, points[1:])]
        assert max(increments) - min(increments) > 1  # Chart data is not a straight line.
    assert len(predictions) == 10


@pytest.mark.parametrize('operation', ['DB', 'DC'])
def test_unavailable_api_is_not_retried_per_candidate(monkeypatch, operation):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise SalaryGrowthArtifactError('Test API timeout')

    monkeypatch.setattr(SalaryGrowthPredictor, 'predict', unavailable)
    user = make_user(operation)
    for tool in (finance_engine_tool, monte_carlo_tool, portfolio_optimizer_tool):
        with pytest.raises(SalaryGrowthRequiredError):
            tool(user)
    assert len(calls) == 1


def test_mid_path_failure_extends_last_successful_rate(monkeypatch):
    calls = []

    def predict(self, age, salary, occupation):
        calls.append(age)
        if age >= 38:
            raise SalaryGrowthArtifactError('connection reset midway')
        return {'predicted_growth_rate': 3.123}

    monkeypatch.setattr(SalaryGrowthPredictor, 'predict', predict)
    user = make_user()
    result = finance_engine_tool(user)
    optimized = portfolio_optimizer_tool(user)
    assert calls == [32, 35, 38]
    projection = result['salary_projection']
    assert projection == optimized['salary_projection']
    assert projection['continuation']['from_age'] == 38
    rate = projection['continuation']['rate_pct']
    assert rate == pytest.approx(projection['blocks'][1]['final_growth'], abs=1e-6)
    for block in projection['blocks'][2:]:
        assert block['growth_source'] == 'last_successful_rate'
        assert block['final_growth'] == pytest.approx(rate, abs=1e-6)
        assert block['raw_catboost_growth'] is None
    assert projection['salary_path'][-1]['age'] == 62
    assert '38세 이후' in result['calculation_note']
