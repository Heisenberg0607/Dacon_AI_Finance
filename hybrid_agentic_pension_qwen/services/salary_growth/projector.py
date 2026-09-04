from __future__ import annotations

from typing import Any

from .predictor import SalaryGrowthPredictor, get_salary_growth_predictor


class SalaryGrowthProjector:
    def __init__(self, predictor: SalaryGrowthPredictor | None = None):
        self.predictor = predictor or get_salary_growth_predictor()

    @staticmethod
    def _model_weight(block_index: int) -> float:
        return max(0.35, 0.75 - block_index * 0.10)

    def project(
        self,
        current_age: int,
        retirement_age: int,
        current_salary: float,
        occupation: str,
    ) -> dict[str, Any]:
        if retirement_age <= current_age:
            raise ValueError('retirement_age must be greater than current_age')
        if retirement_age > 90:
            raise ValueError('retirement_age must be less than or equal to 90')

        age = int(current_age)
        salary = float(current_salary)
        blocks: list[dict[str, Any]] = []
        salary_path = [{'age': age, 'salary': round(salary, 2)}]
        block_index = 0

        while age < retirement_age:
            block_years = min(3, retirement_age - age)
            prediction = self.predictor.predict(age, salary, occupation)
            catboost_growth = float(prediction['predicted_growth_rate'])
            age_curve = self.predictor.age_curve.growth_for_age(age)
            age_curve_growth = float(age_curve['growth'])
            model_weight = self._model_weight(block_index)
            final_growth = model_weight * catboost_growth + (1.0 - model_weight) * age_curve_growth
            annual_rate = final_growth / 100.0
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
                'age_curve_growth': round(age_curve_growth, 6),
                'age_curve_matched_age': age_curve['matched_age'],
                'age_curve_clamped': age_curve['clamped'],
                'model_weight': round(model_weight, 4),
                'final_growth': round(final_growth, 6),
                'end_salary': round(end_salary, 2),
            })

            age = end_age
            salary = end_salary
            block_index += 1

        target = self.predictor.metadata.get('target', {})
        return {
            'current_salary': round(float(current_salary), 2),
            'current_age': int(current_age),
            'retirement_age': int(retirement_age),
            'projected_salary_at_retirement': round(salary, 2),
            'model': 'catboost_m3',
            'model_version': self.predictor.metadata.get('model_version'),
            'projection_supported': bool(target.get('is_cagr') is True),
            'blending_validated': False,
            'provisional': True,
            'projection_warning': target.get('projection_warning'),
            'blocks': blocks,
            'salary_path': salary_path,
        }
