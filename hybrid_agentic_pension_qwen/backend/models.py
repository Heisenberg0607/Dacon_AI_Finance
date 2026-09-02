from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator


InvestmentType = Literal['안정형', '안정투자형', '중립투자형', '적극투자형']
OperationType = Literal['DB', 'DC', 'IRP']


class UserPensionInput(BaseModel):
    # 공통 입력
    age: int = Field(ge=18, le=70)
    retirement_age: int = Field(ge=40, le=85)
    annual_income: float = Field(gt=0, description='현재 연소득, 만원')
    desired_monthly_income: float = Field(gt=0, description='은퇴 후 희망 월 소득, 만원')
    operation_type: OperationType

    # DC / IRP 전용
    current_savings: float | None = Field(default=None, ge=0, description='현재 적립금, 만원')
    annual_contribution: float | None = Field(default=None, ge=0, description='연간 납입액, 만원')
    provider: str | None = Field(default=None, max_length=80)
    product_name: str | None = Field(default=None, max_length=180)
    investment_type: InvestmentType | None = None

    # DB 전용
    current_tenure_years: float | None = Field(default=None, ge=0, le=50, description='현재 근속연수')
    wage_growth_rate: float | None = Field(default=None, ge=-5, le=20, description='예상 임금상승률, %')
    industry_job: str | None = Field(default=None, max_length=120)
    company_size: Literal['대기업', '중견기업', '중소기업', '공공/기타'] | None = None
    salary_history: list[float] = Field(default_factory=list, description='과거→현재 순 최근 연봉 이력, 만원')

    @model_validator(mode='after')
    def validate_input_consistency(self):
        if self.retirement_age <= self.age:
            raise ValueError('은퇴 나이는 현재 나이보다 커야 합니다.')

        # 연봉 이력은 유효한 양수만 최대 5개 유지
        self.salary_history = [float(v) for v in self.salary_history if v is not None and float(v) > 0][-5:]

        if self.operation_type == 'DB':
            if self.current_tenure_years is None:
                raise ValueError('DB형은 현재 근속연수가 필요합니다.')
            # DB는 개인 투자상품/투자유형/개인 적립금 납입 정보를 사용하지 않음
            self.current_savings = None
            self.annual_contribution = None
            self.product_name = None
            self.investment_type = None
            if not self.provider:
                self.provider = '모름'
        else:
            if self.current_savings is None:
                raise ValueError('DC/IRP는 현재 적립금이 필요합니다.')
            if self.annual_contribution is None:
                raise ValueError('DC/IRP는 연간 납입액이 필요합니다.')
            if not self.provider or self.provider == '모름':
                raise ValueError('DC/IRP는 가입 사업자를 선택해야 합니다.')
            if not self.product_name:
                raise ValueError('DC/IRP는 가입 상품명을 선택해야 합니다.')
            if not self.investment_type:
                raise ValueError('DC/IRP는 투자 유형이 필요합니다.')
            # DB 전용값은 분석에서 사용하지 않음
            self.current_tenure_years = None
            self.wage_growth_rate = None
            self.industry_job = None
            self.company_size = None
            self.salary_history = []
        return self

    @property
    def years_to_retirement(self) -> int:
        return self.retirement_age - self.age

    @property
    def expected_additional_tenure_years(self) -> int:
        # 요청대로 사용자에게 별도 입력받지 않고 자동 계산
        return self.years_to_retirement

    @property
    def total_expected_tenure_years(self) -> float | None:
        if self.operation_type != 'DB' or self.current_tenure_years is None:
            return None
        return self.current_tenure_years + self.expected_additional_tenure_years


class ChatTurn(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(max_length=4000)


class ReprojectRequest(BaseModel):
    """보고서 화면에서 다른 상품으로 전망을 다시 계산할 때 쓰는 요청.

    기준 입력값은 서버 세션에 있으므로 바꿀 상품만 보낸다.
    투자유형은 카탈로그의 risk_type을 따르므로 클라이언트가 정하지 않는다.
    """

    analysis_id: str = Field(min_length=8, max_length=64)
    provider: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=300)


class ChatRequest(BaseModel):
    """보고서 화면 챗봇 요청.

    분석 결과 전체는 서버 세션(session_store)에 있으므로 analysis_id만 주고받는다.
    """

    analysis_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=800)
    history: list[ChatTurn] = Field(default_factory=list)

    @model_validator(mode='after')
    def trim_history(self):
        # 최근 6턴만 유지해 프롬프트 길이를 제한한다.
        self.history = self.history[-6:]
        self.message = self.message.strip()
        if not self.message:
            raise ValueError('질문 내용이 비어 있습니다.')
        return self
