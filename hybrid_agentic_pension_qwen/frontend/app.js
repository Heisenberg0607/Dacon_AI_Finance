const $ = (id) => document.getElementById(id);
let catalog = {providers: [], products: []};
let lastResult = null;
let analysisId = null;
let chatHistory = [];

// v10: numeric inputs stay directly editable, but native stepper/wheel/arrow increment behavior is disabled.
document.querySelectorAll('input[type="number"]').forEach(input => {
  input.addEventListener('wheel', event => {
    if(document.activeElement === input) event.preventDefault();
  }, {passive:false});
  input.addEventListener('keydown', event => {
    if(event.key === 'ArrowUp' || event.key === 'ArrowDown') event.preventDefault();
  });
});

function fmtMoney(v){
  // v18: 내부 금액은 만원 스케일을 유지하고, 3단계에서는 실제 원화 숫자로 표시한다.
  // 예: 5,000 -> 50,000,000
  const n = Number(v ?? 0);
  if(!Number.isFinite(n)) return '-';
  const won = Math.round(n * 10000);
  return won.toLocaleString('ko-KR');
}
function fmtPct(v, d=0){ return `${Number(v||0).toFixed(d)}%`; }
function fmtDuration(sec){
  // 서버가 실측한 초 단위 소요시간을 사람이 읽는 형태로 바꾼다. 값이 없으면 만들어내지 않는다.
  const n = Number(sec);
  if(!isFinite(n) || n < 0) return '-';
  // 1초 미만은 소수 첫째 자리로 자르면 0.04초가 '0.0초'로 보인다. 자릿수를 늘려 실측값을 그대로 보여준다.
  if(n < 1) return `${n.toFixed(2)}초`;
  if(n < 60) return `${n.toFixed(1)}초`;
  const m = Math.floor(n/60), s2 = n - m*60;
  return `${m}분 ${s2.toFixed(0)}초`;
}
function fmtDateTime(iso){
  if(!iso) return '-';
  const d = new Date(iso);
  if(isNaN(d)) return '-';
  const p2 = (x)=>String(x).padStart(2,'0');
  return `${d.getFullYear()}-${p2(d.getMonth()+1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}
/* ---- v23: 서버 응답 없음(Failed to fetch)을 사람이 읽을 수 있는 안내로 바꾼다 ---- */
// fetch는 서버가 응답을 못 주면 TypeError로 거절되고, 그 message는 브라우저 원문
// "Failed to fetch"다. 지금까지는 그 문자열이 화면에 그대로 찍혀서, 원인이 서버가 안 떠
// 있는 것뿐인데도 계산 기능이 고장난 것처럼 보였다. 이 앱에서 그 실패는 사실상 하나뿐이라
// (로컬 uvicorn이 꺼졌거나 재시작 중이라 연결 자체가 안 된다) 원인과 조치를 한국어로 적는다.
//
// HTTP 응답이 온 실패(4xx/5xx)는 서버가 살아 있다는 뜻이므로 여기로 오지 않는다.
// 그쪽은 각 호출부가 상태코드를 보고 따로 처리한다. 둘을 뭉뚱그리면 안내가 다시 틀려진다.
class ServerUnreachableError extends Error {
  constructor(message){ super(message); this.name = 'ServerUnreachableError'; }
}
const SERVER_DOWN_MESSAGE = '서버에 연결하지 못했습니다. 분석 서버(uvicorn)가 실행 중인지 확인한 뒤 다시 시도해주세요.';
const REQUEST_TIMEOUT_MESSAGE = '서버가 제한 시간 안에 응답하지 않아 요청을 중단했습니다. 서버 로그를 확인한 뒤 다시 시도해주세요.';

// 상품 비교의 첫 계산은 상품 PDF 구조화(Qwen 호출 1회)를 포함해 수십 초가 걸릴 수 있다.
// 그렇다고 상한이 없으면 서버가 멈췄을 때 버튼이 '계산 중...'에 갇혀 되돌릴 방법이 없다.
const REQUEST_TIMEOUT_MS = 180000;

// 서버가 응답은 했지만 실패한 경우. FastAPI는 detail에 한국어 사유를 담아 보내므로 그것을 쓰고,
// detail이 없으면(500 트레이스백 등) 원문 대신 정해둔 안내로 대체한다. 응답 본문을 그대로
// 화면에 찍으면 사용자에게는 읽을 수 없는 문자열이고 구현 내부만 새어 나간다.
async function httpErrorMessage(res, fallback){
  try{
    const body = await res.json();
    const detail = body && body.detail;
    if(typeof detail === 'string' && detail.trim()) return detail;
  }catch(_){ /* JSON이 아니면 fallback */ }
  return `${fallback} (HTTP ${res.status})`;
}

// 화면에 그대로 보여줘도 되는, 이미 한국어로 만들어 둔 실패 사유.
class CompareError extends Error {
  constructor(message){ super(message); this.name = 'CompareError'; }
}

async function postJson(path, body, timeoutMs = REQUEST_TIMEOUT_MS){
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try{
    return await fetch(path, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body), signal:controller.signal,
    });
  }catch(err){
    throw new ServerUnreachableError(
      err && err.name === 'AbortError' ? REQUEST_TIMEOUT_MESSAGE : SERVER_DOWN_MESSAGE);
  }finally{
    clearTimeout(timer);
  }
}

function esc(v){ return String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;'); }

async function init(){
  try{
    const response = await fetch('/api/catalog', { cache: 'no-store' });
    if(!response.ok) throw new Error(`catalog HTTP ${response.status}`);
    const c = await response.json();
    if(!Array.isArray(c.providers) || !Array.isArray(c.products)) throw new Error('catalog 응답 형식이 올바르지 않습니다.');
    catalog = c;
    fillProviders();
  }catch(e){
    console.error('상품 catalog 로딩 실패:', e);
    const provider = $('provider');
    const product = $('productName');
    if(provider) provider.innerHTML = '<option value="">상품 정보를 불러올 수 없습니다</option>';
    if(product) product.innerHTML = '<option value="">상품 정보를 불러올 수 없습니다</option>';
  }
  setOperationType($('operationType').value || 'DC');
  updateAdditionalTenure();
  setStageScope($('operationType').value || 'DC');
  resetNodes();
}

function fillProviders(){
  const p = $('provider');
  if(!p) return;
  p.innerHTML = catalog.providers.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
  if(catalog.providers.length) p.value = catalog.providers.includes('KB국민은행') ? 'KB국민은행' : catalog.providers[0];
  fillProducts();
}

function fillProducts(){
  if($('operationType').value === 'DB') return;
  const provider = $('provider').value;
  const p = $('productName');
  const items = catalog.products.filter(x=>x.provider===provider);
  p.innerHTML = items.map(x=>`<option value="${esc(x.title)}" data-risk="${esc(x.risk_type||'')}">${esc(x.title)}</option>`).join('');
  if(items.length){
    const neutral = items.findIndex(x=>x.risk_type==='중립투자형');
    p.selectedIndex = neutral >= 0 ? neutral : 0;
    syncRiskFromProduct();
  }else{
    p.innerHTML = '<option value="">등록된 상품이 없습니다</option>';
  }
}

function syncRiskFromProduct(){
  if($('operationType').value === 'DB') return;
  const opt = $('productName').selectedOptions[0];
  const risk = opt?.dataset?.risk;
  const investment = $('investmentType');
  if(risk && ['안정형','안정투자형','중립투자형','적극투자형'].includes(risk)){
    investment.value = risk;
    investment.disabled = true;
    investment.required = false;
  }else{
    investment.disabled = false;
    investment.required = true;
  }
}

function toggleFieldControls(container, enabled){
  if(!container) return;
  container.querySelectorAll('input,select,button').forEach(el=>{
    if(el.id === 'estimateWageBtn'){
      el.disabled = !enabled;
      return;
    }
    el.disabled = !enabled;
  });
}

function setOperationType(op){
  $('operationType').value = op;
  document.querySelectorAll('.operation-btn').forEach(btn=>{
    const active = btn.dataset.operation === op;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });

  const isDB = op === 'DB';
  $('dbFields').classList.toggle('is-hidden', !isDB);
  $('dcIrpFields').classList.toggle('is-hidden', isDB);
  toggleFieldControls($('dbFields'), isDB);
  toggleFieldControls($('dcIrpFields'), !isDB);

  $('currentTenureYears').required = isDB;
  $('currentSavings').required = !isDB;
  $('annualContribution').required = !isDB;
  $('provider').required = !isDB;
  $('productName').required = !isDB;

  if(!isDB){
    $('dcIrpTypeChip').textContent = op;
    fillProducts();
  }
  updateAdditionalTenure();
}

document.querySelectorAll('.operation-btn').forEach(btn=>{
  btn.addEventListener('click',()=>setOperationType(btn.dataset.operation));
});
$('provider').addEventListener('change', fillProducts);
$('productName').addEventListener('change', syncRiskFromProduct);

function updateAdditionalTenure(){
  const age = Number($('age').value || 0);
  const retirement = Number($('retirementAge').value || 0);
  const years = Math.max(0, retirement - age);
  $('additionalTenureDisplay').textContent = `${years}년`;
}
$('age').addEventListener('input', updateAdditionalTenure);
$('retirementAge').addEventListener('input', updateAdditionalTenure);

function getSalaryHistory(includeCurrent=true){
  const ids=['salary3YearsAgo','salary2YearsAgo','salary1YearAgo'];
  const vals=ids.map(id=>Number($(id).value)).filter(v=>Number.isFinite(v)&&v>0);
  if(includeCurrent && vals.length) vals.push(Number($('annualIncome').value));
  return vals;
}

async function previewWageEstimate(){
  if($('operationType').value !== 'DB') return;
  const btn=$('estimateWageBtn');
  const original=btn.textContent;
  btn.disabled=true;
  btn.textContent='추정 중...';
  try{
    const data=payload();
    data.wage_growth_rate=null;
    const res=await fetch('/api/estimate-wage-growth',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(data),
    });
    if(!res.ok) throw new Error(await res.text());
    const estimate=await res.json();
    $('wageGrowthRate').value='';
    $('wageGrowthRate').placeholder=`자동 추정 약 ${Number(estimate.rate_pct).toFixed(2)}%`;
    $('wageGrowthHint').textContent=`깨움 자동 추정: 약 ${Number(estimate.rate_pct).toFixed(2)}% · ${estimate.explanation}`;
  }catch(err){
    console.error('임금상승률 추정 실패:',err);
    $('wageGrowthHint').textContent='자동 추정에 실패했습니다. 입력값을 확인하거나 직접 임금상승률을 입력해주세요.';
  }finally{
    btn.disabled=false;
    btn.textContent=original;
  }
}
$('estimateWageBtn').addEventListener('click', previewWageEstimate);

function nullableNumber(id){
  const raw=$(id).value;
  if(raw === '' || raw == null) return null;
  const n=Number(raw);
  return Number.isFinite(n) ? n : null;
}

function payload(){
  const op = $('operationType').value;
  const common = {
    age:Number($('age').value),
    retirement_age:Number($('retirementAge').value),
    annual_income:Number($('annualIncome').value),
    desired_monthly_income:Number($('desiredMonthlyIncome').value),
    operation_type:op,
  };

  if(op === 'DB'){
    return {
      ...common,
      current_savings:null,
      annual_contribution:null,
      provider:null,
      product_name:null,
      investment_type:null,
      current_tenure_years:nullableNumber('currentTenureYears'),
      wage_growth_rate:nullableNumber('wageGrowthRate'),
      industry_job:$('industryJob').value.trim() || null,
      company_size:$('companySize').value || null,
      salary_history:getSalaryHistory(true),
    };
  }

  return {
    ...common,
    current_savings:Number($('currentSavings').value),
    annual_contribution:Number($('annualContribution').value),
    provider:$('provider').value,
    product_name:$('productName').value,
    investment_type:$('investmentType').value,
    current_tenure_years:null,
    wage_growth_rate:null,
    industry_job:null,
    company_size:null,
    salary_history:[],
  };
}


const TOOL_KO = {
  'Pension AI Agent':'깨움 AI 에이전트',
  'Profile Tool':'사용자 프로필 분석',
  'RAG Tool':'금융지식 검색',
  'extract_selected_product_pdf':'상품정보 구조화 추출',
  'Finance Engine':'금융 계산 엔진',
  'Monte Carlo Simulation':'몬테카를로 시뮬레이션',
  'Portfolio Optimizer':'포트폴리오 최적화',
  // 백엔드 trace가 실제로 담는 tool 이름(function calling 이름)도 한글로 표시한다.
  'analyze_profile':'사용자 프로필 분석',
  'search_product_rag':'금융지식 검색',
  'run_finance_engine':'금융 계산 엔진',
  'run_monte_carlo':'몬테카를로 시뮬레이션',
  'optimize_retirement_strategy':'포트폴리오 최적화',
  'Recommendation Agent':'맞춤 전략 생성',
  'Critic Agent':'전략 검증 에이전트',
  'Report Generator':'보고서 생성'
};
function toolKo(name){ return TOOL_KO[name] || name || ''; }
function statusKo(status){
  const m={running:'진행 중',done:'완료',retry:'재검토',wait:'대기',error:'오류','revised-with-warnings':'수정 후 완료'};
  return m[status] || status || '';
}
function ragModeKo(value){
  if(!value) return '-';
  const s=String(value).toLowerCase();
  if(s.includes('exact-product') && s.includes('semantic')) return '선택 상품 PDF 한정 · 의미 기반 검색';
  if(s.includes('exact-product')) return '선택 상품 PDF 한정 검색';
  if(s.includes('semantic') && s.includes('hybrid')) return '의미 기반 + 키워드 혼합 검색';
  if(s.includes('semantic')) return '의미 기반 검색';
  if(s.includes('lexical') || s.includes('keyword')) return '키워드 기반 검색';
  if(s.includes('disabled') || s.includes('none')) return '미적용';
  return '금융문서 검색';
}

// v21: 단계별 카드 9개를 한 칸짜리 스테퍼로 바꿨다.
// 한 칸 안에서 현재 단계만 보여주고 다음 단계로 넘어가며, 하단 레일이 전체 진행 위치를 알려준다.
// [stage, 코드, 제목, 설명] — 화면에 나오는 단계 정의의 단일 출처.
const STAGES=[
  ['profile','PF','사용자 프로필 분석','투자기간 · 위험수용능력'],
  ['rag','RG','금융지식 검색','선택 상품 PDF 내부 근거 검색'],
  ['product_extraction','PX','상품정보 구조화 추출','선택한 공식 PDF에서 구성상품 · 비중 · 위험정보를 Qwen이 구조화'],
  ['finance','FN','금융 계산 엔진','목표자산 · 예상자산 · Gap'],
  ['monte_carlo','MC','몬테카를로 시뮬레이션','목표달성 확률 분포'],
  ['optimizer','OP','포트폴리오 최적화','제약 내 후보 전략 탐색'],
  ['recommendation','RA','맞춤 전략 생성','RAG + 계산결과를 결합한 개인화 전략'],
  ['critic','CR','전략 검증 에이전트','적합성 · 근거 · 숫자 · 운영유형 검증'],
  ['report','RP','보고서 생성','검증된 결과만 최종 보고서 반영'],
];
let stageScope=STAGES.map(x=>x[0]);  // 이번 분석에서 실제로 실행되는 단계만 남긴다
let stageState={};                   // stage -> wait | running | done | retry
let stageFocus=null;                 // 지금 칸에 떠 있는 단계

function stageMeta(stage){ return STAGES.find(x=>x[0]===stage); }
function setStageScope(operationType){
  // DB형은 개인 선택 상품 PDF 구조화 추출을 실행하지 않으므로 레일에서도 뺀다.
  stageScope=STAGES.map(x=>x[0]).filter(st=>!(operationType==='DB' && st==='product_extraction'));
  const rail=$('stageRail');
  if(rail){
    rail.innerHTML=stageScope.map(st=>{
      const m=stageMeta(st);
      return `<li data-stage="${st}" title="${esc(m[2])}"><span>${esc(m[1])}</span></li>`;
    }).join('');
  }
  const total=$('stageTotal'); if(total) total.textContent=String(stageScope.length).padStart(2,'0');
}
function renderStage(){
  const rail=$('stageRail'); if(!rail) return;
  stageScope.forEach(st=>{
    const li=rail.querySelector(`[data-stage="${st}"]`); if(!li) return;
    const state=stageState[st]||'wait';
    li.className=state==='wait' ? '' : state;
    if(st===stageFocus) li.classList.add('current');
  });
  const focus=stageFocus||stageScope[0];
  const m=stageMeta(focus); if(!m) return;
  const idx=stageScope.indexOf(focus);
  const state=stageState[focus]||'wait';
  $('stageIndex').textContent=String(idx+1).padStart(2,'0');
  $('stageCode').textContent=m[1];
  $('stageTitle').textContent=m[2];
  $('stageDesc').textContent=m[3];
  $('stageStatus').textContent=statusKo(state);
  const next=stageScope[idx+1];
  $('stageNext').textContent=next ? `다음 · ${stageMeta(next)[2]}` : '마지막 단계';
  const card=$('stageCard');
  card.classList.remove('is-running','is-done','is-retry');
  if(state==='running') card.classList.add('is-running');
  else if(state==='done') card.classList.add('is-done');
  else if(state==='retry') card.classList.add('is-retry');
}
function playStageTransition(){
  // 칸 내용이 바뀔 때만 전환 애니메이션을 다시 태운다.
  const body=$('stageBody'); if(!body) return;
  body.classList.remove('is-entering');
  void body.offsetWidth;
  body.classList.add('is-entering');
}
// 레일에 존재하는 단계인지 확인하는 용도. 알 수 없는 stage(planner 등)는 null.
function node(stage){ return document.querySelector(`#stageRail [data-stage="${stage}"]`); }
function setNode(stage, status){
  if(!stageMeta(stage) || !stageScope.includes(stage)) return;
  stageState[stage]=status;
  // 진행/재검토로 들어온 단계가 칸의 주인공이 된다. 완료는 넘어가기 전까지 그대로 보여준다.
  if((status==='running'||status==='retry') && stageFocus!==stage){
    stageFocus=stage;
    playStageTransition();
  }
  renderStage();
}
function resetNodes(){ stageState={}; stageFocus=stageScope[0]; renderStage(); }
const delay = ms => new Promise(r=>setTimeout(r,ms));

// v20: 2단계 대기 화면의 "예상 남은 시간" 게이지.
// 근거는 서버가 perf_counter로 실측해 쌓은 과거 소요시간(/api/eta)뿐이다.
// 이력이 없거나 예상치를 넘긴 뒤에는 남은 시간을 지어내지 않고 비확정 상태로 표시한다.
const WAIT_RING_LENGTH = 477.52;  // 2 * PI * r(76). styles.css의 stroke-dasharray와 같은 값.
let waitTimer=null, waitStartedAt=0, waitEstimate=null;

function fmtRemaining(sec){
  // 남은 시간은 초 단위로 올림해 0초에서 멈춘 것처럼 보이지 않게 한다.
  const n=Math.max(0, Math.ceil(Number(sec)||0));
  if(n<60) return `${n}초`;
  const m=Math.floor(n/60), s2=n%60;
  return s2 ? `${m}분 ${s2}초` : `${m}분`;
}
function setWaitProgress(ratio){
  const r=Math.max(0, Math.min(1, Number(ratio)||0));
  const ring=$('waitRing'); if(ring) ring.style.strokeDashoffset=String(WAIT_RING_LENGTH*(1-r));
  const bar=$('waitBar'); if(bar) bar.style.width=`${(r*100).toFixed(1)}%`;
}
function waitBasisText(est){
  // 표시된 숫자가 어디서 온 값인지 그대로 밝힌다. source와 percentile 모두 서버가 알려준다.
  // 예상치는 백분위수라 '표본의 N%가 이 시간 안에 끝났다'가 문자 그대로 참이다.
  //
  // fixed만은 백분위수가 아니라 미리 정해둔 한 값이라 percentile이 null로 온다.
  // 근거로 적을 표본이 없으므로 빈 문자열을 돌려주고, 화면에는 경과 시간만 남긴다.
  // '80%가 이내 완료' 문구를 그대로 쓰면 표본에서 나온 값이 아니라 거짓말이 된다.
  if(est.source==='fixed') return '';
  const within=`${est.percentile}%가 ${fmtDuration(est.expected_seconds)} 이내 완료`;
  if(est.source==='related') return `${est.basis_operation_type} 실측 ${est.sample_size}회 중 ${within} (이 유형 이력 없음)`;
  if(est.source==='baseline') return `기본 측정치 ${est.sample_size}회 중 ${within}`;
  return `최근 ${est.sample_size}회 중 ${within}`;
}
function renderWaitMeter(){
  const card=$('agentCoreCard'); if(!card) return;
  const elapsed=(performance.now()-waitStartedAt)/1000;
  const elapsedText=`경과 ${fmtDuration(elapsed)}`;
  if(!waitEstimate){
    // 실측 이력이 없는 첫 분석. 남은 시간을 추정할 근거가 없다.
    card.classList.add('is-indeterminate'); card.classList.remove('is-overrun');
    $('waitLabel').textContent='분석 진행 중';
    $('waitRemaining').textContent='예상 시간 산출 전';
    $('waitBasis').textContent=`${elapsedText} · 실측 이력이 쌓이면 남은 시간을 표시합니다`;
    return;
  }
  const expected=waitEstimate.expected_seconds;
  const basisText=waitBasisText(waitEstimate);
  const basis=basisText ? `${elapsedText} · ${basisText}` : elapsedText;
  const remaining=expected-elapsed;
  if(remaining<=0){
    // 예상치를 넘겼다. 남은 시간을 새로 지어내지 않고 초과 상태만 알린다.
    card.classList.add('is-indeterminate','is-overrun');
    $('waitLabel').textContent='예상 시간 초과';
    $('waitRemaining').textContent='마무리 중입니다';
    $('waitBasis').textContent=basis;
    setWaitProgress(1);
    return;
  }
  card.classList.remove('is-indeterminate','is-overrun');
  $('waitLabel').textContent='예상 남은 시간';
  $('waitRemaining').textContent=`약 ${fmtRemaining(remaining)}`;
  $('waitBasis').textContent=basis;
  setWaitProgress(elapsed/expected);
}
function startWaitMeter(operationType){
  waitStartedAt=performance.now(); waitEstimate=null;
  const card=$('agentCoreCard'); if(card) card.classList.remove('is-overrun');
  $('waitMeter').classList.remove('hidden');
  setWaitProgress(0);
  renderWaitMeter();
  waitTimer=setInterval(renderWaitMeter, 250);
  // 예상치 조회가 분석 요청을 늦추지 않도록 병렬로 가져오고, 도착하면 그때부터 반영한다.
  fetch(`/api/eta?operation_type=${encodeURIComponent(operationType)}`, {cache:'no-store'})
    .then(r=>r.ok?r.json():null)
    .then(d=>{ if(d && d.available && waitTimer){ waitEstimate=d; renderWaitMeter(); } })
    .catch(()=>{ /* 예상치는 부가 정보다. 실패해도 비확정 표시로 계속 진행한다. */ });
}
function finishWaitMeter(result){
  if(waitTimer) clearInterval(waitTimer);
  waitTimer=null;
  const card=$('agentCoreCard'); if(!card) return;
  card.classList.remove('is-indeterminate','is-overrun');
  const total=result && result.timing ? result.timing.total_seconds : null;
  if(total==null){ $('waitMeter').classList.add('hidden'); return; }
  setWaitProgress(1);
  $('waitLabel').textContent='분석 완료';
  $('waitRemaining').textContent='0초';
  $('waitBasis').textContent=`실측 소요시간 ${fmtDuration(total)}`;
}

let progressTimer=null; let progressIndex=0;
// 서버 응답을 기다리는 동안 에이전트 카드에 띄울 단계별 안내 문구.
// 단계 순서는 STAGES(stageScope) 하나만 따르므로 여기서는 문구만 들고 있다.
const PENDING_MESSAGE={
  profile:'사용자 프로필을 구조화합니다.',
  rag:'선택한 상품의 공식 PDF 내부에서 근거를 검색합니다.',
  product_extraction:'Qwen이 선택 상품 PDF에서 구성상품과 비중을 구조화합니다.',
  finance:'PDF 추출값을 Python 금융엔진에 넣어 목표자산과 예상 은퇴자산을 계산합니다.',
  monte_carlo:'확률 기반 은퇴자산 분포를 시뮬레이션합니다.',
  optimizer:'제약조건 안에서 후보 전략을 탐색합니다.',
  recommendation:'개인화 추천안을 생성합니다.',
  critic:'추천의 적합성과 근거를 검증합니다.',
  report:'검증된 결과로 보고서를 생성합니다.',
};
// v28: 단계 표시는 서버가 보내는 실제 진행 이벤트를 따른다.
// simulate:true는 스트리밍을 쓸 수 없을 때만 쓰는 대체 경로다(아래 startSimulatedStages 주석 참고).
function startPendingAnimation(operationType, options){
  startWaitMeter(operationType);
  setStageScope(operationType);
  resetNodes(); progressIndex=0;
  $('agentState').textContent='분석을 시작합니다.';
  if(options && options.simulate) startSimulatedStages();
}

// 서버가 보낸 단계 이벤트 하나를 레일에 반영한다.
// 서버 status는 running / done / retry / error / revised-with-warnings가 올 수 있는데
// 레일에 스타일이 있는 상태는 running / done / retry뿐이라 나머지는 가장 가까운 쪽으로 접는다.
// stageScope에 없는 stage(planner, 도구 이름 등)는 setNode가 알아서 무시한다.
function applyStageEvent(ev){
  const stage=ev && ev.stage; if(!stage) return;
  const status = ev.status==='running' ? 'running'
    : (ev.status==='retry' || ev.status==='error') ? 'retry'
    : 'done';
  setNode(stage, status);
  if(status==='running'){
    // v34: 단계 아래 부연 줄(agentDetail)을 통째로 없앴다. 거기 있던 문구는 어떤 방식으로
    // 골랐는지(Qwen 도구 선택 / 안전 실행 로직) 같은 내부 구현이었고, 지금 무엇을 하는
    // 중인지는 이 줄이 이미 말한다.
    $('agentState').textContent=PENDING_MESSAGE[stage] || toolKo(ev.tool || stage);
  }
}

// 대체 경로: 서버 진행 이벤트를 받을 수 없을 때만 쓰는 900ms 타이머.
// 이 표시는 서버 상태와 무관한 '추정'이라 실제 소요시간 분포를 반영하지 못한다.
// 스트리밍이 되는 환경에서는 절대 쓰지 않는다.
function startSimulatedStages(){
  const stages=stageScope.slice();
  const tick=()=>{
    // 앞 단계만 완료로 넘긴다. 마지막 단계(보고서 생성)는 여기서 절대 done을 찍지 않는다.
    // 이 애니메이션은 서버 응답을 기다리는 동안 도는 '추정'이라, 마지막 칸까지 완료로 바꾸면
    // 서버가 아직 보고서를 만들고 있는데도 카드 오른쪽 위에 '완료'가 떠서 거짓말이 된다.
    // 마지막 단계의 완료는 실제 응답이 도착한 finishTrace()만 찍는다.
    if(progressIndex>0 && progressIndex<stages.length) setNode(stages[progressIndex-1],'done');
    if(progressIndex<stages.length){
      const s=stages[progressIndex]; setNode(s,'running');
      $('agentState').textContent=PENDING_MESSAGE[s]||'';
      progressIndex++;
      return;
    }
    // 마지막 단계에 도착했다. 더 추정할 것이 없으므로 타이머를 멈춘다.
    // 무엇을 기다리는지는 마지막 단계 이름과 대기 게이지가 말한다.
    clearInterval(progressTimer); progressTimer=null;
  };
  tick(); progressTimer=setInterval(tick,900);
}
function stopPendingAnimation(result){ if(progressTimer) clearInterval(progressTimer); progressTimer=null; finishWaitMeter(result); }

// 서버가 분석에 실패했다고 알려온 경우. 스트리밍을 다시 시도해도 같은 결과이므로
// 대체 경로로 넘어가지 않고 그대로 실패시킨다.
class AnalyzeError extends Error {
  constructor(message){ super(message); this.name='AnalyzeError'; }
}
// 이 브라우저/서버 조합에서 스트리밍을 쓸 수 없다는 뜻. 분석 자체의 실패가 아니므로
// 기존 blocking 경로로 다시 시도한다.
class StreamUnavailable extends Error {}

// POST /api/analyze/stream을 읽어 단계 이벤트를 onStage로 넘기고, 최종 결과를 돌려준다.
// EventSource는 GET만 지원해서 요청 본문을 실을 수 없으므로 fetch + ReadableStream으로 읽는다.
async function analyzeStreaming(data, onStage){
  const res = await postJson('/api/analyze/stream', data);
  // 404/405 = 스트리밍을 모르는 예전 서버. 그 밖의 실패는 분석 자체의 문제다.
  if(res.status===404 || res.status===405) throw new StreamUnavailable(`HTTP ${res.status}`);
  if(!res.ok) throw new AnalyzeError(await httpErrorMessage(res, '분석에 실패했습니다.'));
  if(!res.body || !res.body.getReader) throw new StreamUnavailable('ReadableStream 미지원');

  const reader=res.body.getReader(), decoder=new TextDecoder();
  let buffer='', result=null, sawEvent=false;
  for(;;){
    const {value, done} = await reader.read();
    if(done) break;
    buffer += decoder.decode(value, {stream:true});
    // SSE 이벤트는 빈 줄로 끊긴다. 마지막 조각은 아직 덜 왔을 수 있으므로 버퍼에 남긴다.
    let cut;
    while((cut = buffer.indexOf('\n\n')) >= 0){
      const chunk = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 2);
      if(!chunk.startsWith('data:')) continue;   // ': keepalive' 주석행
      let ev; try{ ev = JSON.parse(chunk.slice(5).trim()); }catch(_){ continue; }
      sawEvent = true;
      if(ev.type==='stage') onStage(ev);
      else if(ev.type==='result') result = ev.result;
      else if(ev.type==='error') throw new AnalyzeError(ev.message || '분석에 실패했습니다.');
    }
  }
  if(!result){
    // 결과 이벤트 없이 스트림이 끊겼다. 이벤트를 하나도 못 받았다면 중간에서 버퍼링됐을
    // 가능성이 크므로 대체 경로를 시도하고, 받다가 끊긴 경우는 진짜 실패로 본다.
    if(sawEvent) throw new AnalyzeError('분석 결과를 받지 못한 채 연결이 끊겼습니다.');
    throw new StreamUnavailable('결과 이벤트 없음');
  }
  return result;
}

