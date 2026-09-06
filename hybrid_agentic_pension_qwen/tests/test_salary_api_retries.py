import httpx
import pytest
from fastapi.testclient import TestClient

from app import app, workflow
from services.salary_growth.predictor import SalaryGrowthPredictor, SalaryGrowthArtifactError, SalaryGrowthRequiredError


@pytest.mark.parametrize('failure', ['reset', 503, 429])
def test_retries_use_new_connection_and_identical_inputs(monkeypatch, failure):
    real_client = httpx.Client
    calls, clients = [], []

    def respond(request):
        calls.append(request.content)
        if len(calls) == 1:
            if failure == 'reset':
                raise httpx.ReadError('connection reset', request=request)
            return httpx.Response(failure)
        return httpx.Response(200, json={'prediction': 3.123456})

    def client(**kwargs):
        instance = real_client(transport=httpx.MockTransport(respond))
        clients.append(instance)
        return instance

    monkeypatch.setattr(httpx, 'Client', client)
    monkeypatch.setattr('services.salary_growth.predictor.time.sleep', lambda _: None)
    predictor = SalaryGrowthPredictor()
    predictor.api_url = 'https://model.test'
    assert predictor._remote_predict(6.0, 32, '213.0') == 3.123456
    assert len(clients) == 2
    assert calls[0] == calls[1]
    for instance in clients:
        instance.close()


@pytest.mark.parametrize('status, expected_attempts', [(503, 3), (401, 1), (422, 1)])
def test_retry_limit_and_non_retryable_errors(monkeypatch, status, expected_attempts):
    real_client = httpx.Client
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status)

    monkeypatch.setattr(httpx, 'Client', lambda **_: real_client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr('services.salary_growth.predictor.time.sleep', lambda _: None)
    predictor = SalaryGrowthPredictor()
    predictor.api_url = 'https://model.test'
    with pytest.raises(SalaryGrowthArtifactError):
        predictor._remote_predict(6.0, 32, '213.0')
    assert len(calls) == expected_attempts
    predictor._prediction_client.close()


def test_failed_prediction_does_not_return_report(monkeypatch):
    def fail(*args, **kwargs):
        raise SalaryGrowthRequiredError()

    monkeypatch.setattr(workflow, 'run', fail)
    payload = {'age': 32, 'retirement_age': 60, 'annual_income': 5000,
               'desired_monthly_income': 250, 'operation_type': 'DB', 'current_tenure_years': 3}
    client = TestClient(app)
    response = client.post('/api/analyze', json=payload)
    assert response.status_code == 503
    assert '대체값으로 계산하지 않았습니다' in response.json()['detail']
    stream = client.post('/api/analyze/stream', json=payload)
    assert '"type": "error"' in stream.text
    assert '"type": "result"' not in stream.text
