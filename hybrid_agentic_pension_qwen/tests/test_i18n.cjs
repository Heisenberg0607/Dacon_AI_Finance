/* Requires Playwright. Run from the app directory: node tests/test_i18n.cjs */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const series = [{year:0,age:32,value:3500},{year:28,age:60,value:18000}];
function result(operation='DC') {
 return {
  analysis_id:'analysis-123',timing:{finished_at:'2026-10-30T12:00:00',total_seconds:1},
  mode:{qwen_enabled:false,rag:'lexical',iterations:1},trace:[],
  user:{age:32,retirement_age:60,current_savings:3500,annual_contribution:360,
    provider:'KB국민은행',product_name:'공식 안정형 상품명',investment_type:'중립투자형',operation_type:operation},
  profile:{years_to_retirement:28,risk_capacity:'보통',investment_type:'중립투자형',saving_rate_pct:7.2,
    desired_income_replacement_pct:36,diagnosis_hint:'한국어 진단',current_tenure_years:3,
    expected_additional_tenure_years:28,total_expected_tenure_years:31},
  finance:{future_asset:18000,estimated_db_benefit:17000,target_retirement_asset:45000,goal_rate_pct:40,
    current_tenure_years:3,additional_tenure_years:28,first_3y_wage_growth_rate_pct:2,
    calculation_basis:'selected_product_pdf',series,
    db_insights:{benefit_today:1250,tenure_today_years:3,one_more_year_gain:450,monthly_wage_at_retirement:800,
      monthly_wage_now:416,gap:28000,months_left:336,monthly_saving_to_close_gap:83,
      wage_growth_share_pct:20,flat_wage_benefit:13000}},
  monte_carlo:{success_probability_pct:12.5},
  optimizer:{goal_rate_pct:45,recommended_allocation:{주식:40,채권:60},series},
  product_extraction:{source_document_available:true,source_file_id:'official-file',source_filename:'공식설명서.pdf'},
  report:{title:'한국어 보고서',executive_summary:'한국어 요약',product_analysis:'한국어 상품 설명',
    simulation_comment:'한국어 시뮬레이션',strategy:['한국어 전략'],risk_notes:['한국어 위험']},
  recommendation:{},
 };
}
function presentation(language, original) {
 if(language==='ko')return {language,report:original.report,diagnosis_hint:original.profile.diagnosis_hint};
 const word=language==='en'?'English':'中文';
 return {language,report:{title:word+' pension report',executive_summary:word+' summary KRW 180,000,000',
   product_analysis:word+' product analysis',simulation_comment:word+' probability 12.5%',
   strategy:[word+' strategy'],risk_notes:[word+' risk']},diagnosis_hint:word+' assessment'};
}
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try {
  const page=await browser.newPage();
  const errors=[];const requests=[];
  let fixture=result();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://kkaeum.test/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/')return route.fulfill({path:path.join(root,'frontend/index.html'),contentType:'text/html'});
   if(url.pathname.startsWith('/static/'))return route.fulfill({path:path.join(root,'frontend',path.basename(url.pathname))});
   if(url.pathname==='/api/catalog')return route.fulfill({json:{providers:['KB국민은행'],products:[
    {provider:'KB국민은행',title:'공식 안정형 상품명',risk_type:'중립투자형'},
    {provider:'KB국민은행',title:'다른 공식 상품명',risk_type:'안정형'}]}});
   if(url.pathname==='/api/eta')return route.fulfill({json:{available:false}});
   const body=route.request().postDataJSON();requests.push({path:url.pathname,body});
   if(url.pathname==='/api/analyze/stream') {
    fixture=result(body.operation_type);
    fixture.presentation=presentation(body.language,fixture);
    return route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'result',result:fixture})+'\n\n'});
   }
   if(url.pathname==='/api/report-language')return route.fulfill({json:presentation(body.language,fixture)});
   if(url.pathname==='/api/chat')return route.fulfill({json:{answer:body.language==='zh'?'中文回答 12.5%':'English answer 12.5%',evidence:[]}});
   if(url.pathname==='/api/reproject')return route.fulfill({json:{applicable:true,
     product:{...fixture.user,product_name:'다른 공식 상품명'},finance:fixture.finance,
     optimizer:fixture.optimizer,monte_carlo:fixture.monte_carlo,product_extraction:{source:'qwen_pdf_extraction'}}});
   return route.fulfill({status:404,json:{detail:'Not found'}});
  });
  await page.goto('http://kkaeum.test/');
  await page.selectOption('#languageSelect','en');
  assert.equal(await page.locator('.sub').textContent(),'Your AI retirement pension analyst');
  assert.equal(await page.locator('#age').inputValue(),'32');
  assert.equal(await page.locator('#provider').inputValue(),'KB국민은행');
  assert.equal(await page.locator('#investmentType').inputValue(),'중립투자형');
  assert.equal(await page.evaluate(()=>I18n.text('가입 상품 기준 · 공식 안정형 상품명')),'Enrolled product · 공식 안정형 상품명');
  await page.locator('[data-operation="DB"]').click();
  assert.match(await page.locator('#additionalTenureDisplay').textContent(),/28 years/);
  await page.locator('[data-operation="IRP"]').click();
  assert.equal(await page.locator('#annualContributionLabel').textContent(),'Annual personal contribution');
  await page.locator('[data-operation="DC"]').click();
  await page.locator('#submitBtn').click();
  await page.locator('#reportView').waitFor({state:'visible'});
  assert.match(await page.locator('#executiveSummary').textContent(),/English summary/);
  assert.equal(requests.find(r=>r.path==='/api/analyze/stream').body.language,'en');
  assert.equal(await page.locator('#currentProduct').textContent(),'공식 안정형 상품명');
  assert.equal(await page.locator('#sourcePdfBtn').getAttribute('href'),'/api/source-document?file_id=official-file');
  const originalValue=await page.locator('#mFuture').textContent();
  await page.locator('#compareBtn').click();
  await page.locator('#compareToggleBtn').waitFor({state:'visible'});
  await page.selectOption('#languageSelect','zh');
  await page.waitForFunction(()=>document.getElementById('executiveSummary').textContent.startsWith('中文'));
  assert.equal(await page.locator('#mFuture').textContent(),originalValue);
  assert.equal(await page.locator('#compareToggleBtn').getAttribute('aria-pressed'),'true');
  await page.locator('#chatFab').click();
  await page.locator('.chat-suggestion').first().click();
  await page.waitForFunction(()=>document.querySelector('.chat-msg.bot:last-child')?.textContent.includes('中文回答'));
  const chat=requests.find(r=>r.path==='/api/chat');
  assert.equal(chat.body.language,'zh');
  assert.match(chat.body.message,/目标/);
  await page.selectOption('#languageSelect','ko');
  await page.waitForFunction(()=>document.getElementById('reportTitle').textContent==='한국어 보고서');
  assert.equal(await page.locator('#executiveSummary').textContent(),'한국어 요약');
  await page.locator('#restartBtn').click();
  await page.selectOption('#languageSelect','en');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  await page.locator('[data-operation="DB"]').click();
  await page.locator('#submitBtn').click();
  await page.locator('#reportView').waitFor({state:'visible'});
  assert.match(await page.locator('#mFutureLabel').textContent(),/DB/);
  assert.match(await page.locator('#dbInsights').textContent(),/KRW/);
  await page.reload();
  assert.equal(await page.locator('#languageSelect').inputValue(),'en');
  assert.equal(await page.locator('.sub').textContent(),'Your AI retirement pension analyst');
  assert.deepEqual(errors,[]);
  console.log('PASS: 3 locales, persistence, DB/DC/IRP, report switching, comparison, chat, PDF link, mobile layout, unchanged input identifiers and calculated values.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