async function runAnalysis(data){
  try{
    return await analyzeStreaming(data, applyStageEvent);
  }catch(err){
    if(err instanceof AnalyzeError || err instanceof ServerUnreachableError) throw err;
    console.warn('스트리밍 분석을 쓸 수 없어 기존 방식으로 전환합니다:', err);
  }
  // 대체 경로: 단계 진행을 알 수 없으므로 타이머로 흉내 낸다.
  startSimulatedStages();
  const res = await postJson('/api/analyze', data);
  if(!res.ok) throw new AnalyzeError(await httpErrorMessage(res, '분석에 실패했습니다.'));
  return await res.json();
}

$('pensionForm').addEventListener('submit', async(e)=>{
  e.preventDefault();
  const data=payload();
  if(data.retirement_age<=data.age){ alert('은퇴 나이는 현재 나이보다 커야 합니다.'); return; }
  const btn=$('submitBtn'); btn.disabled=true; btn.querySelector('span').textContent='AI 분석 중...';
  $('inputView').classList.add('hidden'); $('workflowView').classList.remove('hidden'); $('reportView').classList.add('hidden');
  $('modeBadge').textContent='AI 분석 진행 중';
  startPendingAnimation(data.operation_type, {simulate:false});
  try{
    const result=await runAnalysis(data);
    lastResult=result; analysisId=result.analysis_id||null; stopPendingAnimation(result); await finishTrace(result); renderReport(result); await delay(350); $('workflowView').classList.add('hidden'); $('reportView').classList.remove('hidden'); window.scrollTo({top:0,behavior:'smooth'});
  }catch(err){
    stopPendingAnimation();
    console.error('분석 실패:', err);
    // 서버가 아예 응답하지 않은 경우까지 'Qwen 설정을 확인하라'고 안내하면 엉뚱한 곳을 보게 된다.
    // AnalyzeError는 서버가 알려준 실제 사유를 담고 있으므로 그대로 보여준다.
    alert((err instanceof ServerUnreachableError || err instanceof AnalyzeError)
      ? err.message
      : '분석 중 오류가 발생했습니다. .env의 Qwen 설정 또는 서버 로그를 확인해주세요.');
    $('inputView').classList.remove('hidden'); $('workflowView').classList.add('hidden');
  }
  finally{ btn.disabled=false; btn.querySelector('span').textContent='깨움 분석 시작'; }
});

