// 真实浏览器验证账号操作；测试凭据只保留在内存，不使用主账号密码。
const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {execFileSync}=require('node:child_process'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const python='E:/Anaconda3/python.exe',origin='http://127.0.0.1:8765';
(async()=>{
 const setup=`import sys,json,secrets
sys.path.insert(0,'E:/Agent')
from user_accounts import AuthStore
from project_paths import PROJECT_DIR,OUTPUT_DIR
s=AuthStore(PROJECT_DIR/'data/accounts.sqlite3');p=secrets.token_urlsafe(20)
u=s.create('QA-admin-'+secrets.token_hex(4),p)
with s.connect() as db:db.execute('UPDATE users SET is_admin=1 WHERE id=?',(u['id'],))
folder=OUTPUT_DIR/'users'/u['id']/'briefing_20990101_000000_123456';folder.mkdir(parents=True)
(folder/'trace.json').write_text(json.dumps({'type':'briefing','title':'QA private history','question':'QA fixture','status':'completed','started_at':'2099-01-01T00:00:00+08:00','articles':[],'requests':{}}),encoding='utf-8')
(folder/'report.md').write_text('Private QA report',encoding='utf-8')
print(json.dumps({'user':u,'password':p}))`;
 const info=JSON.parse(execFileSync(python,['-B','-'],{input:setup,encoding:'utf8'}));
 const addedName='QA-created-'+crypto.randomBytes(4).toString('hex'),password=crypto.randomBytes(18).toString('hex');
 const b=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
  await p.goto(origin);await p.waitForURL('**/login');assert.equal((await p.request.get(origin+'/api/reports')).status(),401);
  await p.screenshot({path:'E:/Agent/output/ui_preview/accounts-login-desktop.png'});
  await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await p.screenshot({path:'E:/Agent/output/ui_preview/accounts-login-mobile.png'});await p.setViewportSize({width:1440,height:1000});
  async function login(name,pass){await p.locator('#login-name').fill(name);await p.locator('#login-password').fill(pass);await p.locator('#login-submit').click();await p.waitForURL(origin+'/');await p.waitForFunction(()=>!!window.accountProfile);}
  async function menu(){await p.locator('#account-menu summary').click();}
  await login(info.user.username,info.password);
  assert.equal(await p.locator('#welcome-message').innerText(),info.user.username+'，欢迎来到大都会球场！');
  await p.locator('.history-item').first().waitFor();assert.match(await p.locator('#history').innerText(),/QA private history/);
  await menu();await p.locator('#manage-accounts').click();await p.locator('#new-account-name').fill(addedName);await p.locator('#new-account-password').fill(password);
  await p.locator('#create-account-form button').click();await p.waitForFunction(name=>document.getElementById('account-users').textContent.includes(name),addedName);
  await p.screenshot({path:'E:/Agent/output/ui_preview/accounts-management.png'});await p.locator('#close-account-dialog').click();
  await menu();await p.locator('#logout-account').click();await p.waitForURL('**/login');
  await login(addedName,password);await p.waitForFunction(()=>document.getElementById('history').textContent.length>0);
  assert.equal(await p.locator('.history-item').count(),0);assert(!((await p.locator('#history').innerText()).includes('QA private history')));
  assert.equal((await p.request.get(origin+'/api/reports/briefing_20990101_000000_123456')).status(),404);
  await menu();await p.locator('#manage-accounts').click();assert(await p.locator('#account-admin').isHidden());await p.locator('#close-account-dialog').click();
  await menu();await p.locator('#logout-account').click();await p.waitForURL('**/login');await login(info.user.username,info.password);
  await menu();await p.locator('#manage-accounts').click();const row=p.locator('.managed-account').filter({hasText:addedName});await row.getByRole('button',{name:'修改密码'}).click();
  const newPass=crypto.randomBytes(18).toString('hex');await p.locator('#reset-new-password').fill(newPass);await p.locator('#reset-password-form button[type=submit]').click();
  await p.waitForFunction(()=>document.getElementById('account-message').textContent.includes('密码已更新'));
  const oldLogin=await p.request.post(origin+'/api/auth/login',{headers:{Origin:origin},data:{username:addedName,password}});assert.equal(oldLogin.status(),400);
  p.once('dialog',d=>d.accept());await row.getByRole('button',{name:'注销账号'}).click();await p.waitForFunction(name=>!document.getElementById('account-users').textContent.includes(name),addedName);
  await p.locator('#close-account-dialog').click();await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);console.log('Verified login UI, dynamic greeting, create/reset/delete, logout, empty new history, private report denial, mobile layout.');
 }finally{
  await b.close();
  const cleanup=`import sys,shutil
sys.path.insert(0,'E:/Agent')
from user_accounts import AuthStore
from project_paths import PROJECT_DIR,OUTPUT_DIR
s=AuthStore(PROJECT_DIR/'data/accounts.sqlite3')
with s.connect() as db:
 rows=db.execute('SELECT id FROM users WHERE username IN (?,?)',('${info.user.username}','${addedName}')).fetchall()
 ids=[r['id'] for r in rows]
 for uid in ids:db.execute('DELETE FROM users WHERE id=?',(uid,))
for uid in ids:
 for parent in (OUTPUT_DIR/'users',PROJECT_DIR/'data/users'):
  target=(parent/uid).resolve()
  if target.parent!=parent.resolve() or target.name!=uid:raise ValueError('Unexpected QA cleanup path')
  if target.exists():shutil.rmtree(target)`;
  execFileSync(python,['-B','-'],{input:cleanup,encoding:'utf8'});info.password='';
 }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
