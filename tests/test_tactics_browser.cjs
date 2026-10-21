const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {browserSession}=require('./browser_session.cjs');
const assert=require('node:assert/strict'),fs=require('node:fs');
const origin='http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});let cleanup,cleanupMobile;
 try{
  const p=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
  await p.goto(origin+'/tactics');await p.waitForURL('**/login');assert.equal((await p.request.get(origin+'/api/tactics-image?kind=player&id=123')).status(),401);
  cleanup=await browserSession(p);await p.goto(origin+'/team-profile?team=Atletico%20Madrid&team_id=9906');await p.locator('#tactics-link').waitFor({timeout:90000});
  await p.screenshot({path:'E:/Agent/output/ui_preview/tactics-profile-entry.png'});
  await p.click('#tactics-link');await p.waitForURL('**/tactics?*');await p.locator('.squad-player').first().waitFor({timeout:90000});
  assert((await p.locator('.squad-player').count())>15);assert.match(await p.locator('#tactics-team-label').innerText(),/马德里竞技/);
  const session=await (await p.request.get(origin+'/api/auth/session')).json(),key='atletico-tactics-v1:'+session.user.id+':9906';
  const draft=()=>p.evaluate(k=>{const d=JSON.parse(localStorage.getItem(k)||'[]');return Array.isArray(d)?d:d.players;},key);
  // 真正用鼠标从球员单元拖入球场。
  const first=p.locator('.squad-player').first();await first.scrollIntoViewIfNeeded();const a=await first.boundingBox(),c=await p.locator('#tactics-canvas').boundingBox(),scale=c.width/1200;
  await p.mouse.move(a.x+a.width/2,a.y+a.height/2);await p.mouse.down();await p.mouse.move(c.x+500*scale,c.y+530*scale,{steps:15});await p.mouse.up();assert.equal((await draft()).length,1);
  // 点击放置第二位；键盘调整后可移除。
  await p.locator('.squad-player').nth(1).click();await p.locator('#tactics-canvas').click({position:{x:700*scale,y:450*scale}});assert.equal((await draft()).length,2);
  const before=await draft();await p.locator('#tactics-canvas').press('ArrowRight');assert((await draft())[1].x>before[1].x);
  await p.reload();await p.locator('.squad-player').first().waitFor({timeout:60000});await p.waitForFunction(()=>document.querySelector('#tactics-count').textContent.includes('2 / 11'));
  await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:'E:/Agent/output/ui_preview/tactics-desktop.png'});
  // 验证实际生成的 JPG，不只是检查按钮存在。
  const downloaded=p.waitForEvent('download',{timeout:90000});await p.click('#tactics-download');const download=await downloaded;await download.saveAs('E:/Agent/output/ui_preview/tactics-export.jpg');const bytes=fs.readFileSync('E:/Agent/output/ui_preview/tactics-export.jpg');assert.equal(bytes[0],255);assert.equal(bytes[1],216);assert(bytes.length>20000);
  // 新账号同一球队应为空；手机使用点选再点球场。
  const mobile=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,isMobile:true});cleanupMobile=await browserSession(mobile);await mobile.goto(origin+'/tactics?team=Atletico%20Madrid&team_id=9906');await mobile.locator('.squad-player').first().waitFor({timeout:60000});assert.match(await mobile.locator('#tactics-count').innerText(),/0 \/ 11/);
  await mobile.locator('.squad-player').first().tap();const mc=await mobile.locator('#tactics-canvas').boundingBox();await mobile.locator('#tactics-canvas').tap({position:{x:mc.width*.45,y:mc.height*.55}});assert.match(await mobile.locator('#tactics-count').innerText(),/1 \/ 11/);
  await mobile.evaluate(()=>scrollTo(0,0));await mobile.screenshot({path:'E:/Agent/output/ui_preview/tactics-mobile.png'});assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await p.locator('#tactics-placed button').first().click();assert.equal((await draft()).length,1);
  p.once('dialog',d=>d.accept());await p.click('#tactics-clear');assert.equal((await draft()).length,0);
  // 点击放置最多11位，不允许重复单元无限增加，也不能塞入第12人。
  await p.evaluate(()=>{const canvas=document.querySelector('#tactics-canvas'),buttons=[...document.querySelectorAll('.squad-player')];for(let i=0;i<12;i++){buttons[i].click();const box=canvas.getBoundingClientRect(),event={bubbles:true,button:0,pointerId:77,clientX:box.x+(200+i%4*220)*box.width/1200,clientY:box.y+(300+Math.floor(i/4)*180)*box.height/940};canvas.dispatchEvent(new PointerEvent('pointerdown',event));window.dispatchEvent(new PointerEvent('pointerup',event));}});
  assert.equal((await draft()).length,11);assert.match(await p.locator('#tactics-status').innerText(),/最多11人/);
  p.once('dialog',d=>d.accept());await p.click('#tactics-clear');assert.equal((await draft()).length,0);
  assert.deepEqual(errors,[]);console.log('Verified protected entry, real drag and click placement, keyboard movement, private draft restoration, mobile touch, removal/clear and actual JPG download. No model requests.');
 }finally{if(cleanupMobile)cleanupMobile();if(cleanup)cleanup();await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
