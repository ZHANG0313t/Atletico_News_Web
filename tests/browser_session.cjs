// 浏览器回归使用临时账号；凭据仅在测试进程内存中，结束后删除账号与测试目录。
const {execFileSync}=require('node:child_process');
const python='E:/Anaconda3/python.exe';
async function browserSession(page){
 const setup=`import sys,json,secrets
sys.path.insert(0,'E:/Agent')
from user_accounts import AuthStore
from project_paths import PROJECT_DIR
s=AuthStore(PROJECT_DIR/'data/accounts.sqlite3')
p=secrets.token_urlsafe(22)
u=s.create('QA-browser-'+secrets.token_hex(4),p)
print(json.dumps({'user':u,'password':p}))`;
 const info=JSON.parse(execFileSync(python,['-B','-'],{input:setup,encoding:'utf8'}));
 const response=await page.request.post('http://127.0.0.1:8765/api/auth/login',{
  headers:{Origin:'http://127.0.0.1:8765'},data:{username:info.user.username,password:info.password}});
 if(response.status()!==200)throw new Error('Temporary browser account login failed');
 info.password='';
 return ()=>{
  const cleanup=`import sys,shutil
sys.path.insert(0,'E:/Agent')
from user_accounts import AuthStore
from project_paths import PROJECT_DIR,OUTPUT_DIR
s=AuthStore(PROJECT_DIR/'data/accounts.sqlite3')
uid='${info.user.id}'
with s.connect() as db:
 row=db.execute('SELECT username,is_admin FROM users WHERE id=?',(uid,)).fetchone()
 if not row or not row['username'].startswith('QA-browser-') or row['is_admin']:raise ValueError('Not a temporary browser account')
s.delete(uid)
for parent in (OUTPUT_DIR/'users',PROJECT_DIR/'data/users'):
 target=(parent/uid).resolve()
 if target.parent!=parent.resolve() or target.name!=uid:raise ValueError('Unexpected cleanup path')
 if target.exists():shutil.rmtree(target)`;
  execFileSync(python,['-B','-'],{input:cleanup,encoding:'utf8'});
 };
}
module.exports={browserSession};
