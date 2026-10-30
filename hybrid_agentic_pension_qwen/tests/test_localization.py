"""Run with: python -m unittest discover -s tests -p test_localization.py"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.localization import localize_chat, localize_presentation, translate_strings
from backend.models import ChatRequest, ReportLanguageRequest, UserPensionInput


class Gateway:
    enabled = True

    def __init__(self, transform):
        self.transform = transform

    def chat(self, messages, **kwargs):
        source = json.loads(messages[1]['content'])['strings']
        content = json.dumps({'translations': self.transform(source)}, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    @staticmethod
    def parse_json(text, fallback):
        return json.loads(text)


class DisabledGateway:
    enabled = False


def sample_result(operation='DC'):
    return {
        'user': {'operation_type': operation, 'age': 32, 'retirement_age': 60},
        'finance': {'future_asset': 18000, 'estimated_db_benefit': 17000,
                    'target_retirement_asset': 45000, 'goal_rate_pct': 40},
        'monte_carlo': {'success_probability_pct': 12.5},
        'profile': {'diagnosis_hint': '은퇴까지 28년'},
        'report': {'title': '연금 보고서', 'executive_summary': '자산 180,000,000, 확률 12.5%',
                   'strategy': ['저축을 검토하세요.'], 'risk_notes': ['4%는 보장값이 아닙니다.']},
    }


class LocalizationTests(unittest.TestCase):
    def test_defaults_and_rejects_unsupported_language(self):
        self.assertEqual(ChatRequest(analysis_id='analysis1', message='Hello').language, 'ko')
        self.assertEqual(ReportLanguageRequest(analysis_id='analysis1', language='zh').language, 'zh')
        with self.assertRaises(ValueError):
            ReportLanguageRequest(analysis_id='analysis1', language='fr')

    def test_language_does_not_change_financial_input(self):
        payload = dict(age=32, retirement_age=60, annual_income=5000,
                       desired_monthly_income=150, operation_type='DB', current_tenure_years=3)
        original = UserPensionInput(**payload)
        localized = UserPensionInput(**payload, language='en')
        self.assertEqual(original.model_dump(exclude={'language'}),
                         localized.model_dump(exclude={'language'}))

    def test_numeric_tokens_survive_translation_and_reordering(self):
        gateway = Gateway(lambda _: ['Probability ⟦N1⟧%, assets KRW ⟦N0⟧.'])
        self.assertEqual(translate_strings(['자산 180,000,000, 확률 12.5%'], 'en', gateway),
                         ['Probability 12.5%, assets KRW 180,000,000.'])

    def test_missing_duplicate_and_invented_numbers_are_rejected(self):
        for target in ['Assets', 'Assets ⟦N0⟧ ⟦N0⟧', 'Assets ⟦N0⟧ and 99']:
            with self.subTest(target=target):
                self.assertIsNone(translate_strings(['자산 123'], 'en', Gateway(lambda _: [target])))

    def test_evidence_identifiers_are_preserved(self):
        self.assertIsNone(translate_strings(['근거 E123 금액 500'], 'en',
                          Gateway(lambda _: ['Evidence E124 amount ⟦N0⟧'])))

    def test_report_translation_keeps_source_immutable(self):
        result = sample_result()
        before = copy.deepcopy(result)
        translated = localize_presentation(result, 'en',
            Gateway(lambda strings: ['English ' + item for item in strings]))
        self.assertEqual(translated['mode'], 'translated')
        self.assertEqual(result, before)
        self.assertEqual(translated['report']['executive_summary'],
                         'English 자산 180,000,000, 확률 12.5%')

    def test_no_key_templates_support_both_plans_and_languages(self):
        for operation in ('DB', 'DC', 'IRP'):
            for language in ('en', 'zh'):
                with self.subTest(operation=operation, language=language):
                    result = sample_result(operation)
                    before = copy.deepcopy(result)
                    localized = localize_presentation(result, language, DisabledGateway())
                    self.assertEqual(localized['mode'], 'template')
                    self.assertNotRegex(json.dumps(localized, ensure_ascii=False), r'[가-힣]')
                    self.assertIn('170,000,000' if operation == 'DB' else '180,000,000',
                                  localized['report']['executive_summary'])
                    self.assertEqual(result, before)

    def test_korean_returns_original_report_without_gateway(self):
        result = sample_result()
        self.assertEqual(localize_presentation(result, 'ko', DisabledGateway())['report'],
                         result['report'])

    def test_unavailable_chat_translation_is_explicit_and_preserves_evidence(self):
        response = {'answer': '자산 123', 'evidence': [{'evidence_id': 'E1', 'snippet': '한국어 원문'}],
                    'what_if': {'notes': ['가정']}}
        localized = localize_chat(response, sample_result(), 'en', DisabledGateway())
        self.assertIn('translation is unavailable', localized['answer'])
        self.assertEqual(localized['evidence'], response['evidence'])
        self.assertIsNone(localized['what_if'])
        self.assertIsNotNone(response['what_if'])


if __name__ == '__main__':
    unittest.main()
