const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {browserSession}=require('./browser_session.cjs');const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});let cleanup,cleanupMobile;
 try{
  const p=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];p.on('pageerror',e=>errors.push(e.message));cleanup=await browserSession(p);
  const url='http://127.0.0.1:8765/tactics?team=Atletico%20Madrid&team_id=9906';await p.goto(url);await p.locator('.squad-player').first().waitFor({timeout:90000});
  const session=await(await p.request.get('http://127.0.0.1:8765/api/auth/session')).json(),key='atletico-tactics-v1:'+session.user.id+':9906';
  const read=()=>p.evaluate(k=>JSON.parse(localStorage.getItem(k)),key);
  // 真实旧版数组草稿迁移，阵容不丢失。
  const firstId=await p.locator('.squad-player').first().getAttribute('data-id');await p.evaluate(({key,id})=>localStorage.setItem(key,JSON.stringify([{id,x:.48,y:.52}])) ,{key,id:firstId});await p.reload();await p.locator('.squad-player').first().waitFor({timeout:60000});assert.equal((await read()).players.length,1);assert.equal((await read()).version,2);
  async function pixel(x,y){const r=await p.locator('#tactics-canvas').boundingBox();return{x:r.x+(70+x*1060)*r.width/1200,y:r.y+(180+y*(1060*68/105))*r.height/940};}
  async function viewField(){await p.locator('#tactics-canvas').evaluate(el=>scrollTo(0,el.getBoundingClientRect().top+scrollY+100));}
  async function stroke(tool,a,b){await p.click('[data-drawing-tool="'+tool+'"]');await viewField();const pa=await pixel(...a),pb=await pixel(...b);await p.mouse.move(pa.x,pa.y);await p.mouse.down();await p.mouse.move(pb.x,pb.y,{steps:12});await p.mouse.up();}
  async function fieldClick(x,y){await viewField();const pp=await pixel(x,y);await p.mouse.click(pp.x,pp.y);}
  await stroke('arrow',[.18,.17],[.58,.28]);assert.equal((await read()).drawings.length,1);
  await stroke('curve',[.15,.48],[.62,.72]);let curve=(await read()).drawings.find(s=>s.type==='curve');const beforeC=curve.c.y;
  const handle=await pixel(curve.c.x,curve.c.y),target=await pixel(.4,.2);await p.mouse.move(handle.x,handle.y);await p.mouse.down();await p.mouse.move(target.x,target.y,{steps:12});await p.mouse.up();curve=(await read()).drawings.find(s=>s.type==='curve');assert.notEqual(curve.c.y,beforeC);assert.equal((await read()).drawings.length,2);
  await stroke('line',[.05,.08],[.3,.08]);await stroke('dash',[.68,.65],[.92,.8]);await stroke('rect',[.65,.15],[.88,.45]);await stroke('ellipse',[.08,.69],[.34,.89]);
  await p.click('[data-drawing-tool="text"]');await p.fill('#drawing-text','边路前插');await p.selectOption('#drawing-font','32');await fieldClick(.43,.77);assert.equal((await read()).drawings.length,7);
  // 选择、移动、改色、粗细、透明度；撤销与重做只影响标注。
  await p.click('[data-drawing-tool="select"]');await fieldClick(.76,.3);await p.click('[data-drawing-color="#6dd9ed"]');await p.selectOption('#drawing-fill','0.4');await p.selectOption('#drawing-width','7');let rect=(await read()).drawings.find(s=>s.type==='rect');assert.equal(rect.color,'#6dd9ed');assert.equal(rect.fill,.4);assert.equal(rect.width,7);
  await p.click('#drawing-undo');assert.equal((await read()).drawings.find(s=>s.type==='rect').width,4);await p.click('#drawing-redo');assert.equal((await read()).drawings.find(s=>s.type==='rect').width,7);
  await viewField();const moveStart=await pixel(.76,.3),moveEnd=await pixel(.7,.38);await p.mouse.move(moveStart.x,moveStart.y);await p.mouse.down();await p.mouse.move(moveEnd.x,moveEnd.y,{steps:12});await p.mouse.up();assert((await read()).drawings.find(s=>s.type==='rect').a.x<rect.a.x);
  await fieldClick(.44,.79);await p.locator('#tactics-canvas').press('Delete');assert.equal((await read()).drawings.length,6);await p.click('#drawing-undo');assert.equal((await read()).drawings.length,7);
  p.once('dialog',d=>d.accept());await p.click('#drawing-clear');assert.equal((await read()).drawings.length,0);assert.equal((await read()).players.length,1);await p.click('#drawing-undo');assert.equal((await read()).drawings.length,7);
  await p.reload();await p.locator('.squad-player').first().waitFor({timeout:60000});assert.equal((await read()).drawings.length,7);assert.match(await p.locator('#drawing-count').innerText(),/7 个标注/);
  await p.locator('#drawing-tools').scrollIntoViewIfNeeded();await p.screenshot({path:'E:/Agent/output/ui_preview/tactics-drawing-tools.png'});
  await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:'E:/Agent/output/ui_preview/tactics-drawing-desktop.png'});
  const downloadEvent=p.waitForEvent('download',{timeout:60000});await p.click('#tactics-download');const download=await downloadEvent;await download.saveAs('E:/Agent/output/ui_preview/tactics-annotated-export.jpg');assert(fs.readFileSync('E:/Agent/output/ui_preview/tactics-annotated-export.jpg').length>20000);
  // 手机的真实触摸绘制；新账号独立草稿。
  const mobile=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,isMobile:true});cleanupMobile=await browserSession(mobile);await mobile.goto(url);await mobile.locator('.squad-player').first().waitFor({timeout:60000});assert.match(await mobile.locator('#drawing-count').innerText(),/0 个标注/);await mobile.locator('[data-drawing-tool="rect"]').tap();await mobile.locator('#tactics-canvas').scrollIntoViewIfNeeded();
  const r=await mobile.locator('#tactics-canvas').boundingBox(),sx=r.x+r.width*.2,sy=r.y+r.height*.4,ex=r.x+r.width*.7,ey=r.y+r.height*.7,cdp=await mobile.context().newCDPSession(mobile);await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:sx,y:sy}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:ex,y:ey}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});assert.match(await mobile.locator('#drawing-count').innerText(),/1 个标注/);
  await mobile.locator('#drawing-tools').scrollIntoViewIfNeeded();await mobile.screenshot({path:'E:/Agent/output/ui_preview/tactics-drawing-mobile.png'});assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  console.log('Verified seven drawing tools, adjustable curve, selection/movement, styling, text, undo/redo/delete, clear preserves lineup, legacy migration, private draft reload, touch drawing and annotated JPG export.');
 }finally{if(cleanupMobile)cleanupMobile();if(cleanup)cleanup();await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
