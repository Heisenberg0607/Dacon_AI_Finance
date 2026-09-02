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
function selectedByKo(value){
  if(!value) return '';
  const s=String(value);
  if(s.includes('Qwen function calling')) return 'Qwen 도구 선택';
  if(s==='Qwen') return 'Qwen';
  if(s.includes('fallback-orchestrator')) return '안전 실행 로직';
  if(s==='fallback') return '안전 실행 로직';
  if(s==='template') return '보고서 템플릿';
  return s;
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
  const basis=`${elapsedText} · ${waitBasisText(waitEstimate)}`;
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
function startPendingAnimation(operationType){
  startWaitMeter(operationType);
  setStageScope(operationType);
  resetNodes(); progressIndex=0;
  const stages=stageScope.slice();
  const tick=()=>{
    if(progressIndex>0) setNode(stages[progressIndex-1],'done');
    if(progressIndex<stages.length){
      const s=stages[progressIndex]; setNode(s,'running');
      $('agentState').textContent=PENDING_MESSAGE[s]||'';
      $('agentDetail').textContent='깨움 AI 에이전트의 분석 결과를 기다리는 중입니다.';
      progressIndex++;
    }
  };
  tick(); progressTimer=setInterval(tick,900);
}
function stopPendingAnimation(result){ if(progressTimer) clearInterval(progressTimer); progressTimer=null; finishWaitMeter(result); }

$('pensionForm').addEventListener('submit', async(e)=>{
  e.preventDefault();
  const data=payload();
  if(data.retirement_age<=data.age){ alert('은퇴 나이는 현재 나이보다 커야 합니다.'); return; }
  const btn=$('submitBtn'); btn.disabled=true; btn.querySelector('span').textContent='AI 분석 중...';
  $('inputView').classList.add('hidden'); $('workflowView').classList.remove('hidden'); $('reportView').classList.add('hidden');
  $('modeBadge').textContent='AI 분석 진행 중';
  startPendingAnimation(data.operation_type);
  try{
    const res=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(!res.ok){ throw new Error(await res.text()); }
    const result=await res.json(); lastResult=result; analysisId=result.analysis_id||null; stopPendingAnimation(result); await replayTrace(result); renderReport(result); await delay(350); $('workflowView').classList.add('hidden'); $('reportView').classList.remove('hidden'); window.scrollTo({top:0,behavior:'smooth'});
  }catch(err){ stopPendingAnimation(); console.error('분석 실패:', err); alert('분석 중 오류가 발생했습니다. .env의 Qwen 설정 또는 서버 로그를 확인해주세요.'); $('inputView').classList.remove('hidden'); $('workflowView').classList.add('hidden'); }
  finally{ btn.disabled=false; btn.querySelector('span').textContent='깨움 분석 시작'; }
});

async function replayTrace(result){
  setStageScope(result.user.operation_type);
  resetNodes();
  $('modeBadge').textContent=result.mode.qwen_enabled?'Qwen 에이전트':'안전 실행 모드';
  $('workflowSubtitle').textContent=`${result.mode.qwen_enabled?'Qwen 에이전트가 도구를 선택':'안전 실행 로직 적용'} · ${ragModeKo(result.mode.rag)} · 분석 반복 ${result.mode.iterations}회`;
  for(const t of result.trace){
    const stage=t.stage;
    if(node(stage)) setNode(stage, t.status==='retry'?'retry':'running');
    $('agentState').textContent=toolKo(t.tool || stage);
    $('agentDetail').textContent=t.selected_by ? `${selectedByKo(t.selected_by)} 방식으로 이 단계를 실행했습니다.` : '실행 중입니다.';
    await delay(180);
    if(node(stage)) setNode(stage, t.status==='retry'?'retry':'done');
  }
  const totalTook = result.timing && result.timing.total_seconds != null ? ` 총 소요시간 ${fmtDuration(result.timing.total_seconds)}.` : '';
  $('agentState').textContent='분석 완료'; $('agentDetail').textContent=`전략 검증을 거친 최종 보고서가 준비되었습니다.${totalTook}`;
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
    $('mFuture').textContent=fmtMoney(f.future_asset);
    $('mFutureSmall').textContent=f.calculation_basis==='selected_product_pdf'?'선택 상품 PDF 기반 계산':'추출 실패 fallback 계산';
    $('mTargetLabel').textContent='목표 은퇴자산';
    $('mTarget').textContent=fmtMoney(f.target_retirement_asset);
    $('mTargetSmall').textContent='4% 인출률 계산값 · 실제 원화 숫자';
    $('mProbabilityLabel').textContent='목표달성 확률';
    $('mProbability').textContent=fmtPct(mc.success_probability_pct,1);
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
  }else{
    $('currentProduct').textContent=u.product_name || '-';
    const ext=r.product_extraction||{};
    const alloc=(ext.asset_allocation||[]).map(x=>`${x.component_name} ${Number(x.weight_pct||0).toFixed(1).replace('.0','')}%`).join(' · ');
    const extractionLine=ext.source_filename ? `\n\nPDF 구조화: ${ext.source_filename}${alloc?` / ${alloc}`:''}` : '';
    $('productAnalysis').textContent=(rep.product_analysis || rec.product_analysis || '') + extractionLine;
  }

  $('simulationComment').textContent=rep.simulation_comment || '';
  $('strategyList').innerHTML=(rep.strategy||rec.actions||[]).map(x=>`<div>${esc(x)}</div>`).join('');
  if(isDB){
    $('allocationBars').innerHTML='<div class="db-allocation-note">DB형은 개인 자산배분 최적화 대신 예상 DB 급여와 희망 노후소득의 Gap을 분석합니다.</div>';
    $('optimizedGoal').textContent=fmtPct(o.goal_rate_pct,1);
  }else{
    renderAllocation(o.recommended_allocation);
    $('optimizedGoal').textContent=fmtPct(o.goal_rate_pct,1);
  }

  $('ragModeLabel').textContent=ragModeKo(r.rag.mode);
  const ragResults=r.rag.results||[];
  $('evidenceGrid').innerHTML=ragResults.length
    ? ragResults.map(e=>`<article class="evidence-card"><div class="e-head"><b>${esc(e.evidence_id)} · ${esc(e.provider)}</b><em>p.${esc(e.page)}</em></div><h4>${esc(e.title)}</h4><p>${esc(e.snippet)}</p></article>`).join('')
    : '<div class="db-allocation-note">DB형 개인 분석에는 현재 디폴트옵션 상품 PDF RAG를 적용하지 않습니다.</div>';

  const critic=r.critic||{};
  $('criticStatus').textContent=critic.passed?'검증 완료':'수정 반영 / 주의';
  $('criticStatus').className=`critic-pass ${critic.passed?'':'warn'}`;
  const checks=[...(critic.checks||[]),...(critic.issues||[]).map(x=>`확인 필요: ${x}`)];
  $('criticChecks').innerHTML=checks.map(x=>`<div>${esc(x)}</div>`).join('');
  $('riskNotes').innerHTML=(rep.risk_notes||[]).filter(Boolean).map(x=>`<div>${esc(x)}</div>`).join('');
  drawChart(f.series,o.series,f.target_retirement_asset);
  setupChat(r);
}
function renderAllocation(allocation){ $('allocationBars').innerHTML=Object.entries(allocation||{}).map(([k,v])=>`<div class="allocation-row"><span>${esc(k)}</span><div class="allocation-track"><div class="allocation-fill" style="width:${Math.max(0,Math.min(100,Number(v)))}%"></div></div><strong>${fmtPct(v,1)}</strong></div>`).join(''); }
function drawChart(current, optimized, target){
  const svg=$('projectionChart'); const W=980,H=360,L=70,R=26,T=24,B=45,iw=W-L-R,ih=H-T-B; const all=[...current,...optimized].map(x=>Number(x.value)); const max=Math.max(target,...all)*1.1; const last=Math.max(current.at(-1).year,optimized.at(-1).year); const x=y=>L+(y/last)*iw; const y=v=>T+ih-(v/max)*ih; const pts=s=>s.map(d=>`${x(d.year).toFixed(1)},${y(d.value).toFixed(1)}`).join(' '); let html='';
  for(let i=0;i<=5;i++){const val=max*i/5,yy=y(val);html+=`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#21372e"/><text x="${L-9}" y="${yy+4}" text-anchor="end" fill="#8ea49a" font-size="10">${fmtMoney(val)}</text>`;}
  const ty=y(target); html+=`<line x1="${L}" y1="${ty}" x2="${W-R}" y2="${ty}" stroke="#efc76d" stroke-width="2" stroke-dasharray="7 7"/><text x="${W-R}" y="${Math.max(12,ty-7)}" text-anchor="end" fill="#efc76d" font-size="10">목표</text>`;
  html+=`<polyline points="${pts(current)}" fill="none" stroke="#55d7e7" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><polyline points="${pts(optimized)}" fill="none" stroke="#55efaa" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`;
  [0,Math.round(last/2),last].forEach(t=>html+=`<text x="${x(t)}" y="${H-13}" text-anchor="middle" fill="#8ea49a" font-size="10">${t}년</text>`); svg.innerHTML=html;
}
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
    const res=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({analysis_id:analysisId, message:text, history:chatHistory.slice(-6)}),
    });
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
    chatAppend(chatEl('div','chat-msg bot error','답변을 가져오지 못했습니다. 잠시 후 다시 시도해주세요.'));
  }finally{
    chatBusy=false;
    $('chatSendBtn').disabled=false;
    if(!input.disabled) input.focus();
  }
}
$('chatForm').addEventListener('submit',(e)=>{ e.preventDefault(); sendChat(); });
