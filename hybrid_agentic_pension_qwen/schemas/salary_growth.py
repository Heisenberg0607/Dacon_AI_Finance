from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class SalaryGrowthPredictRequest(BaseModel):
    current_age: int = Field(ge=17, le=90)
    current_salary: float = Field(gt=0, description='Current annual salary, in manwon')
    occupation: str = Field(default='', max_length=120)


class SalaryGrowthPredictResponse(BaseModel):
    model: str
    model_name: str | None = None
    model_version: str | None = None
    prediction_horizon_years: int
    predicted_growth_rate: float
    target_unit: str | None = None
    target_definition: str | None = None
    target_type: str | None = None
    projection_supported: bool
    occupation_mapping: dict
    input_features: dict


class SalaryGrowthProjectRequest(SalaryGrowthPredictRequest):
    retirement_age: int = Field(ge=18, le=90)

    @model_validator(mode='after')
    def validate_retirement_age(self):
        if self.retirement_age <= self.current_age:
            raise ValueError('retirement_age must be greater than current_age')
        return self


class SalaryProjectionBlock(BaseModel):
    start_age: int
    end_age: int
    block_years: int
    start_salary: float
    catboost_growth: float
    raw_catboost_growth: float
    growth_source: str
    age_curve_growth: float
    age_curve_matched_age: int
    age_curve_clamped: bool
    years_from_now: int
    model_weight: float
    final_growth: float
    end_salary: float


class SalaryPathPoint(BaseModel):
    age: int
    salary: float


class SalaryGrowthProjectResponse(BaseModel):
    current_salary: float
    current_age: int
    retirement_age: int
    projected_salary_at_retirement: float
    model: str
    model_version: str | None = None
    projection_supported: bool
    blending_validated: bool
    provisional: bool
    blending_config: dict
    projection_warning: str | None = None
    blocks: list[SalaryProjectionBlock]
    salary_path: list[SalaryPathPoint]
