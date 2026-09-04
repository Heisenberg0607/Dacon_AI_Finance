from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import ROOT

from .age_curve import AgeGrowthCurve


ARTIFACT_DIR = ROOT / 'models' / 'salary_growth'
REQUIRED_FILENAMES = {
    'model': 'catboost_m3.cbm',
    'metadata': 'metadata.json',
    'occupation_categories': 'occupation_categories.json',
    'age_growth_curve': 'age_growth_curve.json',
    'smoke_test': 'smoke_test.json',
}


class SalaryGrowthArtifactError(RuntimeError):
    pass


class SalaryGrowthPredictor:
    def __init__(self, artifact_dir: Path = ARTIFACT_DIR):
        self.artifact_dir = artifact_dir
        self.model_path = artifact_dir / REQUIRED_FILENAMES['model']
        self.metadata_path = artifact_dir / REQUIRED_FILENAMES['metadata']
        self.occupation_path = artifact_dir / REQUIRED_FILENAMES['occupation_categories']
        self.age_curve_path = artifact_dir / REQUIRED_FILENAMES['age_growth_curve']
        self.smoke_test_path = artifact_dir / REQUIRED_FILENAMES['smoke_test']
        self.metadata = self._load_json(self.metadata_path)
        self.occupation_payload = self._load_json(self.occupation_path)
        self.age_curve = AgeGrowthCurve(self._load_json(self.age_curve_path))
        self.smoke_test = self._load_json(self.smoke_test_path)
        self.feature_order = list(self.metadata.get('features', {}).get('order') or [])
        self.categorical_features = list(self.metadata.get('features', {}).get('categorical') or [])
        self.occupation_categories = {str(x) for x in self.occupation_payload.get('categories', [])}
        self._model: Any | None = None
        self._validate_static_artifacts()

    @property
    def required_files(self) -> dict[str, Path]:
        return {name: self.artifact_dir / filename for name, filename in REQUIRED_FILENAMES.items()}

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise SalaryGrowthArtifactError(f'missing artifact: {path.name}')
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise SalaryGrowthArtifactError(f'invalid JSON artifact: {path.name}') from exc

    def _validate_static_artifacts(self) -> None:
        missing = [name for name, path in self.required_files.items() if not path.exists()]
        if missing:
            raise SalaryGrowthArtifactError(f'missing salary growth artifact(s): {", ".join(missing)}')
        if self.feature_order != ['log_wage_t', 'age', 'occupation']:
            raise SalaryGrowthArtifactError(f'unexpected feature order: {self.feature_order}')
        model_features = list(self.metadata.get('catboost', {}).get('feature_names_') or [])
        if model_features and model_features != self.feature_order:
            raise SalaryGrowthArtifactError('metadata CatBoost feature_names_ does not match features.order')
        if not self.occupation_categories:
            raise SalaryGrowthArtifactError('occupation_categories.json has no categories')
        if '-1.0' not in self.occupation_categories:
            raise SalaryGrowthArtifactError('occupation fallback category -1.0 is missing')

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from catboost import CatBoostRegressor
            except ImportError as exc:
                raise SalaryGrowthArtifactError('catboost is not installed') from exc
            model = CatBoostRegressor()
            model.load_model(str(self.model_path))
            feature_names = list(getattr(model, 'feature_names_', []) or [])
            if feature_names and feature_names != self.feature_order:
                raise SalaryGrowthArtifactError('loaded model feature_names_ does not match metadata')
            self._model = model
        return self._model

    def normalize_occupation(self, occupation: str) -> str:
        raw = str(occupation).strip()
        if not raw:
            raise ValueError('occupation is required')
        if raw in self.occupation_categories:
            return raw
        try:
            numeric = f'{float(raw):.1f}'
        except ValueError:
            numeric = raw
        if numeric in self.occupation_categories:
            return numeric
        raise ValueError(f'unsupported occupation category: {occupation}')

    @staticmethod
    def annual_to_monthly_salary(current_salary: float) -> float:
        salary = float(current_salary)
        if not math.isfinite(salary) or salary <= 0:
            raise ValueError('current_salary must be greater than 0')
        return salary / 12.0

    def predict(self, current_age: int, current_salary: float, occupation: str) -> dict[str, Any]:
        if current_age < 17 or current_age > 90:
            raise ValueError('current_age must be between 17 and 90')
        normalized_occupation = self.normalize_occupation(occupation)
        monthly_wage = self.annual_to_monthly_salary(current_salary)
        log_wage_t = math.log1p(monthly_wage)
        row = [[log_wage_t, int(current_age), normalized_occupation]]
        prediction = float(self._load_model().predict(row)[0])
        if not math.isfinite(prediction):
            raise SalaryGrowthArtifactError('model returned a non-finite prediction')
        target = self.metadata.get('target', {})
        return {
            'model': 'catboost_m3',
            'model_name': self.metadata.get('model_name'),
            'model_version': self.metadata.get('model_version'),
            'prediction_horizon_years': int(target.get('horizon_years', 3)),
            'predicted_growth_rate': round(prediction, 6),
            'target_unit': target.get('unit'),
            'target_definition': target.get('formula'),
            'target_type': target.get('target_type'),
            'projection_supported': bool(target.get('is_cagr') is True),
            'input_features': {
                'log_wage_t': log_wage_t,
                'age': int(current_age),
                'occupation': normalized_occupation,
                'monthly_wage_manwon': monthly_wage,
            },
        }

    def validate_artifacts(self, include_model_load: bool = False) -> dict[str, Any]:
        checks: dict[str, Any] = {
            'ok': True,
            'artifact_dir': str(self.artifact_dir),
            'files': {name: path.exists() for name, path in self.required_files.items()},
            'feature_order': self.feature_order,
            'metadata_model_features': list(self.metadata.get('catboost', {}).get('feature_names_') or []),
            'feature_order_matches_metadata': self.feature_order == list(self.metadata.get('catboost', {}).get('feature_names_') or []),
            'occupation_category_count': len(self.occupation_categories),
            'fallback_occupation_supported': '-1.0' in self.occupation_categories,
            'age_curve_min_age': self.age_curve.min_age,
            'age_curve_max_age': self.age_curve.max_age,
            'smoke_test_reload_identical': bool(
                self.smoke_test.get('original_vs_reloaded_prediction', {}).get('identical_with_atol_1e_12')
            ),
            'target_is_cagr': bool(self.metadata.get('target', {}).get('is_cagr')),
            'projection_supported': bool(self.metadata.get('target', {}).get('is_cagr') is True),
        }
        if include_model_load:
            self._load_model()
            checks['model_loaded'] = True
        return checks


@lru_cache(maxsize=1)
def get_salary_growth_predictor() -> SalaryGrowthPredictor:
    return SalaryGrowthPredictor()
