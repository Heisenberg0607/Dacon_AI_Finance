from __future__ import annotations

import re
from functools import lru_cache


# These expand job vocabulary, not the model's category table. Every resulting
# code is checked against the loaded artifact before it can become a candidate.
SEARCH_SYNONYMS = {
    '510.0': {'영업', '영업직', '영업사원', '세일즈'},
    '251.0': {'교수', '대학교수'},
    '222.0': {'개발자', '프로그래머', '백엔드', '프론트엔드'},
    '223.0': {'데이터분석가', '데이터사이언티스트'},
    '243.0': {'간호사'},
    '441.0': {'조리사', '요리사', '셰프'},
    '922.0': {'배달원', '배달기사'},
}
GENERIC = {'및', '관련', '기타', '전문가', '종사자', '관리자', '단순', '서비스', '기능'}
JOB_WORDS = {'영업', '판매', '개발', '분석', '연구', '설치', '수리', '정비', '조립',
             '제조', '운전', '조리', '교사', '강사', '간호사', '회계', '경리', '상담'}



class OccupationMatcher:
    def __init__(self, labels: dict[str, str]):
        self.labels = labels
        self.keywords = {
            code: set(re.findall(r'[가-힣a-zA-Z0-9]+', label.casefold())) - GENERIC
            for code, label in labels.items() if code != '-1.0'
        }
        self.vocabulary = set().union(*self.keywords.values(), *SEARCH_SYNONYMS.values())
        self.vocabulary.update(JOB_WORDS | {'관리', '기기'})
        # Occupational suffixes in labels (수리원, 분석가) should also expose
        # the duty stem used in natural-language descriptions.
        for keywords in self.keywords.values():
            keywords.update({word[:-1] for word in keywords
                             if len(word) >= 3 and word.endswith(('원', '가'))})
        self.vocabulary.update(set().union(*self.keywords.values()))
        self.vocabulary = {word for word in self.vocabulary if len(word) >= 2}
        for keywords in self.keywords.values():
            keywords.update(part for word in list(keywords) for part in self.tokenize(word))

    def tokenize(self, raw: str) -> list[str]:
        """Segment complete compounds using the occupation vocabulary.

        Unknown words remain whole: 인사이트 must never produce 인사.
        This is dictionary segmentation, not a Korean morphological model.
        """
        @lru_cache(maxsize=None)
        def segment(word: str):
            if not word:
                return ()
            # Prefer longer dictionary entries, but require complete coverage.
            for end in range(len(word), 1, -1):
                head = word[:end]
                if head in self.vocabulary:
                    tail = segment(word[end:])
                    if tail is not None:
                        return (head, *tail)
            return None

        result = []
        for word in re.findall(r'[가-힣]+|[a-zA-Z]+|[0-9]+', raw.casefold()):
            parts = segment(word)
            if parts is None:
                for suffix in ('에서는', '으로', '에서', '이고', '이며', '은', '는', '을', '를', '로', '의', '이'):
                    if word.endswith(suffix):
                        parts = segment(word[:-len(suffix)])
                        if parts:
                            break
            result.extend(parts or (word,))
        return list(dict.fromkeys(result))

    def search(self, raw: str) -> list[dict]:
        """Return every keyword match in artifact order, without role weights."""
        tokens = set(self.tokenize(raw))
        candidates = []
        for code, keywords in self.keywords.items():
            matched = tokens & (keywords | SEARCH_SYNONYMS.get(code, set()))
            if matched:
                candidates.append({'code': code, 'label': self.labels[code],
                                   'matched_keywords': sorted(matched)})
        return candidates
