// 浏览器检查使用临时账号，只访问公开足球统计，不调用付费模型。
const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {browserSession}=require('./browser_session.cjs');
const assert=require('node:assert/strict');
const origin='http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 let cleanup;
 try{
  const p=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
  await p.goto(origin+'/team-profile');await p.waitForURL('**/login');
  const denied=await p.request.post(origin+'/api/team-profile',{headers:{Origin:origin},data:{team:'Atletico Madrid'}});assert.equal(denied.status(),401);
  cleanup=await browserSession(p);
  await p.goto(origin);await p.locator('.fixture-row').first().waitFor({timeout:60000});
  await p.locator('#team-profile-link').click();await p.waitForURL('**/team-profile?*');
  await p.locator('#season-players tr').first().waitFor({timeout:90000});
  assert.match(await p.locator('#profile-name').innerText(),/马德里竞技/);
  assert.equal(await p.locator('#profile-season option').count(),3);
  await p.waitForFunction(()=>[...document.querySelectorAll('#season-players img')].some(i=>i.complete&&i.naturalWidth>0),{},{timeout:30000});
  await p.screenshot({path:'E:/Agent/output/ui_preview/team-profile-desktop.png',fullPage:false});
  await p.selectOption('#profile-season','2024/2025');await p.waitForFunction(()=>document.querySelector('#season-scope').textContent.startsWith('2024/2025'),{},{timeout:90000});
  const metrics=await p.locator('#season-metrics').innerText();assert.match(metrics,/68/);assert.match(metrics,/76/);
  assert.match(await p.locator('#season-players').innerText(),/Antoine Griezmann/);
  await p.selectOption('#player-sort','goals');assert.match(await p.locator('#season-players tr').first().innerText(),/Alexander Sørloth/);
  await p.selectOption('#player-position','门将');for(const row of await p.locator('#season-players tr').all())assert.match(await row.innerText(),/门将/);
  await p.click('#tab-roster');assert.equal(await p.locator('#season-view').isVisible(),false);assert((await p.locator('.roster-card').count())>15);
  assert.match(await p.locator('#roster-note').innerText(),/当前阵容/);
  await p.screenshot({path:'E:/Agent/output/ui_preview/team-profile-roster.png',fullPage:true});
  await p.click('#tab-season');await p.selectOption('#player-position','');
  await p.screenshot({path:'E:/Agent/output/ui_preview/team-profile-history.png',fullPage:false});
  await p.locator('.player-table-wrap').evaluate(el=>el.scrollIntoView({block:'start'}));await p.screenshot({path:'E:/Agent/output/ui_preview/team-profile-players.png',fullPage:false});
  await p.fill('#profile-team','Barcelona');await p.click('#profile-confirm');await p.waitForFunction(()=>document.querySelector('#profile-name').textContent==='巴塞罗那'&&document.querySelectorAll('#season-players tr').length>0,{},{timeout:90000});
  await p.setViewportSize({width:390,height:844});await p.screenshot({path:'E:/Agent/output/ui_preview/team-profile-mobile.png',fullPage:false});
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // 网络失败后，不得把上支球队的数据留在屏幕上冒充新球队。
  await p.route('**/api/team-profile',r=>r.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'测试：资料暂不可用'})}));
  await p.fill('#profile-team','Not A Team');await p.click('#profile-confirm');await p.waitForFunction(()=>document.querySelector('#profile-status').classList.contains('error'));
  assert.equal(await p.locator('#profile-content').isVisible(),false);
  assert.deepEqual(errors,[]);console.log('Verified protected page/API, confirmed-team entry, real player photos, three seasons, historical squad/stats, sorting, filters, team switch, failure state, mobile width. No model requests.');
 }finally{if(cleanup)cleanup();await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
