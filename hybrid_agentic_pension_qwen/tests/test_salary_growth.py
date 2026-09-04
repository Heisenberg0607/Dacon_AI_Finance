from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from backend.models import UserPensionInput
from backend.tools import finance_engine_tool
from services.salary_growth.predictor import get_salary_growth_predictor
from services.salary_growth.projector import SalaryGrowthProjectionUnsupported, SalaryGrowthProjector


@pytest.fixture(scope='module')
def predictor():
    return get_salary_growth_predictor()


def test_artifact_load_and_feature_order(predictor):
    checks = predictor.validate_artifacts(include_model_load=True)
    assert checks['ok'] is True
    assert checks['feature_order'] == ['log_wage_t', 'age', 'occupation']
    assert checks['feature_order_matches_metadata'] is True
    assert checks['smoke_test_reload_identical'] is True


def test_predict_supported_occupation_matches_smoke_test(predictor):
    result = predictor.predict(current_age=32, current_salary=5000, occupation='-1.0')
    expected = predictor.smoke_test['sample_prediction']['prediction_avg_annual_growth_pp']
    assert result['prediction_horizon_years'] == 3
    assert result['predicted_growth_rate'] == pytest.approx(expected, abs=1e-6)
    assert result['projection_supported'] is False
    assert result['occupation_mapping']['category'] == '-1.0'


def test_prediction_is_deterministic_after_reload(predictor):
    first = predictor.predict(current_age=32, current_salary=5000, occupation='-1.0')
    get_salary_growth_predictor.cache_clear()
    reloaded = get_salary_growth_predictor().predict(current_age=32, current_salary=5000, occupation='-1.0')
    assert reloaded['predicted_growth_rate'] == pytest.approx(first['predicted_growth_rate'], abs=1e-12)


def test_unknown_natural_language_occupation_falls_back(predictor):
    result = predictor.predict(current_age=32, current_salary=5000, occupation='not-a-category')
    assert result['occupation_mapping']['category'] == '-1.0'
    assert result['occupation_mapping']['fallback'] is True


def test_natural_language_occupation_keyword_mapping(predictor):
    result = predictor.predict(current_age=32, current_salary=5000, occupation='금융 데이터 분석가')
    assert result['occupation_mapping']['source'] == 'keyword_mapping'
    assert result['occupation_mapping']['category'] in predictor.occupation_categories
    assert result['occupation_mapping']['fallback'] is False


def test_current_salary_validation(predictor):
    with pytest.raises(ValueError):
        predictor.predict(current_age=32, current_salary=0, occupation='-1.0')


def test_projection_rejects_non_cagr_target(predictor):
    with pytest.raises(SalaryGrowthProjectionUnsupported):
        SalaryGrowthProjector(predictor).project(
            current_age=32,
            retirement_age=60,
            current_salary=5000,
            occupation='-1.0',
        )


def test_project_retirement_age_validation(predictor):
    with pytest.raises(ValueError):
        SalaryGrowthProjector(predictor).project(32, 32, 5000, '-1.0')


def test_api_predict_and_project():
    client = TestClient(app)
    health_response = client.get('/api/health')
    assert health_response.status_code == 200
    assert health_response.json()['salary_growth']['ok'] is True

    predict_response = client.post('/api/salary-growth/predict', json={
        'current_age': 32,
        'current_salary': 5000,
        'occupation': '금융 데이터 분석가',
    })
    assert predict_response.status_code == 200
    assert predict_response.json()['model'] == 'catboost_m3'
    assert predict_response.json()['occupation_mapping']['source'] == 'keyword_mapping'

    project_response = client.post('/api/salary-growth/project', json={
        'current_age': 32,
        'retirement_age': 60,
        'current_salary': 5000,
        'occupation': '-1.0',
    })
    assert project_response.status_code == 409
    assert 'CAGR' in project_response.json()['detail']


def test_db_finance_falls_back_when_projection_target_is_not_cagr():
    user = UserPensionInput.model_validate({
        'age': 32,
        'retirement_age': 60,
        'annual_income': 5000,
        'desired_monthly_income': 250,
        'operation_type': 'DB',
        'current_tenure_years': 3,
        'industry_job': '금융 데이터 분석가',
    })
    result = finance_engine_tool(user)
    assert result['calculation_basis'] == 'wage_growth_rate_fallback'
    assert result['salary_projection'] is None
    assert 'fallback' in result['calculation_note']


def test_dc_finance_keeps_fixed_contribution_without_supported_salary_path():
    user = UserPensionInput.model_validate({
        'age': 32,
        'retirement_age': 60,
        'annual_income': 5000,
        'desired_monthly_income': 250,
        'operation_type': 'DC',
        'current_savings': 1000,
        'annual_contribution': 360,
        'personal_additional_contribution': 120,
        'provider': '삼성증권',
        'product_name': '테스트 상품',
        'investment_type': '중립투자형',
        'industry_job': '금융 데이터 분석가',
    })
    result = finance_engine_tool(user)
    assert result['salary_projection'] is None
    assert result['contribution_projection_basis'] == 'fixed_annual_contribution'
