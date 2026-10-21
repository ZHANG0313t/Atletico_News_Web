const {browserSession}=require('./browser_session.cjs');let cleanupSession;
// 读取真实赛程验证界面，只访问 FotMob/本机，不执行新闻搜索或模型请求。
const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[];let newsRequests=0;
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/api/briefings')&&r.method()==='POST')newsRequests++;});
  cleanupSession=await browserSession(page);await page.goto('http://127.0.0.1:8765/');
  await page.waitForFunction(()=>document.getElementById('fixtures-team').textContent.includes('马德里竞技'),{timeout:60000});
  assert.equal(await page.locator('.fixture-row').count(),10);
  assert.equal(await page.locator('#fixtures-list').evaluate(e=>getComputedStyle(e).overflowY),'auto');
  assert(await page.locator('#fixtures-list').evaluate(e=>e.scrollHeight>e.clientHeight));
  const images=page.locator('#fixtures-team img');await images.first().evaluate(e=>e.decode());
  assert(await images.first().evaluate(e=>e.naturalWidth>0));
  await page.locator('#fixtures-go-future').click();
  await page.waitForFunction(()=>document.getElementById('fixtures-list').scrollTop>100);
  await page.locator('#fixtures-go-past').click();
  await page.waitForFunction(()=>document.getElementById('fixtures-list').scrollTop<5);
  await page.locator('#team').fill('皇家马德里');assert.match(await page.locator('#fixtures-team').innerText(),/马德里竞技/);
  await page.locator('#confirm-team').click();await page.waitForFunction(()=>document.getElementById('fixtures-team').textContent.includes('皇家马德里'),{timeout:60000});
  assert.equal(await page.locator('.fixture-row').count(),10);
  await page.locator('#team').fill('Madrid');await page.locator('#confirm-team').click();await page.locator('.fixture-choice').first().waitFor();
  assert((await page.locator('.fixture-choice').count())>=2);await page.getByRole('button',{name:'马德里竞技',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('fixtures-team').textContent.includes('马德里竞技'));
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:'E:/Agent/output/ui_preview/fixtures-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert((await page.locator('#fixtures-panel').boundingBox()).height<=361);
  await page.locator('#fixtures-panel').screenshot({path:'E:/Agent/output/ui_preview/fixtures-mobile.png'});
  assert.deepEqual(errors,[]);assert.equal(newsRequests,0);
  console.log('Fixtures verified: real badges, past/future, internal vertical scroll, confirm-only switch, ambiguity choices, mobile width. No model requests.');
 }finally{if(cleanupSession)cleanupSession();await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
