// 实际局域网地址的浏览器回归；临时账号，模拟新闻状态，不调用付费API。
const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {execFileSync}=require('node:child_process'),assert=require('node:assert/strict');
const python='E:/Anaconda3/python.exe',origin=process.env.LAN_TEST_ORIGIN||'http://127.0.0.1:8765';
function py(code){return execFileSync(python,['-B','-'],{input:"import sys\nsys.path.insert(0,'E:/Agent')\n"+code,encoding:'utf8'});}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 let user;
 try{
  const info=JSON.parse(py("import json,secrets\nfrom user_accounts import AuthStore\nfrom project_paths import PROJECT_DIR\ns=AuthStore(PROJECT_DIR/'data/accounts.sqlite3')\np=secrets.token_urlsafe(24)\nu=s.create('QA-browser-lan-'+secrets.token_hex(4),p)\nprint(json.dumps(dict(user=u,password=p)))"));user=info.user;
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
  await p.goto(origin+'/');await p.waitForURL('**/login');
  const login=await p.request.post(origin+'/api/auth/login',{headers:{Origin:origin},data:{username:user.username,password:info.password}});info.password='';assert.equal(login.status(),200);
  await p.goto(origin+'/');await p.waitForFunction(()=>window.accountProfile?.id);
  await p.waitForFunction(()=>document.getElementById('connection').textContent==='局域网服务已连接');
  const requests=await Promise.all(Array.from({length:10},()=>p.request.get(origin+'/api/reports')));assert(requests.every(r=>r.status()===200));
  // 只模拟新闻任务状态，验证排队提示、取消按钮和刷新后恢复显示。
  let job={id:'browser-queue-fixture',status:'queued',ahead:3,messages:['前面3个等待任务'],record:null};
  await p.route('**/api/status',async route=>{const response=await route.fetch(),data=await response.json();await route.fulfill({json:{...data,job}});});
  await p.route('**/api/jobs/cancel',async route=>{job={...job,status:'cancelled',messages:['已取消排队，没有付费请求。']};await route.fulfill({json:{job}});});
  await p.reload();await p.locator('#cancel-queued').waitFor();assert.match(await p.locator('#progress-title').innerText(),/前面还有 3/);assert(await p.locator('#search-submit').isDisabled());
  await p.click('#cancel-queued');await p.waitForFunction(()=>!document.getElementById('search-submit').disabled);assert(await p.locator('#cancel-queued').isHidden());
  await p.unroute('**/api/status');await p.unroute('**/api/jobs/cancel');
  const url=origin+'/tactics?team=Atletico%20Madrid&team_id=9906';await p.goto(url);await p.locator('.squad-player').first().waitFor({timeout:90000});
  // 使用LAN_TEST_ORIGIN指定普通HTTP局域网地址，以覆盖非安全上下文下的绘图兼容性。
  const second=await context.newPage();second.on('pageerror',e=>errors.push(e.message));await second.goto(url);await second.locator('.squad-player').first().waitFor({timeout:60000});
  const key='atletico-tactics-v1:'+user.id+':9906';
  async function stroke(page,a,b){await page.click('[data-drawing-tool="curve"]');await page.locator('#tactics-canvas').scrollIntoViewIfNeeded();const r=await page.locator('#tactics-canvas').boundingBox();const at=v=>({x:r.x+(70+1060*v[0])*r.width/1200,y:r.y+(180+1060*68/105*v[1])*r.height/940});const s=at(a),e=at(b);await page.mouse.move(s.x,s.y);await page.mouse.down();await page.mouse.move(e.x,e.y,{steps:10});await page.mouse.up();}
  await stroke(p,[.2,.2],[.55,.45]);await second.locator('#draft-conflict').waitFor();
  const first=await p.evaluate(k=>localStorage.getItem(k),key);assert.equal(JSON.parse(first).drawings.length,1);
  await stroke(second,[.6,.3],[.8,.6]);assert.equal(await second.evaluate(k=>localStorage.getItem(k),key),first);
  await second.click('#draft-load');await second.locator('.squad-player').first().waitFor({timeout:60000});assert(await second.locator('#draft-conflict').isHidden());assert.match(await second.locator('#drawing-count').innerText(),/1 个标注/);
  // JPG仍能在普通HTTP局域网地址导出。
  const download=p.waitForEvent('download',{timeout:60000});await p.click('#tactics-download');const file=await download;assert.match(file.suggestedFilename(),/\.jpg$/);
  await p.setViewportSize({width:390,height:844});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  console.log('Verified LAN login, 10 concurrent report reads, queue/cancel UI, two-tab draft protection, insecure-context curved arrow, JPG download and mobile width.');
 }finally{
  await browser.close();
  if(user)py("import shutil\nfrom user_accounts import AuthStore\nfrom project_paths import PROJECT_DIR,OUTPUT_DIR\ns=AuthStore(PROJECT_DIR/'data/accounts.sqlite3')\nuid='"+user.id+"'\nwith s.connect() as db:\n row=db.execute('SELECT username,is_admin FROM users WHERE id=?',(uid,)).fetchone()\n if not row or not row['username'].startswith('QA-browser-lan-') or row['is_admin']:raise ValueError('Not a QA account')\ns.delete(uid)\nfor parent in (OUTPUT_DIR/'users',PROJECT_DIR/'data/users'):\n target=(parent/uid).resolve()\n if target.parent!=parent.resolve():raise ValueError('Unexpected cleanup path')\n if target.exists():shutil.rmtree(target)\n");
 }
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
