from __future__ import annotations

import math
from typing import Any

from .config import SalaryProjectionConfig
from .predictor import SalaryGrowthArtifactError, SalaryGrowthPredictor, get_salary_growth_predictor


class SalaryGrowthProjectionUnsupported(ValueError):
    pass


class SalaryGrowthProjector:
    def __init__(self, predictor: SalaryGrowthPredictor | None = None):
        self.predictor = predictor or get_salary_growth_predictor()
        self.config = SalaryProjectionConfig.from_metadata(self.predictor.metadata)

    def _model_weight(self, years_from_now: int | float, block_index: int) -> float:
        return self.config.model_weight(years_from_now, first_block=block_index == 0)

    def _assert_projection_supported(self) -> None:
        target = self.predictor.metadata.get('target', {})
        if not self.predictor.projection_supported:
            warning = target.get('projection_warning') or 'salary projection requires a CAGR-compatible target'
            raise SalaryGrowthProjectionUnsupported(warning)

    def project(
        self,
        current_age: int,
        retirement_age: int,
        current_salary: float,
        occupation: str,
        initial_growth_override: float | None = None,
    ) -> dict[str, Any]:
        if retirement_age <= current_age:
            raise ValueError('retirement_age must be greater than current_age')
        if retirement_age > 90:
            raise ValueError('retirement_age must be less than or equal to 90')
        self._assert_projection_supported()
        if not math.isfinite(current_salary) or current_salary <= 0:
            raise ValueError('current_salary must be finite and greater than 0')
        if initial_growth_override is not None and (
            not math.isfinite(initial_growth_override) or not -5 <= initial_growth_override <= 20
        ):
            raise ValueError('initial_growth_override must be between -5 and 20')

        age = int(current_age)
        salary = float(current_salary)
        blocks: list[dict[str, Any]] = []
        salary_path = [{'age': age, 'salary': round(salary, 2)}]
        block_index = 0
        continuation = None
        last_growth = None

        while age < retirement_age:
            years_from_now = age - int(current_age)
            block_years = min(self.config.block_years, retirement_age - age)
            if continuation is None:
                try:
                    prediction = self.predictor.predict(age, salary, occupation)
                    model_growth = float(prediction['predicted_growth_rate'])
                    raw_model_growth = float(prediction.get('raw_prediction', model_growth))
                except SalaryGrowthArtifactError:
                    if last_growth is None:
                        raise
                    continuation = {
                        'from_age': age,
                        'last_success_age': blocks[-1]['start_age'],
                        'rate_pct': last_growth,
                        'note': f'{age}세 이후는 모델 API 예측 실패로 마지막 성공 구간의 적용 상승률 {last_growth:.3f}%를 유지했습니다. 이후 재예측·연령 커브 보정은 적용하지 않았습니다.',
                    }
            catboost_growth = (
                float(initial_growth_override)
                if block_index == 0 and initial_growth_override is not None
                else model_growth
            )
            age_curve = self.predictor.age_curve.growth_for_age(age)
            age_curve_growth = float(age_curve['growth'])
            model_weight = self._model_weight(years_from_now, block_index)
            # Preserve the existing explicit first-block override contract.
            if block_index == 0 and initial_growth_override is not None:
                model_weight = 1.0
            final_growth = model_weight * catboost_growth + (1.0 - model_weight) * age_curve_growth
            if continuation is not None:
                final_growth = last_growth
            annual_rate = final_growth / 100.0
            if not math.isfinite(annual_rate) or annual_rate <= -1:
                raise SalaryGrowthProjectionUnsupported('Predicted annual growth must be finite and greater than -100%')
            end_salary = salary * ((1.0 + annual_rate) ** block_years)
            start_age = age
            end_age = age + block_years

            for offset in range(1, block_years + 1):
                yearly_salary = salary * ((1.0 + annual_rate) ** offset)
                salary_path.append({'age': start_age + offset, 'salary': round(yearly_salary, 2)})

            blocks.append({
                'start_age': start_age,
                'end_age': end_age,
                'block_years': block_years,
                'start_salary': round(salary, 2),
                'catboost_growth': round(catboost_growth, 6),
                'raw_catboost_growth': None if continuation else round(raw_model_growth, 6),
                'growth_source': 'last_successful_rate' if continuation else ('user_first_block_override' if block_index == 0 and initial_growth_override is not None else 'catboost_m3'),
                'age_curve_growth': round(age_curve_growth, 6),
                'age_curve_matched_age': age_curve['matched_age'],
                'age_curve_clamped': age_curve['clamped'],
                'years_from_now': years_from_now,
                'model_weight': None if continuation else round(model_weight, 4),
                'final_growth': round(final_growth, 6),
                'end_salary': round(end_salary, 2),
            })

            age = end_age
            salary = end_salary
            last_growth = final_growth
            block_index += 1

        target = self.predictor.metadata.get('target', {})
        return {
            'current_salary': round(float(current_salary), 2),
            'current_age': int(current_age),
            'retirement_age': int(retirement_age),
            'projected_salary_at_retirement': round(salary, 2),
            'model': 'catboost_m3',
            'model_version': self.predictor.metadata.get('model_version'),
            'projection_supported': self.predictor.projection_supported,
            'projection_method': target.get('projection_method', 'recursive_cagr'),
            'blending_validated': self.config.blending_validated,
            'provisional': self.config.provisional,
            'blending_config': self.config.as_dict(),
            'projection_warning': target.get('projection_warning'),
            'continuation': continuation,
            'blocks': blocks,
            'salary_path': salary_path,
        }
