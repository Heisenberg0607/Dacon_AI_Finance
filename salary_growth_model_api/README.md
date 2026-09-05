# KKAEUM Salary Growth Model API

This folder runs the existing `catboost_m3.cbm` outside Vercel.

## 1) Copy the model into this service

From Windows, double-click or run:

```bat
salary_growth_model_api\copy_model.bat
```

After that this file must exist:

```text
salary_growth_model_api/models/catboost_m3.cbm
```

The original model under
`hybrid_agentic_pension_qwen/models/salary_growth/catboost_m3.cbm`
can remain in your local repository. Vercel excludes it through `.vercelignore`.

## 2) Local test

```powershell
cd salary_growth_model_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:MODEL_API_KEY="test-secret"
uvicorn main:app --reload --port 8001
```

Health:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/health" `
  -Headers @{"X-API-Key"="test-secret"}
```

Prediction example:

```powershell
$body = @{
  log_wage_t = 6.0
  age = 32
  occupation = "222.0"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8001/predict" `
  -Headers @{"X-API-Key"="test-secret"} `
  -ContentType "application/json" `
  -Body $body
```

## 3) Deploy this folder to a Python/Docker web host

Use the included `Dockerfile`, or run:

```text
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set:

```env
MODEL_API_KEY=use-a-long-random-secret
MODEL_PATH=models/catboost_m3.cbm
```

After deployment, assume the service URL is:

```text
https://YOUR-MODEL-SERVICE
```

## 4) Vercel Environment Variables

In the existing KKAEUM Vercel project set:

```env
SALARY_GROWTH_API_URL=https://YOUR-MODEL-SERVICE
SALARY_GROWTH_API_KEY=the-same-secret-as-MODEL_API_KEY
SALARY_GROWTH_API_TIMEOUT_SECONDS=20
```

Then redeploy Vercel.

## What changed

Before:

```text
Vercel -> import catboost -> load catboost_m3.cbm -> predict
```

After:

```text
Vercel -> HTTP /predict -> external CatBoost service -> prediction
```

Occupation normalization, salary preprocessing, metadata, age curve blending,
DB/DC projection logic and the existing `/api/salary-growth/*` routes remain
inside the original KKAEUM project.
