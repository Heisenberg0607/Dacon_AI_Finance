import json
from types import SimpleNamespace

import pytest

from backend.qwen_client import QwenGateway
from services.salary_growth.predictor import SalaryGrowthPredictor, SalaryGrowthArtifactError


@pytest.fixture
def predictor():
    return SalaryGrowthPredictor()


def gateway(predictor, content):
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    predictor._occupation_gateway = SimpleNamespace(enabled=True, chat=chat, parse_json=QwenGateway.parse_json)
    return calls


def test_mapping_matches_export(predictor):
    export = predictor.artifact_dir.parents[3] / 'modeling/export/occupation_categories.json'
    assert predictor.occupation_labels == json.loads(export.read_text(encoding='utf-8'))['mapping']


def test_llm_label_becomes_model_code_and_is_cached(predictor, monkeypatch):
    calls = gateway(predictor, '{"label": "컴퓨터 시스템 및 소프트웨어 전문가"}')
    inputs = []
    monkeypatch.setattr(predictor, '_remote_predict', lambda **kw: inputs.append(kw) or 3.0)
    for age in (32, 35):
        result = predictor.predict(age, 5000, '은행에서 백엔드 개발을 하고 있어요')
        assert result['occupation_mapping']['source'] == 'llm_mapping'
        assert result['input_features']['occupation'] == '222.0'
    assert [row['occupation'] for row in inputs] == ['222.0', '222.0']
    assert len(calls) == 1
    assert '컴퓨터 시스템 및 소프트웨어 전문가' in calls[0]['messages'][0]['content']


@pytest.mark.parametrize('raw,code', [('간호사', '243.0'), ('252', '252.0'), ('222.0', '222.0')])
def test_exact_inputs_skip_llm(predictor, raw, code):
    calls = gateway(predictor, '')
    assert predictor.normalize_occupation(raw)['category'] == code
    assert not calls


@pytest.mark.parametrize('content', ['not json', '[]', '{"label": "invented"}', '{"label": 222}', '{"category": "222.0"}'])
def test_invalid_answers_raise_and_are_not_cached(predictor, content):
    calls = gateway(predictor, content)
    for _ in range(2):
        with pytest.raises(SalaryGrowthArtifactError):
            predictor.normalize_occupation('backend developer')
    assert len(calls) == 4


def test_unavailable_llm_is_explicit(predictor):
    predictor._occupation_gateway = SimpleNamespace(enabled=False)
    with pytest.raises(SalaryGrowthArtifactError):
        predictor.normalize_occupation('backend developer')


def test_llm_failure_is_not_a_model_category(predictor):
    def fail(**kwargs):
        raise RuntimeError('offline')
    predictor._occupation_gateway = SimpleNamespace(enabled=True, chat=fail)
    with pytest.raises(SalaryGrowthArtifactError):
        predictor.normalize_occupation('backend developer')


@pytest.mark.parametrize('raw', ['교수', '대학교수', '대학교수 및 강사'])
def test_professor_variants_share_code_with_semantic_selection(predictor, raw):
    calls = gateway(predictor, '{"label": "대학교수 및 강사"}')
    assert predictor.normalize_occupation(raw)['category'] == '251.0'
    assert len(calls) == (0 if raw == '대학교수 및 강사' else 1)


def test_ambiguous_keywords_use_llm(predictor):
    calls = gateway(predictor, '{"label": "유치원 교사"}')
    assert predictor.normalize_occupation('교사')['category'] == '253.0'
    assert len(calls) == 1


def test_semantic_match_without_keywords(predictor):
    calls = gateway(predictor, '{"label": "데이터 및 네트워크 관련 전문가"}')
    assert predictor.normalize_occupation('SQL로 지표를 뽑고 인사이트를 찾아요')['category'] == '223.0'
    assert len(calls) == 1


def test_unclassified_answer_gets_second_review(predictor):
    responses = iter(['{"label": "분류불가"}', '{"label": "대학교수 및 강사"}'])
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))])
    predictor._occupation_gateway = SimpleNamespace(enabled=True, chat=chat, parse_json=QwenGateway.parse_json)
    assert predictor.normalize_occupation('대학에서 논문을 쓰고 학생들을 가르쳐요')['category'] == '251.0'
    assert len(calls) == 2


@pytest.mark.parametrize('raw', ['의료기기 판매 영업', '의료기기판매영업', '의료 기기 판매 영업',
                                '의료기기 영업사원', '자동차 영업', '의료기기 세일즈'])
