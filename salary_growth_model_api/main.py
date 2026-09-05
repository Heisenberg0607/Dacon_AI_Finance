from __future__ import annotations

import hmac
import math
import os
from pathlib import Path
from typing import Any

from catboost import CatBoostRegressor
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


MODEL_PATH = Path(os.getenv('MODEL_PATH', 'models/catboost_m3.cbm'))
MODEL_API_KEY = os.getenv('MODEL_API_KEY', '').strip()

EXPECTED_FEATURES = ['log_wage_t', 'age', 'occupation']


def _load_model() -> CatBoostRegressor:
    if not MODEL_PATH.exists():
        raise RuntimeError(
            f'CatBoost model not found: {MODEL_PATH}. '
            'Run copy_model.bat (Windows) before deploying this service.'
        )

    model = CatBoostRegressor()
    model.load_model(str(MODEL_PATH))

    feature_names = list(getattr(model, 'feature_names_', []) or [])
    if feature_names and feature_names != EXPECTED_FEATURES:
        raise RuntimeError(
            f'Unexpected model feature order: {feature_names}; expected {EXPECTED_FEATURES}'
        )

    return model


MODEL = _load_model()

app = FastAPI(
    title='KKAEUM Salary Growth Model API',
    version='1.0.0',
)


class PredictRequest(BaseModel):
    log_wage_t: float
    age: int = Field(ge=17, le=90)
    occupation: str


def _authorize(received_key: str | None) -> None:
    # Local testing can leave MODEL_API_KEY empty.
    # In production, set it and use the same value as Vercel SALARY_GROWTH_API_KEY.
    if not MODEL_API_KEY:
        return

    if not hmac.compare_digest(received_key or '', MODEL_API_KEY):
        raise HTTPException(status_code=401, detail='Invalid API key')


@app.get('/health')
def health(
    x_api_key: str | None = Header(default=None, alias='X-API-Key'),
) -> dict[str, Any]:
    _authorize(x_api_key)
    return {
        'ok': True,
        'model_loaded': True,
        'model': 'catboost_m3',
        'model_path': MODEL_PATH.name,
        'feature_order': EXPECTED_FEATURES,
    }


@app.post('/predict')
def predict(
    request: PredictRequest,
    x_api_key: str | None = Header(default=None, alias='X-API-Key'),
) -> dict[str, Any]:
    _authorize(x_api_key)

    row = [[
        float(request.log_wage_t),
        int(request.age),
        str(request.occupation),
    ]]

    try:
        prediction = float(MODEL.predict(row)[0])
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f'CatBoost prediction failed: {type(exc).__name__}: {exc}',
        ) from exc

    if not math.isfinite(prediction):
        raise HTTPException(status_code=500, detail='Model returned non-finite prediction')

    return {
        'prediction': prediction,
        'model': 'catboost_m3',
    }
