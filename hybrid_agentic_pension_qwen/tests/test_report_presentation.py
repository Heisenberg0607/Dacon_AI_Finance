import unittest

from backend.report_presentation import clean_report_prose, clean_report_text


class ReportPresentationTests(unittest.TestCase):
    def test_internal_field_variants(self):
        for field in [
            'risk_level_verified: false',
            r'risk\_level\_verified: false',
            r'risk\\_level\\_verified: false',
            '"risk_level_verified": false',
            '`risk_level_verified`: `false`',
            'product_extraction.risk_level_verified: False',
            'risk_level_verified',
        ]:
            with self.subTest(field=field):
                self.assertEqual(
                    clean_report_text(f'위험등급이 검증되지 않았으나 ({field}), 구성상품은 고위험입니다.'),
                    '위험등급이 검증되지 않았으나, 구성상품은 고위험입니다.',
                )

    def test_prose_only_and_ordinary_parentheses(self):
        original = {
            'product_analysis': 'PDF(E1, E7)에 따라 예금(30%), TDF(고위험)입니다.',
            'risk_notes': ['미확인 (risk_level_verified: false).'],
            'evidence_ids': ['E1', 'E7'],
            'risk_level_verified': False,
        }
        result = clean_report_prose(original)
        self.assertEqual(result['product_analysis'], 'PDF에 따라 예금(30%), TDF(고위험)입니다.')
        self.assertEqual(result['risk_notes'], ['미확인.'])
        self.assertEqual(result['evidence_ids'], ['E1', 'E7'])
        self.assertIs(result['risk_level_verified'], False)
        self.assertIn('risk_level_verified', original['risk_notes'][0])
