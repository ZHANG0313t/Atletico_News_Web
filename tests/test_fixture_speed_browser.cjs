const {browserSession}=require('./browser_session.cjs');let cleanupSession;
// 浏览器缓存与布局回归：模拟延迟更新，确认旧赛程立即可见，不调用模型。
const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{const b=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const p=await b.newPage({viewport:{width:1440,height:1080}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
  cleanupSession=await browserSession(p);await p.goto('http://127.0.0.1:8765/');await p.locator('.fixture-row').first().waitFor({timeout:60000});
  await p.waitForFunction(()=>!!localStorage.getItem('atletico-fixtures-view-v1:'+window.accountProfile.id));
  const sizes=await p.evaluate(()=>({panel:document.getElementById('fixtures-panel').getBoundingClientRect().width,hero:document.querySelector('.intro').getBoundingClientRect().width}));
  assert(sizes.panel/sizes.hero>.59,JSON.stringify(sizes));
  let release;const held=new Promise(r=>release=r);let intercepted=false;let markRequested;const requested=new Promise(r=>markRequested=r);
  await p.route('**/api/fixtures',async route=>{intercepted=true;markRequested();await held;await route.continue();});
  await p.reload({waitUntil:'domcontentloaded'});
  await p.locator('.fixture-row').first().waitFor({timeout:1500});await Promise.race([requested,new Promise((_,reject)=>setTimeout(()=>reject(new Error("Fixture request did not start")),5000))]);assert(intercepted);
  assert.match(await p.locator('#fixtures-note').innerText(),/保存的赛程/);
  const list=p.locator('#fixtures-list');await list.evaluate(e=>e.scrollTop=180);release();
  await p.waitForFunction(()=>!document.getElementById('confirm-team').disabled);
  assert((await list.evaluate(e=>e.scrollTop))>=175);
  await p.unroute('**/api/fixtures');
  await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:'E:/Agent/output/ui_preview/fixtures-wide-fast.png'});
  await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);console.log('Verified wide panel, immediate cached rows during blocked request, retained scroll, and mobile width.');
 }finally{if(cleanupSession)cleanupSession();await b.close();}})().catch(e=>{console.error(e.message);process.exitCode=1;});
