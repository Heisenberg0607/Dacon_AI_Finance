"""Report Q&A Agent의 질의 전처리 - 구어체 질문을 상품설명서 어휘로 옮긴다.

퇴직연금 가입자는 연령대가 높고, 질문은 문서의 문어체가 아니라 말하듯 들어온다.
그런데 rag.tokenize는 형태소 분석 없이 [가-힣A-Za-z0-9]+ 로만 자르므로 표현이 조금만
달라도 lexical 점수가 통째로 바뀐다. 실제 코퍼스(338청크)에서 확인한 격차:

    "원리금 보장 되나요"   -> 원리금 + 보장 + 되나요   (원리금보장 idf 4.43을 놓친다)
    "원금 까먹지 않나요"   -> 원금                     (원금손실 idf 5.44를 놓친다)
    "수수료 얼마나 떼가요" -> 수수료 + 얼마나           (총보수 idf 3.24를 놓친다)

이 모듈은 그 격차를 질의 쪽에서 메운다. 설계 원칙은 나머지 파이프라인과 같다.

  - LLM을 부르지 않는다. 결정론적이라 같은 질문에 항상 같은 확장이 나오고 demo 모드에서도
    똑같이 동작한다. 검색어를 LLM이 지어내면 근거 없는 확장을 검증할 방법이 없다.
  - 확장어는 실제 코퍼스 어휘에 있는 것만 내보낸다. SYNONYM_GROUPS는 넉넉히 적어두고
    QueryExpander가 생성 시점에 vocabulary로 걸러낸다. 문서에 없는 용어를 붙이면
    점수만 흔들고 근거는 하나도 늘지 않는다. PDF가 바뀌면 살아남는 매핑도 따라 바뀐다.
  - 원문을 버리지 않는다. 확장 질의는 lexical 매칭에만 쓰고 semantic 임베딩은 원문을
    그대로 쓴다(rag.search의 expanded_query 인자). 키워드를 잔뜩 붙인 문장을 임베딩하면
    질문의 의미가 오히려 흐려진다.
  - 무엇을 바꿨는지 전부 돌려준다. 응답의 query_expansion으로 화면과 로그에서 확인할 수 있다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping

# rag.tokenize와 같은 기준. 다만 결합 변형을 만들 때는 한 글자(형, 형태소 꼬리)도 필요하므로
# 길이 필터 없이 뽑는 별도 함수를 둔다.
TOKEN_RE = re.compile(r'[가-힣A-Za-z0-9]+')

# 확장어 상한. 여러 그룹이 동시에 걸리면 질의가 부풀어 원래 묻던 것이 묻힌다.
MAX_ADDED_TERMS = 8

# 고령 사용자에게서 실제로 자주 나오는 표기 오류. 부분 문자열로 치환하므로 순서가 의미를 갖는다.
TYPO_FIXES: tuple[tuple[str, str], ...] = (
    ('퇴직년금', '퇴직연금'),
    ('국민년금', '국민연금'),
    ('년금', '연금'),
    ('포토폴리오', '포트폴리오'),
    ('포트포리오', '포트폴리오'),
    ('포프폴리오', '포트폴리오'),
    ('수수로', '수수료'),
    ('수익율', '수익률'),
    ('변동율', '변동성'),
    ('원리급', '원리금'),
    ('게좌', '계좌'),
    ('예치금', '적립금'),
    ('디씨형', 'DC형'),
    ('디비형', 'DB형'),
    ('아이알피', 'IRP'),
    ('이알피', 'IRP'),
    ('몬테칼로', '몬테카를로'),
    ('디폴트옵숀', '디폴트옵션'),
)

# 질문을 감싸는 말이라 검색에 기여하지 않는다. 남겨두면 idf 노이즈로 엉뚱한 청크를 끌어올린다.
# 실제로 "원리금 보장 되나요"에서 '되나요'가, "수수료 얼마나 떼가요"에서 '얼마나'가 그렇게 작동했다.
STOPWORDS: frozenset[str] = frozenset({
    '되나요', '인가요', '일까요', '있나요', '없나요', '하나요', '한가요', '되요', '나요', '가요',
    '있는지', '없는지', '되는지', '하는지', '인지요', '되죠', '인가', '맞나요', '맞는지',
    '얼마나', '얼마', '얼마예요', '얼마인가요', '어떻게', '어떤', '어디', '무엇', '뭔가요', '뭐가', '뭐야', '뭔지',
    '알려줘', '알려주세요', '알려', '궁금', '궁금해요', '궁금합니다', '해줘', '해주세요', '주세요',
    '설명', '말해줘', '보여줘', '싶어요', '싶은데', '해요', '합니다', '입니다', '이에요', '예요',
    '그리고', '그런데', '근데', '그럼', '그러면', '만약', '혹시', '아니', '아니면',
    '지금', '제가', '저는', '저희', '우리', '나는', '내가', '이거', '그거', '저거', '이게', '그게',
    '대해', '대해서', '관련', '정도', '진짜', '정말', '조금', '많이', '너무',
    '것인지', '인지', '건가요', '거예요', '건지', '까요',
})

# (트리거, 확장어). 트리거는 공백을 제거한 질문 문자열에 대해 부분 일치로 검사하므로
# 사용자가 띄어 쓰든 붙여 쓰든 같은 결과가 나온다.
SYNONYM_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # 원금이 깎이는 것에 대한 걱정 - 가장 흔한 질문 유형
    (('까먹', '날리', '잃', '손해', '마이너스', '반토막', '떨어지', '깎이', '줄어들'),
     ('원금손실', '손실', '투자위험', '변동성')),
    # 원금이 지켜지는지
    (('원금보장', '원리금보장', '보장되', '보장인', '보장하', '안전한', '안전해', '안전하', '떼이', '보호받'),
     ('원리금보장', '예금자보호', '정기예금')),
    # 떼가는 돈
    (('수수료', '떼가', '떼는', '뗀다', '빼가', '비용', '보수', '얼마나내', '부담금'),
     ('총보수', '보수', '수수료')),
    # 중간에 찾는 것
    (('중도인출', '중간에빼', '미리빼', '먼저빼', '깨면', '깨고', '깰수', '해지', '찾아쓰', '빼쓰', '해약'),
     ('중도해지', '해지', '만기')),
    # 위험도
    (('위험한', '위험해', '위험도', '위험등급', '등급', '리스크', '안정적', '변동'),
     ('위험등급', '투자위험', '변동성')),
    # 얼마나 버는지
    (('수익률', '이자', '이율', '몇프로', '몇퍼센트', '얼마나벌', '얼마나불', '불어나', '수익'),
     ('수익률', '이율', '금리')),
    # 무엇에 투자하는지
    (('구성', '비중', '어디에투자', '뭐에투자', '무엇에투자', '담고', '들어있', '섞여'),
     ('자산배분', '포트폴리오', '편입')),
    # 상품을 바꾸는 것
    (('갈아타', '바꾸', '변경', '옮기', '교체'),
     ('상품변경', '운용')),
    # 상품 유형
    (('예금', '적금', '저축'), ('정기예금', '예금')),
    (('펀드', '주식', '채권', '투자상품'), ('펀드', '자산배분')),
    (('디폴트', '기본으로', '알아서'), ('디폴트옵션', '사전지정운용방법')),
)


def _raw_tokens(text: str) -> list[str]:
    """결합 변형을 만들기 위한 토큰. rag.tokenize와 달리 한 글자도 남긴다."""
    return TOKEN_RE.findall(text or '')


def normalize_query_text(text: str) -> tuple[str, list[str]]:
    """전각/공백 정리 + 오타 교정. 무엇을 고쳤는지 함께 돌려준다.

    챗봇의 키워드 폴백 라우팅도 이 함수를 거친 문장을 쓴다. '퇴직년금'이라고 적은 질문이
    키워드 분기에서만 걸러지는 일이 없도록 검색과 라우팅이 같은 표기를 보게 한다.
    """
    if not text:
        return '', []
    # NFKC로 전각 영숫자와 호환 문자를 반각으로 접는다.
    out = unicodedata.normalize('NFKC', text)
    out = re.sub(r'\s+', ' ', out).strip()
    corrected: list[str] = []
    for wrong, right in TYPO_FIXES:
        if wrong in out:
            out = out.replace(wrong, right)
            corrected.append(f'{wrong}→{right}')
    return out, corrected


class QueryExpander:
    """코퍼스 어휘에 맞춰 질의를 넓히는 결정론적 확장기.

    vocabulary는 PensionRAG.idf를 그대로 받는다. 값(idf)이 있으면 확장어를 상한까지 자를 때
    희소한 용어부터 남기는 데 쓴다. 흔한 말보다 드문 용어가 근거를 좁혀준다.
    """

    def __init__(self, vocabulary: Mapping[str, float] | Iterable[str]):
        if isinstance(vocabulary, Mapping):
            self.idf: dict[str, float] = dict(vocabulary)
        else:
            self.idf = {t: 1.0 for t in vocabulary}
        self.vocabulary = set(self.idf)

        # 코퍼스에 실제로 있는 확장어만 남긴다. 트리거가 하나도 안 남은 그룹은 통째로 버린다.
        groups: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        for triggers, targets in SYNONYM_GROUPS:
            live = tuple(t for t in targets if t in self.vocabulary)
            if live:
                groups.append((triggers, live))
        self.groups = tuple(groups)

    # ------------------------------------------------------------------ helpers

    def _join_variants(self, tokens: list[str]) -> list[str]:
        """띄어 쓴 복합어를 붙여 쓴 형태로 복원한다.

        '원리금 보장' -> '원리금보장'. 코퍼스에 그 형태가 실제로 있을 때만 추가하므로
        '중도 인출'처럼 문서에 없는 조합은 만들어내지 않는다.
        """
        found: list[str] = []
        for size in (2, 3):
            for i in range(len(tokens) - size + 1):
                joined = ''.join(tokens[i:i + size])
                if len(joined) > 1 and joined in self.vocabulary:
                    found.append(joined)
        return found

    def _rank(self, terms: Iterable[str]) -> list[str]:
        # idf가 높은(드문) 용어부터. 같은 값이면 원래 순서를 유지한다.
        ordered = list(dict.fromkeys(terms))
        return sorted(ordered, key=lambda t: -self.idf.get(t, 1.0))

    # ------------------------------------------------------------------ main

    def expand(self, query: str, context_terms: Iterable[str] = ()) -> dict[str, Any]:
        """질의 하나를 확장한다.

        context_terms는 남는 내용어가 거의 없을 때만 쓰는 보조 힌트다(가입 상품명, 사업자).
        "수수료?"처럼 한 단어짜리 질문에서 순위를 잡아주는 용도이고, 평소에는 붙이지 않는다.
        내 상품 쪽으로 결과를 계속 끌어당기면 비교 질문이 망가진다.
        """
        normalized, corrected = normalize_query_text(query)
        if not normalized:
            return {
                'original': query,
                'expanded_query': query,
                'changed': False,
                'corrected': [],
                'removed': [],
                'added': [],
            }

        raw = _raw_tokens(normalized)
        sized = [t for t in raw if len(t) > 1]
        content = [t for t in sized if t.lower() not in STOPWORDS]
        removed = [t for t in sized if t.lower() in STOPWORDS]
        # "알려주세요"처럼 전부 불용어인 질문. 되살려두긴 하되 내용어가 없다는 사실은 기억한다.
        # 되살린 토큰을 내용어로 세면 아래 컨텍스트 보강이 걸리지 않는다.
        empty_of_content = not content
        if empty_of_content:
            content = sized
            removed = []

        candidates: list[str] = list(self._join_variants(raw))

        squashed = normalized.replace(' ', '')
        for triggers, targets in self.groups:
            if any(trigger in squashed for trigger in triggers):
                candidates.extend(targets)

        have = {t.lower() for t in content}
        added = self._rank(t for t in candidates if t.lower() not in have)[:MAX_ADDED_TERMS]

        # 내용어가 거의 없는 질문만 상품 컨텍스트로 받쳐준다.
        # 상품명은 'KB국민은행 디폴트옵션 안정형 포트폴리오' 같은 문장이므로 토큰으로 쪼개
        # 코퍼스에 있는 조각만 취한다. 제목 전체를 그대로 넣으면 어휘에 없어 전부 버려진다.
        if empty_of_content or len(content) + len(added) < 2:
            extra = [
                t for term in context_terms for t in _raw_tokens(term)
                if len(t) > 1 and t in self.vocabulary and t.lower() not in have
            ]
            added = self._rank(added + extra)[:MAX_ADDED_TERMS]

        expanded = ' '.join(content + added)
        return {
            'original': query,
            'expanded_query': expanded or normalized,
            'changed': bool(corrected or removed or added),
            'corrected': corrected,
            'removed': removed,
            'added': added,
        }
