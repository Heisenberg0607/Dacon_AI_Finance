# 깨움 CatBoost API 분리 패치

기준 저장소:
`Heisenberg0607/Dacon_AI_Finance` `master`

이 ZIP은 **저장소 루트(Dacon_AI_Finance 폴더)** 에 덮어쓰도록 경로가 구성돼 있습니다.

## 바뀌는 파일

```text
hybrid_agentic_pension_qwen/
├─ services/salary_growth/predictor.py   # CatBoost 직접 import -> 외부 HTTP API 호출
├─ requirements.txt                     # catboost 제거, httpx 명시, uvicorn standard 축소
├─ .env.example                         # 외부 모델 API 환경변수 추가
└─ .vercelignore                        # Vercel에서 로컬 .cbm 제외

salary_growth_model_api/
├─ main.py                              # 외부 CatBoost inference API
├─ requirements.txt
├─ Dockerfile
├─ .env.example
├─ copy_model.bat
├─ copy_model.ps1
└─ models/
```

## 적용 순서

1. 이 ZIP을 `Dacon_AI_Finance` 저장소 루트에 풀고 덮어쓰기.
2. `salary_growth_model_api\copy_model.bat` 실행.
3. `salary_growth_model_api\models\catboost_m3.cbm` 생성 확인.
4. 외부 모델 API 서비스를 배포.
5. Vercel 환경변수에 아래 설정:
   - `SALARY_GROWTH_API_URL`
   - `SALARY_GROWTH_API_KEY`
6. Vercel 재배포.

## 핵심

Vercel에서는 더 이상 `catboost` 패키지를 설치하지 않습니다.
기존 predictor의 전처리/occupation mapping/age curve는 유지하고,
CatBoost의 `MODEL.predict()` 실행만 외부 API로 옮겼습니다.