def test_sales_candidate_is_selected_by_llm_not_weights(predictor, monkeypatch, raw):
    calls = gateway(predictor, '{"label": "영업 종사자"}')
    inputs = []
    monkeypatch.setattr(predictor, '_remote_predict', lambda **kw: inputs.append(kw) or 3.0)
    result = predictor.predict(32, 5000, raw)
    mapping = result['occupation_mapping']
    assert mapping['category'] == '510.0'
    assert mapping['label'] == '영업 종사자'
    assert mapping['fallback'] is False
    assert '510.0' in {row['code'] for row in mapping['keyword_candidates']}
    assert mapping['source'] == 'llm_mapping'
    assert len(calls) == 1
    assert all('score' not in row for row in mapping['keyword_candidates'])
    assert inputs[0]['occupation'] == '510.0'


def test_compound_tokens_and_no_partial_word_false_match(predictor):
    assert predictor._occupation_matcher.tokenize('의료기기 판매 영업') == ['의료', '기기', '판매', '영업']
    assert '인사' not in predictor._occupation_matcher.tokenize('인사이트를 찾아요')
    assert '271.0' not in predictor._occupation_keyword_candidates('인사이트를 찾아요')


@pytest.mark.parametrize('raw,label,code', [
    ('의료기기 기술영업', '감정·기술 영업 및 중개 관련 종사자', '274.0'),
    ('의료기기 영업이 아니라 수리를 합니다', '전기·전자기기 설치 및 수리원', '761.0'),
    ('예전에는 영업 지금은 간호사로 일해요', '간호사', '243.0'),
    ('의료기기 판매 영업 관리자', '판매 및 운송 관리자', '151.0'),
    ('의료기기 영업 개발자', '컴퓨터 시스템 및 소프트웨어 전문가', '222.0'),
])
def test_specialties_and_context_still_use_semantic_classification(predictor, raw, label, code):
    calls = gateway(predictor, json.dumps({'label': label}, ensure_ascii=False))
    result = predictor.normalize_occupation(raw)
    assert result['category'] == code
    assert result['source'] == 'llm_mapping'
    assert len(calls) == 1
    prompt = calls[0]['messages'][0]['content']
    assert '입력 토큰' in prompt
    assert 'matched_keywords' in prompt


def test_sales_sentence_sends_unweighted_candidates_to_llm(predictor):
    calls = gateway(predictor, '{"label": "영업 종사자"}')
    result = predictor.normalize_occupation('병원에 의료기기를 판매하는 영업 일을 하고 있어요')
    assert result['category'] == '510.0'
    assert result['source'] == 'llm_mapping'
    assert '510.0' in {row['code'] for row in result['keyword_candidates']}
    assert len(calls) == 1


def test_llm_can_choose_another_matched_candidate_for_same_keywords(predictor):
    # Demonstrate that application code does not force the sales category.
    calls = gateway(predictor, '{"label": "감정·기술 영업 및 중개 관련 종사자"}')
    result = predictor.normalize_occupation('의료기기 판매 영업')
    assert result['category'] == '274.0'
    assert len(calls) == 1


def test_valid_label_outside_retrieved_pool_is_rejected(predictor):
    calls = gateway(predictor, '{"label": "간호사"}')
    with pytest.raises(SalaryGrowthArtifactError):
        predictor.normalize_occupation('교수')
    assert len(calls) == 2


def test_no_suitable_candidate_expands_to_full_table(predictor):
    responses = iter(['{"no_suitable_candidate": true}', '{"label": "간호사"}'])
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))])
    predictor._occupation_gateway = SimpleNamespace(enabled=True, chat=chat, parse_json=QwenGateway.parse_json)
    assert predictor.normalize_occupation('병원 업무')['category'] == '243.0'
    assert len(calls) == 2


def test_unclassified_requires_explicit_confirmation_after_review(predictor):
    calls = gateway(predictor, '{"label": "분류불가", "unclassifiable": true}')
    assert predictor.normalize_occupation('현재 무직입니다')['category'] == '-1.0'
    assert len(calls) == 2


def test_bare_unclassified_answer_cannot_silently_feed_model(predictor):
    gateway(predictor, '{"label": "분류불가"}')
    with pytest.raises(SalaryGrowthArtifactError):
        predictor.normalize_occupation('의료기기 판매 영업')