// 응답이 온 뒤 남은 단계를 마무리하는 간격. 대기 애니메이션(900ms)보다 짧게 둔다.
// 이미 끝난 일을 확정하는 중이므로 기다리게 할 이유가 없다.
const FINISH_STEP_MS = 120;

// v24: 완료 시 레일을 처음부터 다시 걸어가지 않는다.
//
// 이전 구현은 resetNodes()로 진행 상황을 통째로 지운 뒤 트레이스 전체를 180ms 간격으로
// 다시 걸어갔다. 대기 중에 이미 한 번 진행한 레일이 완료 직후 처음으로 되감겼다가 다시 도니까,
// 서버가 같은 파이프라인을 두 번 실행하는 것처럼 보였다.
//
// 지금은 대기 애니메이션이 멈춘 지점에서 '이어서' 마무리한다. 이미 지나간 단계는 그대로 두고,
// 아직 확정되지 않은 단계만 서버 트레이스의 실제 결과로 닫는다. 그래서 레일은 화면에 떠 있는
// 동안 정확히 한 번만 흘러간다.
//
// Qwen 모드(수십 초)에서는 애니메이션이 이미 마지막 단계에 도착해 있어 마지막 칸만 완료로 바뀌고,
// demo 모드(0.1초 미만)에서는 아직 첫 단계이므로 남은 단계가 여기서 한 번 흘러간다. 어느 쪽이든
// 되감기는 없다.
async function finishTrace(result){
  $('modeBadge').textContent=result.mode.qwen_enabled?'Qwen 에이전트':'안전 실행 모드';
  $('workflowSubtitle').textContent=`${result.mode.qwen_enabled?'Qwen 에이전트가 도구를 선택':'안전 실행 로직 적용'} · ${ragModeKo(result.mode.rag)} · 분석 반복 ${result.mode.iterations}회`;

  // 한 단계가 재시도로 두 번 실행되면 트레이스에 두 번 등장한다. 마지막 항목이 그 단계의
  // 최종 상태다. 레일에 없는 stage(planner 등)는 건너뛴다.
  const lastEntry=new Map();
  for(const t of result.trace){ if(node(t.stage)) lastEntry.set(t.stage, t); }

  for(const stage of stageScope){
    const state=stageState[stage];
    if(state==='done' || state==='retry') continue;  // 대기 중에 이미 확정된 단계는 다시 건드리지 않는다
    const t=lastEntry.get(stage);
    if(t) $('agentState').textContent=toolKo(t.tool || stage);
    if(state!=='running') setNode(stage,'running');
    await delay(FINISH_STEP_MS);
    setNode(stage, (t && t.status==='retry') ? 'retry' : 'done');
  }

  // v34: 총 소요시간은 보고서 표지의 소요시간 칩(renderTiming)이 이미 실측값으로 보여준다.
  $('agentState').textContent='분석 완료';
}

