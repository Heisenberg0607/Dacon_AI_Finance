from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from backend.config import ROOT

from .age_curve import AgeGrowthCurve


ARTIFACT_DIR = ROOT / 'models' / 'salary_growth'
REQUIRED_FILENAMES = {
    # catboost_m3.cbm is intentionally NOT required on Vercel anymore.
    # The CatBoost runtime + .cbm model now live behind SALARY_GROWTH_API_URL.
    'metadata': 'metadata.json',
    'occupation_categories': 'occupation_categories.json',
    'age_growth_curve': 'age_growth_curve.json',
    'smoke_test': 'smoke_test.json',
}

OCCUPATION_KEYWORD_MAP = [
    ('222.0', ['데이터', '개발', '프로그래머', '소프트웨어', 'it', 'ai', '인공지능', '시스템', '엔지니어']),
    ('320.0', ['금융', '은행', '증권', '보험', '자산운용', '투자']),
    ('212.0', ['회계', '세무', '감사', '재무']),
    ('213.0', ['마케팅', '기획', '컨설팅', '분석가', '연구원']),
    ('243.0', ['교사', '강사', '교육']),
    ('245.0', ['의사', '간호', '의료', '보건']),
    ('411.0', ['사무', '행정', '총무']),
    ('510.0', ['영업', '판매', '매장']),
    ('531.0', ['서비스', '상담', '고객']),
    ('611.0', ['농업', '어업', '축산']),
    ('721.0', ['전기', '전자', '정비', '설비']),
    ('741.0', ['건설', '건축', '토목']),
    ('821.0', ['운전', '배송', '물류']),
    ('910.0', ['단순', '보조', '현장']),
]


class SalaryGrowthArtifactError(RuntimeError):
    pass


