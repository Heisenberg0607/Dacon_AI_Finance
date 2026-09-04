from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from services.salary_growth.predictor import get_salary_growth_predictor
from services.salary_growth.projector import SalaryGrowthProjector


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


def test_prediction_is_deterministic_after_reload(predictor):
    first = predictor.predict(current_age=32, current_salary=5000, occupation='-1.0')
    get_salary_growth_predictor.cache_clear()
    reloaded = get_salary_growth_predictor().predict(current_age=32, current_salary=5000, occupation='-1.0')
    assert reloaded['predicted_growth_rate'] == pytest.approx(first['predicted_growth_rate'], abs=1e-12)


def test_unsupported_occupation_errors(predictor):
    with pytest.raises(ValueError):
        predictor.predict(current_age=32, current_salary=5000, occupation='not-a-category')


def test_current_salary_validation(predictor):
    with pytest.raises(ValueError):
        predictor.predict(current_age=32, current_salary=0, occupation='-1.0')


def test_projection_blocks_and_final_short_block(predictor):
    result = SalaryGrowthProjector(predictor).project(
        current_age=32,
        retirement_age=60,
        current_salary=5000,
        occupation='-1.0',
    )
    assert result['projection_supported'] is False
    assert result['blending_validated'] is False
    assert result['provisional'] is True
    assert result['blocks'][0]['block_years'] == 3
    assert result['blocks'][-1]['block_years'] == 1
    assert result['blocks'][-1]['end_age'] == 60
    assert result['salary_path'][0] == {'age': 32, 'salary': 5000.0}
    assert result['salary_path'][-1]['age'] == 60


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
        'occupation': '-1.0',
    })
    assert predict_response.status_code == 200
    assert predict_response.json()['model'] == 'catboost_m3'

    project_response = client.post('/api/salary-growth/project', json={
        'current_age': 32,
        'retirement_age': 60,
        'current_salary': 5000,
        'occupation': '-1.0',
    })
    assert project_response.status_code == 200
    body = project_response.json()
    assert body['blocks'][-1]['end_age'] == 60
    assert body['salary_path'][-1]['age'] == 60
