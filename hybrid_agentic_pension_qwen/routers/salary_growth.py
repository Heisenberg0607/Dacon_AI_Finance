from __future__ import annotations

from fastapi import APIRouter, HTTPException

from schemas.salary_growth import (
    SalaryGrowthPredictRequest,
    SalaryGrowthPredictResponse,
    SalaryGrowthProjectRequest,
    SalaryGrowthProjectResponse,
)
from services.salary_growth.predictor import SalaryGrowthArtifactError, get_salary_growth_predictor
from services.salary_growth.projector import SalaryGrowthProjector


router = APIRouter(prefix='/api/salary-growth', tags=['salary-growth'])


@router.get('/health')
def salary_growth_health(load_model: bool = False):
    try:
        predictor = get_salary_growth_predictor()
        return predictor.validate_artifacts(include_model_load=load_model)
    except SalaryGrowthArtifactError as exc:
        return {'ok': False, 'error': str(exc)}


@router.post('/predict', response_model=SalaryGrowthPredictResponse)
def predict_salary_growth(request: SalaryGrowthPredictRequest):
    try:
        return get_salary_growth_predictor().predict(
            current_age=request.current_age,
            current_salary=request.current_salary,
            occupation=request.occupation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SalaryGrowthArtifactError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post('/project', response_model=SalaryGrowthProjectResponse)
def project_salary_growth(request: SalaryGrowthProjectRequest):
    try:
        projector = SalaryGrowthProjector(get_salary_growth_predictor())
        return projector.project(
            current_age=request.current_age,
            retirement_age=request.retirement_age,
            current_salary=request.current_salary,
            occupation=request.occupation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SalaryGrowthArtifactError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