class SalaryGrowthPredictor:
    def __init__(self, artifact_dir: Path = ARTIFACT_DIR):
        self.artifact_dir = artifact_dir
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

        self.api_url = os.getenv('SALARY_GROWTH_API_URL', '').strip().rstrip('/')
        self.api_key = os.getenv('SALARY_GROWTH_API_KEY', '').strip()
        try:
            self.api_timeout_seconds = float(os.getenv('SALARY_GROWTH_API_TIMEOUT_SECONDS', '20'))
        except ValueError:
            self.api_timeout_seconds = 20.0

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
            raise SalaryGrowthArtifactError(
                'metadata CatBoost feature_names_ does not match features.order'
            )

        if not self.occupation_categories:
            raise SalaryGrowthArtifactError('occupation_categories.json has no categories')

        if '-1.0' not in self.occupation_categories:
            raise SalaryGrowthArtifactError('occupation fallback category -1.0 is missing')

    def _headers(self) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['X-API-Key'] = self.api_key
        return headers

    def _require_api_url(self) -> None:
        if not self.api_url:
            raise SalaryGrowthArtifactError(
                'SALARY_GROWTH_API_URL is not configured. '
                'Deploy salary_growth_model_api and set the URL in Vercel Environment Variables.'
            )

    def _remote_predict(self, log_wage_t: float, age: int, occupation: str) -> float:
        self._require_api_url()

        payload = {
            'log_wage_t': float(log_wage_t),
            'age': int(age),
            'occupation': str(occupation),
        }

        try:
            with httpx.Client(timeout=self.api_timeout_seconds) as client:
                response = client.post(
                    f'{self.api_url}/predict',
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise SalaryGrowthArtifactError(
                f'salary growth model API request failed: {exc}'
            ) from exc

        if response.is_error:
            detail = response.text[:1000]
            raise SalaryGrowthArtifactError(
                f'salary growth model API returned HTTP {response.status_code}: {detail}'
            )

        try:
            data = response.json()
            prediction = float(data['prediction'])
        except (ValueError, TypeError, KeyError) as exc:
            raise SalaryGrowthArtifactError(
                'salary growth model API returned an invalid prediction payload'
            ) from exc

        if not math.isfinite(prediction):
            raise SalaryGrowthArtifactError('model API returned a non-finite prediction')

        return prediction

    def _remote_health(self) -> dict[str, Any]:
        self._require_api_url()

        try:
            with httpx.Client(timeout=self.api_timeout_seconds) as client:
                response = client.get(
                    f'{self.api_url}/health',
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise SalaryGrowthArtifactError(
                f'salary growth model API health check failed: {exc}'
            ) from exc

        if response.is_error:
            raise SalaryGrowthArtifactError(
                f'salary growth model API health returned HTTP {response.status_code}: '
                f'{response.text[:1000]}'
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SalaryGrowthArtifactError(
                'salary growth model API health returned invalid JSON'
            ) from exc

        if payload.get('ok') is not True:
            raise SalaryGrowthArtifactError(
                f'salary growth model API is not healthy: {payload}'
            )

        return payload

    def normalize_occupation(self, occupation: str) -> dict[str, Any]:
        raw = str(occupation).strip()

        if not raw:
            return {
                'input': raw,
                'category': '-1.0',
                'source': 'empty_fallback',
                'confidence': 'low',
                'fallback': True,
            }

        if raw in self.occupation_categories:
            return {
                'input': raw,
                'category': raw,
                'source': 'exact_category',
                'confidence': 'high',
                'fallback': False,
            }

        try:
            numeric = f'{float(raw):.1f}'
        except ValueError:
            numeric = raw

        if numeric in self.occupation_categories:
            return {
                'input': raw,
                'category': numeric,
                'source': 'numeric_category',
                'confidence': 'high',
                'fallback': False,
            }

        lowered = raw.lower()
        for category, keywords in OCCUPATION_KEYWORD_MAP:
            if category in self.occupation_categories and any(keyword in lowered for keyword in keywords):
                return {
                    'input': raw,
                    'category': category,
                    'source': 'keyword_mapping',
                    'confidence': 'medium',
                    'fallback': False,
                }

        return {
            'input': raw,
            'category': '-1.0',
            'source': 'unknown_fallback',
            'confidence': 'low',
            'fallback': True,
        }

    @staticmethod
    def annual_to_monthly_salary(current_salary: float) -> float:
        salary = float(current_salary)
        if not math.isfinite(salary) or salary <= 0:
            raise ValueError('current_salary must be greater than 0')
        return salary / 12.0

    def predict(self, current_age: int, current_salary: float, occupation: str) -> dict[str, Any]:
        if current_age < 17 or current_age > 90:
            raise ValueError('current_age must be between 17 and 90')

        occupation_mapping = self.normalize_occupation(occupation)
        normalized_occupation = occupation_mapping['category']

        monthly_wage = self.annual_to_monthly_salary(current_salary)
        log_wage_t = math.log1p(monthly_wage)

        # Only the actual CatBoost inference moved outside Vercel.
        # Preprocessing / occupation mapping / metadata / age curve remain unchanged here.
        prediction = self._remote_predict(
            log_wage_t=log_wage_t,
            age=int(current_age),
            occupation=normalized_occupation,
        )

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
            'occupation_mapping': occupation_mapping,
            'input_features': {
                'log_wage_t': log_wage_t,
                'age': int(current_age),
                'occupation': normalized_occupation,
                'monthly_wage_manwon': monthly_wage,
            },
        }

    def validate_artifacts(self, include_model_load: bool = False) -> dict[str, Any]:
        model_features = list(self.metadata.get('catboost', {}).get('feature_names_') or [])

        checks: dict[str, Any] = {
            'ok': bool(self.api_url),
            'inference_mode': 'remote_api',
            'api_configured': bool(self.api_url),
            'api_url': self.api_url or None,
            'artifact_dir': str(self.artifact_dir),
            'files': {name: path.exists() for name, path in self.required_files.items()},
            'feature_order': self.feature_order,
            'metadata_model_features': model_features,
            'feature_order_matches_metadata': self.feature_order == model_features,
            'occupation_category_count': len(self.occupation_categories),
            'fallback_occupation_supported': '-1.0' in self.occupation_categories,
            'age_curve_min_age': self.age_curve.min_age,
            'age_curve_max_age': self.age_curve.max_age,
            'smoke_test_reload_identical': bool(
                self.smoke_test.get('original_vs_reloaded_prediction', {}).get(
                    'identical_with_atol_1e_12'
                )
            ),
            'target_is_cagr': bool(self.metadata.get('target', {}).get('is_cagr')),
            'projection_supported': bool(
                self.metadata.get('target', {}).get('is_cagr') is True
            ),
        }

        # Backward-compatible with the existing ?load_model=true health endpoint:
        # now this checks the remote CatBoost service instead of importing CatBoost on Vercel.
        if include_model_load:
            remote = self._remote_health()
            checks['remote_health'] = remote
            checks['model_loaded'] = bool(remote.get('model_loaded'))
            checks['ok'] = checks['ok'] and bool(remote.get('ok'))

        return checks


@lru_cache(maxsize=1)
def get_salary_growth_predictor() -> SalaryGrowthPredictor:
    return SalaryGrowthPredictor()