function renderTiming(timing){
  // 서버가 실측한 값이 없으면 소요시간 영역을 감춘다. 프런트에서 임의로 추정하지 않는다.
  const box = document.querySelector('.report-timing');
  if(!box) return;
  if(!timing || timing.total_seconds == null){ box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  $('reportElapsed').textContent = fmtDuration(timing.total_seconds);
  $('reportGeneratedAt').textContent = `생성 완료 ${fmtDateTime(timing.finished_at)}`;
  const stages = Array.isArray(timing.stages) ? timing.stages : [];
  $('reportElapsed').parentElement.title = stages.length
    ? stages.map(s=>`${toolKo(s.tool || s.stage)} ${fmtDuration(s.seconds)}${s.runs>1?` (${s.runs}회)`:''}`).join('\n')
    : '';
}

// v31: 분석 근거가 된 상품설명서 원문을 그대로 받아볼 수 있게 한다.
// 서버가 Content-Disposition: attachment로 내려주므로 평범한 링크면 충분하다.
// fileId가 없으면(DB형이거나 원문을 못 찾은 경우) 버튼 자체를 숨긴다. 눌러야 없다는 걸
// 알게 되는 버튼보다 없는 편이 낫다.
function setSourcePdfLink(fileId, filename){
  const btn=$('sourcePdfBtn');
  if(!fileId){ btn.classList.add('hidden'); btn.removeAttribute('href'); return; }
  btn.href=`/api/source-document?file_id=${encodeURIComponent(fileId)}`;
  $('sourcePdfName').textContent=filename || '';
  btn.classList.remove('hidden');
}

function renderReport(r){
  const u=r.user, f=r.finance, mc=r.monte_carlo, o=r.optimizer, rep=r.report, rec=r.recommendation;
  const isDB = u.operation_type === 'DB';
  $('reportTitleTop').textContent=rep.title||'AI 퇴직연금 건강검진 보고서';
  $('reportTitle').textContent=rep.title||'AI 퇴직연금 건강검진 보고서';
  $('reportMeta').textContent = isDB ? 'DB형 · 임금/근속 기반 분석' : `${u.provider} · ${u.product_name} · ${u.operation_type} · ${u.investment_type}`;
  renderTiming(r.timing);
  $('goalRate').textContent=fmtPct(f.goal_rate_pct);

  if(isDB){
    $('mCurrentLabel').textContent='현재 근속연수';
    $('mCurrent').textContent=`${Number(f.current_tenure_years||0).toFixed(1).replace('.0','')}년`;
    $('mCurrentSmall').textContent=`추가 ${f.additional_tenure_years}년 자동 계산`;
    $('mFutureLabel').textContent='예상 DB 퇴직급여';
    $('mFuture').textContent=fmtMoney(f.estimated_db_benefit ?? f.future_asset);
    $('mFutureSmall').textContent=`임금상승률 ${Number(f.wage_growth_rate_pct||0).toFixed(2)}% 가정`;
    $('mTargetLabel').textContent='목표 은퇴자산';
    $('mTarget').textContent=fmtMoney(f.target_retirement_asset);
    $('mTargetSmall').textContent='4% 인출률 계산값 · 실제 원화 숫자';
    $('mProbabilityLabel').textContent='목표달성 확률';
    $('mProbability').textContent=fmtPct(mc.success_probability_pct,1);
    $('mProbabilitySmall').textContent='임금경로 몬테카를로';
  }else{
    $('mCurrentLabel').textContent='현재 적립금';
    $('mCurrent').textContent=fmtMoney(u.current_savings);
    $('mCurrentSmall').textContent='입력값을 실제 원화 숫자로 표시';
    $('mFutureLabel').textContent='예상 은퇴자산';
    $('mTargetLabel').textContent='목표 은퇴자산';
    $('mTarget').textContent=fmtMoney(f.target_retirement_asset);
    $('mTargetSmall').textContent='4% 인출률 계산값 · 실제 원화 숫자';
    $('mProbabilityLabel').textContent='목표달성 확률';
    $('mProbabilitySmall').textContent='몬테카를로 시뮬레이션';
  }

  $('executiveSummary').textContent=rep.executive_summary || rec.summary || '';
  const pf=r.profile;
  const profileFacts = isDB ? [
    ['은퇴까지',`${pf.years_to_retirement}년`],
    ['현재 근속연수',`${Number(pf.current_tenure_years||0).toFixed(1).replace('.0','')}년`],
    ['예상 추가 근속',`${pf.expected_additional_tenure_years}년 (자동)`],
    ['예상 총 근속',`${Number(pf.total_expected_tenure_years||0).toFixed(1).replace('.0','')}년`],
    ['예상 임금상승률',`${Number(pf.wage_growth?.rate_pct||0).toFixed(2)}%`],
    ['AI 진단',pf.diagnosis_hint],
  ] : [
    ['은퇴까지',`${pf.years_to_retirement}년`],
    ['객관적 위험수용능력',pf.risk_capacity],
    ['투자유형',pf.investment_type],
    ['연간 납입률',fmtPct(pf.saving_rate_pct,1)],
    ['희망 소득대체 수준',fmtPct(pf.desired_income_replacement_pct,1)],
    ['AI 진단',pf.diagnosis_hint],
  ];
  $('profileFacts').innerHTML=profileFacts.map(([a,b])=>`<div><b>${esc(a)}</b>${esc(b)}</div>`).join('');

  if(isDB){
    $('currentProduct').textContent='DB 급여 분석';
    $('productAnalysis').textContent=`개인 운용상품 대신 현재 연소득, 근속연수, 임금상승률을 이용해 예상 DB 퇴직급여를 계산했습니다. ${f.calculation_note||''}`;
    // DB형은 개인 선택 상품이 없어 내려받을 원문도 없다.
    setSourcePdfLink(null, null);
  }else{
    $('currentProduct').textContent=u.product_name || '-';
    const ext=r.product_extraction||{};
    const alloc=(ext.asset_allocation||[]).map(x=>`${x.component_name} ${Number(x.weight_pct||0).toFixed(1).replace('.0','')}%`).join(' · ');
    const extractionLine=ext.source_filename ? `\n\nPDF 구조화: ${ext.source_filename}${alloc?` / ${alloc}`:''}` : '';
    $('productAnalysis').textContent=(rep.product_analysis || rec.product_analysis || '') + extractionLine;
    setSourcePdfLink(ext.source_document_available ? ext.source_file_id : null, ext.source_filename);
  }

  $('simulationComment').textContent=rep.simulation_comment || '';
  $('strategyList').innerHTML=(rep.strategy||rec.actions||[]).map(x=>`<div>${esc(x)}</div>`).join('');

  // v30: '금융지식 근거'와 'AI 전략 검증' 섹션을 보고서 화면에서 뺐다.
  // 서버는 둘 다 계속 만든다. RAG 근거는 챗봇이 근거 칩으로 쓰고(setupChat),
  // Critic은 보고서 문장을 검증·재생성하는 파이프라인 단계라 화면 표시와 무관하게 필요하다.
  // 화면에서 지웠다고 백엔드를 지우면 검증 자체가 사라진다.
  $('riskNotes').innerHTML=(rep.risk_notes||[]).filter(Boolean).map(x=>`<div>${esc(x)}</div>`).join('');
  renderProjection(r);
  setupCompare(r);
  setupChat(r);
}

// v21: 선택 상품에 따라 달라지는 부분만 모아둔다.
// renderReport와 상품 비교 재계산이 같은 경로를 쓰도록 해서 차트와 숫자가 어긋나지 않게 한다.
// 여기서 다루지 않는 보고서 본문(종합요약·전략·근거·검증)은 항상 가입 상품 기준으로 남는다.
function renderProjection(r){
  const isDB = r.user.operation_type === 'DB';
  const f=r.finance, mc=r.monte_carlo, o=r.optimizer;
  $('goalRate').textContent=fmtPct(f.goal_rate_pct);
  $('mProbability').textContent=fmtPct(mc.success_probability_pct,1);
  if(isDB){
    $('mFuture').textContent=fmtMoney(f.estimated_db_benefit ?? f.future_asset);
    $('mFutureSmall').textContent=`임금상승률 ${Number(f.wage_growth_rate_pct||0).toFixed(2)}% 가정`;
    $('allocationBars').innerHTML='<div class="db-allocation-note">DB형은 개인 자산배분 최적화 대신 예상 DB 급여와 희망 노후소득의 Gap을 분석합니다.</div>';
  }else{
    $('mFuture').textContent=fmtMoney(f.future_asset);
    $('mFutureSmall').textContent=f.calculation_basis==='selected_product_pdf'?'선택 상품 PDF 기반 계산':'추출 실패 fallback 계산';
    renderAllocation(o.recommended_allocation);
  }
  $('optimizedGoal').textContent=fmtPct(o.goal_rate_pct,1);
  // 그래프에 그릴 선은 chartSeries가 정한다. 비교 중이면 가입 상품 두 선이 함께 들어온다.
  drawChart(chartSeries(r), f.target_retirement_asset);
}
/* ---- v21: 다른 상품으로 전망만 다시 계산해 비교 ---- */
// baseline은 가입 상품 기준 원본 분석. 파선과 '가입 상품만 보기'의 기준으로 쓴다.
let compareBaseline = null;
// v33: 계산한 비교 결과를 들고 있는다. 껐다 켜는 것은 화면 전환일 뿐이라 서버를 다시 부르지 않는다.
let compareResult = null;
let compareVisible = false;
let compareBusy = false;

function setupCompare(r){
  compareBaseline = r;
  compareResult = null;
  compareVisible = false;
  const bar=$('compareBar');
  // DB형은 개인 선택 상품이 없어 비교 대상이 존재하지 않는다.
  if(r.user.operation_type === 'DB'){ bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  $('compareStatus').classList.add('hidden');
  $('compareNote').classList.add('hidden');
  $('compareToggleBtn').classList.add('hidden');

  // 사업자는 가입 상품 기준으로 고정한다. 사업자를 넘나드는 비교는 사용자가 당장 실행할 수 없는
  // 선택지라, 같은 사업자 안에서 상품만 갈아보는 쪽이 실제로 행동으로 옮길 수 있는 비교다.
  $('compareProviderLabel').textContent = r.user.provider;
  fillCompareProducts();
}

function fillCompareProducts(){
  const provider = compareBaseline.user.provider;
  // catalog.products에는 title이 겹치는 항목이 있어 그대로 채우면 같은 상품이 여러 번 뜬다.
  // 가입 상품 자체도 뺀다. 남겨두면 기본 선택이 곧 지금 화면이라 계산 버튼이 아무것도
  // 바꾸지 않는 것처럼 보이고, 그 자리는 '가입 상품으로 되돌리기'가 이미 맡고 있다.
  const seen = new Set();
  const items = (catalog.products||[]).filter(x=>{
    if(x.provider !== provider || x.title === compareBaseline.user.product_name || seen.has(x.title)) return false;
    seen.add(x.title);
    return true;
  }).sort((a,b)=>String(a.title).localeCompare(String(b.title),'ko'));
  const sel=$('compareProduct');
  sel.innerHTML = items.length
    ? items.map(x=>`<option value="${esc(x.title)}">${esc(x.title)}${x.risk_type?` (${esc(x.risk_type)})`:''}</option>`).join('')
    : '<option value="">같은 사업자에 비교할 다른 상품이 없습니다</option>';
  sel.disabled = !items.length;
  $('compareBtn').disabled = !items.length;
}

function compareStatusText(d){
  const p=d.product;
  // 아래 숫자 칸은 고른 상품 하나만 보여주지만 그래프에는 네 선이 함께 있다.
  // 그 차이를 적어두지 않으면 숫자와 그래프가 어긋난 것처럼 보인다.
  return `비교용 시뮬레이션 · ${p.product_name} (${p.investment_type}) 기준 · 그래프는 가입 상품(파선)과 고른 상품(실선)을 함께 그립니다 · 아래 숫자와 보고서 본문·챗봇은 각각 고른 상품, 가입 상품 기준입니다.`;
}

async function runCompare(){
  if(compareBusy || !compareBaseline) return;
  const title = $('compareProduct').value;
  if(!title) return;
  if(!analysisId){
    $('compareStatus').textContent='분석 결과가 만료되었습니다. 다시 분석해주세요.';
    $('compareStatus').classList.remove('hidden');
    return;
  }
  const btn=$('compareBtn'), label=btn.textContent;
  compareBusy=true; btn.disabled=true; btn.textContent='계산 중...';
  $('compareStatus').textContent='선택한 상품의 PDF를 구조화하고 다시 계산하는 중입니다. 처음 고르는 상품은 시간이 걸릴 수 있습니다.';
  $('compareStatus').classList.remove('hidden');
  try{
    const res=await postJson('/api/reproject',
      {analysis_id:analysisId, provider:compareBaseline.user.provider, product_name:title});
    if(res.status===404){ analysisId=null; throw new CompareError('분석 결과가 만료되었습니다. 다시 분석해주세요.'); }
    if(!res.ok) throw new CompareError(await httpErrorMessage(res, '선택한 상품을 다시 계산하지 못했습니다.'));
    const d=await res.json();
    if(!d.applicable){ throw new CompareError(d.note || '이 분석에는 상품 비교를 적용할 수 없습니다.'); }

    // 결과는 들고만 있고 화면 반영은 renderCompareView가 한다(v33).
    // 껐다 켜는 것은 화면 전환일 뿐이므로 이 응답을 버리지 않는다.
    compareResult = d;
    compareVisible = true;
    renderCompareView();
  }catch(err){
    console.error('상품 비교 재계산 실패:', err);
    // err.message는 위에서 모두 한국어 안내로 만들어 넣은 값이다. 그렇지 않은 예외(렌더 중
    // 발생한 JS 오류 등)까지 그대로 노출하면 다시 영문 내부 문구가 화면에 찍히므로 막는다.
    $('compareStatus').textContent = (err instanceof ServerUnreachableError || err instanceof CompareError)
      ? err.message
      : '선택한 상품을 다시 계산하지 못했습니다. 브라우저 콘솔과 서버 로그를 확인해주세요.';
    $('compareNote').classList.add('hidden');
  }finally{
    compareBusy=false; btn.disabled=false; btn.textContent=label;
  }
}

function showCompareNote(d, base){
  // Qwen 없이 도는 최소 추출은 구성비중만 읽고 자산군을 전부 원리금보장으로 분류한다.
  // 그래서 상품을 바꿔도 기대수익률이 같아 예상 은퇴자산과 목표달성률이 그대로다.
  // (최적화 자산배분은 투자유형을 따르므로 이때도 바뀐다.) 이유를 밝히지 않으면 고장으로 보인다.
  const note=$('compareNote');
  const source=(d.product_extraction||{}).source;
  const sameProjection = d.finance.future_asset === base.finance.future_asset
    && d.monte_carlo.success_probability_pct === base.monte_carlo.success_probability_pct;
  if(source !== 'qwen_pdf_extraction' && sameProjection){
    note.textContent='API 키가 없어 상품 PDF 구조화를 최소 추출로 대체했습니다. 자산군이 구분되지 않아 예상 은퇴자산과 목표달성률은 상품을 바꿔도 같게 나오고, 최적화 자산배분만 투자유형에 따라 달라집니다.';
    note.classList.remove('hidden');
  }else{
    note.classList.add('hidden');
  }
}

/* ---- v33: 비교를 껐다 켜는 토글 ---- */
// 예전에는 '가입 상품으로 되돌리기'가 비교 결과를 버렸다. 다시 보려면 같은 상품을
// 골라 계산을 또 눌러야 했고, Qwen 모드에서는 그때마다 추출 비용이 들었다.
// 이제 결과를 들고 있으면서 화면만 바꾼다. 끈 상태는 예전 '되돌리기' 직후와 같은 화면이다.
function compareHiddenText(d){
  return `비교 결과를 숨겼습니다 · ${d.product.product_name} · '비교 상품 함께 보기'를 누르면 다시 계산하지 않고 바로 보여줍니다.`;
}

function renderCompareView(){
  if(!compareBaseline) return;
  const on = compareVisible && !!compareResult;

  // 그래프와 아래 숫자 칸은 항상 같은 기준을 봐야 한다. 켜면 고른 상품, 끄면 가입 상품.
  if(on){
    const d=compareResult;
    // 원본 user는 유지한 채 상품 관련 계산 결과만 갈아끼워 같은 렌더 경로를 태운다.
    // compareBaseline을 함께 넘겨 가입 상품 두 선을 같은 그래프에 남긴다(v32).
    renderProjection({user: compareBaseline.user, finance: d.finance, monte_carlo: d.monte_carlo,
                      optimizer: d.optimizer, projectionProductName: d.product.product_name,
                      compareBaseline});
  }else{
    renderProjection(compareBaseline);
  }

  const btn=$('compareToggleBtn');
  btn.classList.toggle('hidden', !compareResult);
  if(compareResult){
    btn.textContent = on ? '가입 상품만 보기' : '비교 상품 함께 보기';
    btn.setAttribute('aria-pressed', String(on));
  }

  const status=$('compareStatus');
  if(compareResult){
    status.textContent = on ? compareStatusText(compareResult) : compareHiddenText(compareResult);
    status.classList.remove('hidden');
  }else{
    status.classList.add('hidden');
  }

  // demo 모드 안내는 비교를 보고 있을 때만 뜻이 있다. 끈 화면에는 비교할 상대가 없다.
  if(on) showCompareNote(compareResult, compareBaseline);
  else $('compareNote').classList.add('hidden');
}

function toggleCompare(){
  if(!compareResult) return;
  compareVisible = !compareVisible;
  renderCompareView();
}

$('compareBtn').addEventListener('click', runCompare);
$('compareToggleBtn').addEventListener('click', toggleCompare);

function renderAllocation(allocation){ $('allocationBars').innerHTML=Object.entries(allocation||{}).map(([k,v])=>`<div class="allocation-row"><span>${esc(k)}</span><div class="allocation-track"><div class="allocation-fill" style="width:${Math.max(0,Math.min(100,Number(v)))}%"></div></div><strong>${fmtPct(v,1)}</strong></div>`).join(''); }
/* ---- v22: 전망 그래프 범례 + 마우스 오버 툴팁 ---- */
// 선이 두 개인데 무엇을 뜻하는지 화면 어디에도 적혀 있지 않았다. 색만 다르고 설명이 없으면
// 상품을 바꿔가며 비교해도 어느 선이 어느 상품인지 읽을 수 없어서, 범례와 툴팁 양쪽에
// "무엇을 기준으로 계산한 선인지"를 상품명까지 붙여 적는다.
// v25: 라이트 테마 계열색. dataviz 검증 통과(흰 배경 기준, 인접쌍 CVD ΔE 30.2 / 일반 ΔE 39.2).
// 목표선은 계열이 아니라 기준선이라 계열색을 주지 않고 중립 회색으로 물러나게 둔다.
// styles.css의 --series-1/--series-2/--chart-target과 같은 값이다. 한쪽만 고치지 말 것.
const CHART_COLORS = {current:'#215ee9', optimized:'#eb6834', target:'#6b6b6b'};
const CHART_INK = {grid:'#e7e8e8', axis:'#8a8f98', surface:'#ffffff', tooltipBg:'#ffffff', tooltipLine:'#dadadb', tooltipInk:'#1f1f1f'};
let chartState = null;

// name은 범례용 전체 이름, short는 툴팁용 짧은 이름이다. 툴팁은 커서를 따라다니며 그래프를
// 가리므로 상품명까지 넣으면 상자가 화면 절반을 덮는다. 어느 상품인지는 범례에 이미 적혀 있다.
//
// v32: 상품을 바꿔 계산하면 네 선을 한 그래프에 함께 그린다.
//   색  = 계산 종류 (파랑 = 상품 구성비중 그대로, 주황 = 최적화 자산배분)
//   선  = 어느 상품인지 (파선 = 지금 가입 상품, 실선 = 방금 고른 상품)
// 색을 상품에 배정하지 않은 이유는, 사용자가 실제로 견주는 짝이 '같은 계산끼리'이기 때문이다.
// 같은 색 두 선 사이의 세로 간격이 곧 상품을 바꿨을 때의 차이가 되고, 파랑/주황이 원래
// 가지고 있던 뜻(구성비중 vs 최적화)도 비교 전후로 흔들리지 않는다.
function chartSeries(r){
  const f=r.finance, o=r.optimizer, base=r.compareBaseline;
  // DB형은 optimizer.series가 finance.series와 같은 배열이라 두 선이 완전히 겹친다.
  // 없는 구분을 범례에 적으면 오히려 거짓말이 되므로 한 줄로만 그린다.
  if(r.user.operation_type === 'DB'){
    return [{key:'current', color:CHART_COLORS.current, points:f.series,
             name:'예상 퇴직급여 (DB)', short:'예상 퇴직급여 (DB)',
             desc:`임금상승률 ${Number(f.wage_growth_rate_pct||0).toFixed(2)}% 가정 · 근속연수 누적`}];
  }
  if(!base){
    return [
      {key:'current', color:CHART_COLORS.current, points:f.series, short:'가입 상품 기준',
       name:`가입 상품 기준 · ${r.user.product_name||'선택 상품'}`,
       desc:'현재 가입 상품의 구성비중으로 계산한 전망'},
      {key:'optimized', color:CHART_COLORS.optimized, points:o.series,
       name:'최적화 자산배분 기준', short:'최적화 자산배분 기준',
       desc:'깨움이 추천한 자산배분을 따랐을 때의 전망'},
    ];
  }
  // 범례 순서는 (가입 → 고름) 짝을 붙여 둔다. 같은 색 두 줄이 나란히 놓여야
  // "이 상품에서 저 상품으로 바꾸면 이만큼"이 한눈에 읽힌다.
  const list=[
    {key:'base-current', color:CHART_COLORS.current, dash:true, points:(base.finance||{}).series, short:'가입 상품',
     name:`지금 가입 상품 · ${base.user.product_name||'가입 상품'}`,
     desc:'바꾸기 전 상품의 구성비중으로 계산한 전망'},
    {key:'current', color:CHART_COLORS.current, points:f.series, short:'고른 상품',
     name:`방금 고른 상품 · ${r.projectionProductName||'비교 상품'}`,
     desc:'고른 상품의 구성비중으로 계산한 전망'},
    {key:'base-optimized', color:CHART_COLORS.optimized, dash:true, points:(base.optimizer||{}).series,
     name:'가입 상품의 최적화 자산배분', short:'가입 최적화',
     desc:'바꾸기 전 상품의 투자유형으로 최적화했을 때의 전망'},
    {key:'optimized', color:CHART_COLORS.optimized, points:o.series,
     name:'고른 상품의 최적화 자산배분', short:'고른 최적화',
     desc:'고른 상품의 투자유형으로 최적화했을 때의 전망'},
  ];
  // API 키 없이 도는 최소 추출은 자산군을 구분하지 못해 상품을 바꿔도 전망이 그대로다.
  // 그러면 같은 색 두 선이 완전히 포개져 한 선만 보인다. 범례가 네 줄인데 선이 둘이면
  // 고장으로 읽히므로, 겹쳤다는 사실을 선을 그리기 전에 범례에 적어 둔다.
  [['base-current','current'],['base-optimized','optimized']].forEach(([a,b])=>{
    const x=list.find(s=>s.key===a), z=list.find(s=>s.key===b);
    if(!sameSeries(x.points, z.points)) return;
    x.desc += ' · 아래 선과 값이 같아 겹칩니다';
    z.desc += ' · 위 선과 값이 같아 겹칩니다';
  });
  return list;
}

function sameSeries(a, b){
  return Array.isArray(a) && Array.isArray(b) && a.length === b.length
    && a.every((d,i)=>Number(d.value) === Number(b[i].value));
}

// 파선 범례 표식은 CSS 클래스 대신 계열색을 그대로 쓰는 그라디언트로 만든다.
// 목표선 표식(.dash)은 색이 고정이지만 이쪽은 계열마다 색이 달라서다.
function legendSwatch(s){
  return s.dash
    ? `background:repeating-linear-gradient(90deg,${s.color} 0 6px,transparent 6px 11px)`
    : `background:${s.color}`;
}

function renderChartLegend(series, target){
  const rows = series.map(s=>
    `<div class="legend-item"><i style="${legendSwatch(s)}"></i><div><b>${esc(s.name)}</b><span>${esc(s.desc)}</span></div></div>`);
  rows.push(`<div class="legend-item"><i class="dash"></i><div><b>목표 은퇴자산</b><span>${fmtMoney(target)} · 희망 노후소득을 4% 인출률로 환산한 금액</span></div></div>`);
  $('chartLegend').innerHTML = rows.join('');
}

function drawChart(series, target){
  const svg=$('projectionChart');
  // 재계산 응답이 비어 오면 at(-1) 접근에서 터진다. 그릴 게 없으면 조용히 비운다.
  const lines=(series||[]).filter(s=>Array.isArray(s.points) && s.points.length);
  if(!lines.length){ svg.innerHTML=''; $('chartLegend').innerHTML=''; chartState=null; return; }
  const W=980,H=360,L=70,R=26,T=24,B=45,iw=W-L-R,ih=H-T-B; const all=lines.flatMap(s=>s.points.map(d=>Number(d.value))); const max=Math.max(target,...all)*1.1; const last=Math.max(...lines.map(s=>s.points.at(-1).year)); const x=y=>L+(y/last)*iw; const y=v=>T+ih-(v/max)*ih; const pts=s=>s.map(d=>`${x(d.year).toFixed(1)},${y(d.value).toFixed(1)}`).join(' '); let html='';
  for(let i=0;i<=5;i++){const val=max*i/5,yy=y(val);html+=`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="${CHART_INK.grid}"/><text x="${L-9}" y="${yy+4}" text-anchor="end" fill="${CHART_INK.axis}" font-size="10">${fmtMoney(val)}</text>`;}
  const ty=y(target); html+=`<line x1="${L}" y1="${ty}" x2="${W-R}" y2="${ty}" stroke="${CHART_COLORS.target}" stroke-width="2" stroke-dasharray="7 7"/><text x="${W-R}" y="${Math.max(12,ty-7)}" text-anchor="end" fill="${CHART_COLORS.target}" font-size="10">목표</text>`;
  // 파선(가입 상품)을 나중에 그려 실선 위에 올린다. 두 상품의 값이 가까울 때
  // 파선이 실선 밑에 깔려 사라지는 쪽보다, 실선 위에 얹혀 끊긴 자국을 남기는 쪽이 읽힌다.
  [...lines].sort((a,b)=>(a.dash?1:0)-(b.dash?1:0)).forEach(s=>{
    const dash = s.dash ? ' stroke-dasharray="5 7"' : '';
    html+=`<polyline points="${pts(s.points)}" fill="none" stroke="${s.color}" stroke-width="${s.dash?3:4}"${dash} stroke-linecap="round" stroke-linejoin="round"/>`;
  });
  [0,Math.round(last/2),last].forEach(t=>html+=`<text x="${x(t)}" y="${H-13}" text-anchor="middle" fill="${CHART_INK.axis}" font-size="10">${t}년</text>`);
  // 히트 영역이 먼저 와서 선들 뒤에 깔리고, 툴팁 레이어는 그 위에 얹되 이벤트를 가로채지 않는다.
  html+=`<rect id="chartHit" x="${L}" y="${T}" width="${iw}" height="${ih}" fill="transparent" style="cursor:crosshair"/>`;
  html+=`<g id="chartHover" style="pointer-events:none;display:none"></g>`;
  svg.innerHTML=html;

  chartState={target, L, T, iw, ih, W, last, x, y, series:lines};
  renderChartLegend(lines, target);
}

// SVG에는 텍스트 폭을 미리 재는 수단이 없어 근사한다. 한글은 글자폭이 폰트 크기와 거의 같고
// 숫자·라틴은 그 절반쯤이라, 이 정도면 배경 상자가 글자를 자르지 않는다.
function svgTextWidth(text, size){
  let w=0;
  for(const ch of text) w += /[가-힣㄰-㆏　-〿＀-￯]/.test(ch) ? size : size*0.56;
  return w;
}
function chartPointFromEvent(ev){
  const svg=$('projectionChart');
  const ctm=svg.getScreenCTM();
  if(!ctm) return null;
  const pt=svg.createSVGPoint(); pt.x=ev.clientX; pt.y=ev.clientY;
  return pt.matrixTransform(ctm.inverse());
}

function hideChartHover(){
  const g=document.getElementById('chartHover');
  if(g) g.setAttribute('style','pointer-events:none;display:none');
}

function showChartHover(year){
  const st=chartState, g=document.getElementById('chartHover');
  if(!st || !g) return;
  const rows=st.series.map(s=>{
    const d=s.points.find(v=>v.year===year);
    return d ? {...s, value:Number(d.value), age:d.age} : null;
  }).filter(Boolean);
  if(!rows.length){ hideChartHover(); return; }

  const cx=st.x(year), FS=11, LH=16, PAD=10;
  const title = rows[0].age!=null ? `${year}년 후 · 만 ${rows[0].age}세` : `${year}년 후`;
  const lines = rows.map(r=>({color:r.color, dash:r.dash, text:`${r.short}  ${fmtMoney(r.value)}`}));
  // 목표선은 연도와 무관하게 일정하지만, 전망과 목표의 거리가 이 그래프의 핵심이라
  // 매 지점에서 두 값을 나란히 읽을 수 있도록 함께 적는다.
  lines.push({color:CHART_COLORS.target, dash:true, text:`목표 은퇴자산  ${fmtMoney(st.target)}`});
  const bw = Math.max(svgTextWidth(title, FS), ...lines.map(l=>svgTextWidth(l.text, FS)+14)) + PAD*2;
  const bh = PAD*2 + FS + 4 + lines.length*LH;

  // 오른쪽에 자리가 없으면 왼쪽으로 넘긴다. 세로는 첫 계열 값 근처에 두되 그림 영역 안으로 가둔다.
  const bx = cx + 14 + bw <= st.L + st.iw ? cx + 14 : cx - 14 - bw;
  const by = Math.min(Math.max(st.y(rows[0].value) - bh/2, st.T + 2), st.T + st.ih - bh - 2);

  let h=`<line x1="${cx}" y1="${st.T}" x2="${cx}" y2="${st.T+st.ih}" stroke="${CHART_INK.axis}" stroke-width="1" stroke-dasharray="4 4"/>`;
  rows.forEach(r=>{ h+=`<circle cx="${cx}" cy="${st.y(r.value)}" r="5" fill="${CHART_INK.surface}" stroke="${r.color}" stroke-width="3"/>`; });
  h+=`<circle cx="${cx}" cy="${st.y(st.target)}" r="5" fill="${CHART_INK.surface}" stroke="${CHART_COLORS.target}" stroke-width="3"/>`;
  h+=`<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="10" fill="${CHART_INK.tooltipBg}" stroke="${CHART_INK.tooltipLine}" stroke-width="1"/>`;
  h+=`<text x="${(bx+PAD).toFixed(1)}" y="${(by+PAD+FS-1).toFixed(1)}" fill="${CHART_INK.axis}" font-size="${FS}" font-weight="700">${esc(title)}</text>`;
  lines.forEach((l,i)=>{
    const ly=by+PAD+FS+4+LH*i+FS-2;
    // 목표선은 그래프에서 파선이므로 툴팁 표식도 파선으로 맞춘다.
    h+= l.dash
      ? `<line x1="${(bx+PAD).toFixed(1)}" y1="${(ly-3).toFixed(1)}" x2="${(bx+PAD+9).toFixed(1)}" y2="${(ly-3).toFixed(1)}" stroke="${l.color}" stroke-width="2" stroke-dasharray="3 2"/>`
      : `<rect x="${(bx+PAD).toFixed(1)}" y="${(ly-FS+2).toFixed(1)}" width="8" height="8" rx="2" fill="${l.color}"/>`;
    h+=`<text x="${(bx+PAD+14).toFixed(1)}" y="${ly.toFixed(1)}" fill="${CHART_INK.tooltipInk}" font-size="${FS}">${esc(l.text)}</text>`;
  });
  g.innerHTML=h;
  g.setAttribute('style','pointer-events:none');
}

function onChartPointerMove(ev){
  if(!chartState) return;
  const p=chartPointFromEvent(ev);
  if(!p) return;
  const st=chartState;
  if(p.x<st.L-2 || p.x>st.L+st.iw+2 || p.y<st.T-2 || p.y>st.T+st.ih+2){ hideChartHover(); return; }
  showChartHover(Math.max(0, Math.min(st.last, Math.round(((p.x-st.L)/st.iw)*st.last))));
}
// 차트 내용은 다시 그릴 때마다 통째로 교체되므로, 리스너는 살아남는 svg 요소에 한 번만 건다.
$('projectionChart').addEventListener('pointermove', onChartPointerMove);
$('projectionChart').addEventListener('pointerleave', hideChartHover);
$('restartBtn').addEventListener('click',()=>{ resetChat(); $('reportView').classList.add('hidden'); $('inputView').classList.remove('hidden'); window.scrollTo({top:0,behavior:'smooth'}); });
$('printBtn').addEventListener('click',()=>window.print());
init();

/* ---- Report Q&A Agent: 보고서 화면 플로팅 챗봇 ---- */
const SUGGESTIONS = {
  DB: ['목표달성률이 왜 이 수준인가요?', '임금상승률이 1.5%면 어떻게 되나요?', '예상 퇴직급여 계산 근거는?'],
  DCIRP: ['목표달성률이 낮은 이유는?', '납입액을 1200만원으로 늘리면?', '제 상품 구성은 어떻게 되나요?'],
};

function chatEl(tag, cls, text){
  const el=document.createElement(tag);
  if(cls) el.className=cls;
  if(text!=null) el.textContent=text;
  return el;
}
function chatScroll(){ const box=$('chatMessages'); box.scrollTop=box.scrollHeight; }
function chatAppend(node){ $('chatMessages').appendChild(node); chatScroll(); return node; }

function setupChat(r){
  const isDB=r.user.operation_type==='DB';
  chatHistory=[];
  $('chatMessages').innerHTML='';
  $('chatInput').disabled=false;
  $('chatSendBtn').disabled=false;
  $('chatScopeBadge').textContent = isDB ? '보고서 계산값 + 전체 상품 DB 기준' : '내 상품 PDF + 전체 상품 DB 기준';
  chatAppend(chatEl('div','chat-msg bot',
    isDB
      ? '분석이 끝났습니다. 보고서의 계산값과 상품 DB를 근거로 답변드립니다. 임금상승률이나 은퇴 나이를 바꾼 경우도 다시 계산해 드립니다.'
      : '분석이 끝났습니다. 보고서의 계산값과 가입 상품 PDF를 근거로 답변드립니다. 납입액, 자산배분, 은퇴 나이를 바꾼 경우도 다시 계산해 드립니다.'));
  const list=$('chatSuggestions');
  list.innerHTML='';
  (isDB?SUGGESTIONS.DB:SUGGESTIONS.DCIRP).forEach(q=>{
    const b=chatEl('button','chat-suggestion',q);
    b.type='button';
    b.addEventListener('click',()=>{ $('chatInput').value=q; sendChat(); });
    list.appendChild(b);
  });
  $('chatDock').classList.remove('hidden');
}

function resetChat(){
  analysisId=null;
  chatHistory=[];
  $('chatDock').classList.add('hidden');
  $('chatPanel').classList.add('hidden');
  $('chatFab').setAttribute('aria-expanded','false');
  $('chatMessages').innerHTML='';
  $('chatSuggestions').innerHTML='';
  $('chatInput').value='';
}

function toggleChat(open){
  $('chatPanel').classList.toggle('hidden',!open);
  $('chatFab').classList.toggle('hidden',open);
  $('chatFab').setAttribute('aria-expanded',open?'true':'false');
  if(open){ $('chatInput').focus(); chatScroll(); }
}
$('chatFab').addEventListener('click',()=>toggleChat(true));
$('chatCloseBtn').addEventListener('click',()=>toggleChat(false));

function renderWhatIf(w){
  if(!w || !w.applicable) return;
  const card=chatEl('div','whatif-card');
  card.appendChild(chatEl('h5',null,'재계산 시나리오'));

  const labels={retirement_age:'은퇴 나이', annual_contribution:'연간 납입액', safe_ratio_pct:'안전자산 비중', wage_growth_rate_pct:'임금상승률'};
  const units={retirement_age:'세', safe_ratio_pct:'%', wage_growth_rate_pct:'%'};
  const changed=Object.entries(w.changes||{}).map(([k,v])=>`${labels[k]||k} ${v}${units[k]||''}`).join(' · ');
  const notes=(w.notes||[]).join(' ');
  card.appendChild(chatEl('p','whatif-changes',[changed,notes].filter(Boolean).join(' — ')));

  const head=chatEl('div','whatif-row');
  head.appendChild(chatEl('span','whatif-head','항목'));
  head.appendChild(chatEl('b','whatif-head','현재'));
  head.appendChild(chatEl('strong','whatif-head','시나리오'));
  card.appendChild(head);

  const rows=[
    ['예상 자산', w.baseline.future_asset, w.scenario.future_asset],
    ['목표달성률', `${w.baseline.goal_rate_pct}%`, `${w.scenario.goal_rate_pct}%`],
    ['목표달성확률', `${w.baseline.success_probability_pct}%`, `${w.scenario.success_probability_pct}%`],
  ];
  rows.forEach(([label,base,scen])=>{
    const row=chatEl('div','whatif-row');
    row.appendChild(chatEl('span',null,label));
    row.appendChild(chatEl('b',null,String(base)));
    row.appendChild(chatEl('strong',null,String(scen)));
    card.appendChild(row);
  });
  card.appendChild(chatEl('p','whatif-note',w.note||''));
  chatAppend(card);
}

function renderEvidence(rows){
  if(!rows || !rows.length) return;
  const wrap=chatEl('div','chat-evidence');
  rows.forEach(e=>{
    const own=e.is_selected_product;
    const chip=chatEl('button',`chat-chip${own?'':' foreign'}`,
      `${e.evidence_id} · ${own?'내 상품':'타 상품'} · ${e.title||''} p.${e.page}`);
    chip.type='button';
    let opened=null;
    chip.addEventListener('click',()=>{
      if(opened){ opened.remove(); opened=null; return; }
      opened=chatEl('div','chat-snippet',`[${e.evidence_id}] ${e.title||''} (${e.provider||''}) p.${e.page}\n\n${e.snippet||''}`);
      wrap.insertAdjacentElement('afterend',opened);
      chatScroll();
    });
    wrap.appendChild(chip);
  });
  chatAppend(wrap);
}

let chatBusy=false;
async function sendChat(){
  if(chatBusy) return;
  const input=$('chatInput');
  const text=(input.value||'').trim();
  if(!text) return;
  if(!analysisId){
    chatAppend(chatEl('div','chat-msg bot error','분석 결과를 찾을 수 없습니다. 다시 입력을 눌러 분석을 새로 실행해주세요.'));
    return;
  }
  chatBusy=true;
  input.value='';
  $('chatSendBtn').disabled=true;
  chatAppend(chatEl('div','chat-msg user',text));
  const typing=chatAppend((()=>{const t=chatEl('div','chat-typing'); t.innerHTML='<i></i><i></i><i></i>'; return t;})());

  try{
    const res=await postJson('/api/chat',
      {analysis_id:analysisId, message:text, history:chatHistory.slice(-6)});
    typing.remove();
    if(res.status===404){
      analysisId=null;
      input.disabled=true;
      chatAppend(chatEl('div','chat-msg bot error','분석 결과가 만료되었습니다. 다시 입력을 눌러 분석을 새로 실행해주세요.'));
      return;
    }
    if(!res.ok) throw new Error(await res.text());
    const data=await res.json();
    chatAppend(chatEl('div','chat-msg bot',data.answer||'답변을 생성하지 못했습니다.'));
    renderWhatIf(data.what_if);
    renderEvidence(data.evidence);
    chatHistory.push({role:'user',content:text},{role:'assistant',content:data.answer||''});
  }catch(err){
    console.error('챗봇 응답 실패:',err);
    typing.remove();
    // 서버가 꺼져 있으면 '잠시 후 다시 시도'는 영원히 틀린 안내다. 원인을 그대로 알린다.
    chatAppend(chatEl('div','chat-msg bot error', err instanceof ServerUnreachableError
      ? err.message : '답변을 가져오지 못했습니다. 잠시 후 다시 시도해주세요.'));
  }finally{
    chatBusy=false;
    $('chatSendBtn').disabled=false;
    if(!input.disabled) input.focus();
  }
}
$('chatForm').addEventListener('submit',(e)=>{ e.preventDefault(); sendChat(); });
