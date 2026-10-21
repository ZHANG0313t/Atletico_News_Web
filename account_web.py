"""登录保护与按用户分配工作台，作为现有网页服务外层的访问控制。

后台始终按会话中的随机用户编号查报告，不相信浏览器提交的归属编号。
"""
import json
from http.cookies import SimpleCookie
from pathlib import Path
import re
import shutil
import sqlite3
import threading
from urllib.parse import urlsplit
from user_accounts import SESSION_SECONDS

COOKIE='atletico_session'


class UserWorkbenches:
    def __init__(self,store,base_workbench,output_root,data_root,provided=None):
        from task_queue import TaskQueue
        self.store=store;self.base=base_workbench;self.output_root=Path(output_root);self.data_root=Path(data_root)
        self.local=threading.local();self.lock=threading.RLock();self.workbenches={}
        self.owner=store.owner()
        if not self.owner:raise ValueError('请先初始化主账号。')
        if provided is not None:self.workbenches[self.owner['id']]=provided
        self.queue=TaskQueue(store.path.parent/'news_jobs.sqlite3',self.execute)

    def for_user(self,uid):
        with self.lock:
            if uid not in self.workbenches:
                folder=self.output_root/'users'/uid;folder.mkdir(parents=True,exist_ok=True)
                self.workbenches[uid]=self.base(folder)
            return self.workbenches[uid]

    def current(self):return self.for_user(self.local.session['user']['id'])

    def execute(self,uid,kind,payload,job,save):
        from event_archive import USER_EVENT_PATH
        with self.lock:
            with self.store.connect() as db:
                if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                    job.update(status='failed',error='任务所属账号已不存在。');save(job);return
            workbench=self.for_user(uid)
        with workbench.lock:workbench.job=job
        # 终态等上下文清理完成后再释放名额，避免下一任务抢用同一工作台。
        def save_progress(state):
            if state['status']=='running':save(state)
        workbench.on_change=save_progress
        marker=USER_EVENT_PATH.set(self.data_root/uid/'events.json')
        try:workbench._run(payload,job['id'],kind)
        finally:
            USER_EVENT_PATH.reset(marker)
            workbench.on_change=None
            save(workbench.status())

    @property
    def token(self):return self.local.session['csrf']
    @property
    def output_dir(self):return self.current().output_dir
    def status(self):
        uid=self.local.session['user']['id']
        return self.queue.status(uid) or self.current().status()
    def history(self):return self.current().history()

    def enqueue(self,kind,payload):
        uid=self.local.session['user']['id']
        # 注销账号与新提交共用这把短锁，避免检查后账号恰好被删除。
        with self.lock:
            with self.store.connect() as db:
                if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                    raise ValueError('账号已注销，请重新登录。')
            return self.queue.enqueue(uid,kind,payload)

    def submit(self,urls):return self.enqueue('import',self.base.validate_urls(urls))
    def submit_briefing(self,*args,**kwargs):return self.enqueue('briefing',self.base.validate_briefing(*args,**kwargs))
    def cancel(self,job_id):return self.queue.cancel(self.local.session['user']['id'],job_id)
    def running(self,uid):return self.queue.unfinished(uid)

    def delete(self,uid):
        with self.lock:
            if self.running(uid):raise RuntimeError('这个账号仍有排队或运行中的任务，请结束或取消后再注销。')
            self.store.delete(uid)



def migrate_owner(store,output_root,data_root):
    """原报告只复制给主账号；源文件保留，其他账号不导入。标记保证只做一次。"""
    owner=store.owner()
    if not owner:raise ValueError('缺少主账号。')
    user_data=Path(data_root)/owner['id'];marker=user_data/'legacy_imported.json'
    if marker.exists():return
    destination=Path(output_root)/'users'/owner['id'];destination.mkdir(parents=True,exist_ok=True)
    imported=0
    for folder in Path(output_root).iterdir():
        if folder.is_dir() and re.fullmatch(r'(?:workflow|briefing)_\d{8}_\d{6}_\d{6}',folder.name) and (folder/'trace.json').is_file():
            target=destination/folder.name
            if not target.exists():shutil.copytree(str(folder),str(target));imported+=1
    # 旧归档需要用到的公开新闻文本也归给主账号，普通新用户不会继承。
    for path in Path(output_root).glob('*.txt'):
        if re.fullmatch(r'(?:\d+|web_[a-f0-9]{24})',path.stem) and not (destination/path.name).exists():shutil.copy2(str(path),str(destination/path.name))
    user_data.mkdir(parents=True,exist_ok=True)
    old_registry=Path(data_root).parent/'events.json'
    if old_registry.exists() and not (user_data/'events.json').exists():shutil.copy2(str(old_registry),str(user_data/'events.json'))
    marker.write_text(json.dumps({'reports_imported':imported}),encoding='utf-8')


def authenticated_handler(core_handler,router,store,web_dir):
    class Handler(core_handler):
        def session(self):
            try:
                cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
                token=cookies[COOKIE].value if COOKIE in cookies else ''
            except Exception:token=''
            self.session_token=token
            session=store.session(token);router.local.session=session
            return session

        def cookie(self,value,expire=False):
            self.extra_headers={'Set-Cookie':COOKIE+'='+value+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+('0' if expire else str(SESSION_SECONDS))}

        def redirect(self,location):
            self.extra_headers={**getattr(self,'extra_headers',{}),'Location':location}
            return self.send(303,b'','text/plain; charset=utf-8')

        def do_GET(self):
            if not self.valid_host():return
            path=urlsplit(self.path).path;session=self.session()
            if path=='/api/auth/session':
                return self.send(200,{'authenticated':bool(session),**(session or {})})
            public={'/login':('login.html','text/html; charset=utf-8'),'/login.js':('login.js','text/javascript; charset=utf-8'),
                    '/accounts.css':('accounts.css','text/css; charset=utf-8'),'/assets/login-background.jpg':('assets/login-background.jpg','image/jpeg')}
            if path in public:
                if path=='/login' and session:return self.redirect('/')
                name,mime=public[path];return self.send(200,(Path(web_dir)/name).read_bytes(),mime)
            if not session:
                return self.send(401,{'error':'请先登录。'}) if path.startswith('/api/') else self.redirect('/login')
            if path=='/api/accounts':
                if not session['user']['is_admin']:return self.send(403,{'error':'只有主账号可以管理其他账号。'})
                return self.send(200,{'users':store.users()})
            return super().do_GET()

        def do_POST(self):
            if not self.valid_host():return
            path=urlsplit(self.path).path;session=self.session()
            auth_paths={'/api/auth/login','/api/auth/logout','/api/accounts','/api/accounts/password','/api/accounts/delete'}
            if path not in auth_paths:
                if not session:
                    try:
                        size=int(self.headers.get('Content-Length','0'))
                        if 0<size<=32768:self.rfile.read(size)
                    except (ValueError,OSError):pass
                    return self.send(401,{'error':'请先登录。'})
                return super().do_POST()
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096:return self.send(400,{'error':'请求过长或为空。'})
                raw=self.rfile.read(size)
            except (ValueError,OSError):return self.send(400,{'error':'请求格式无效。'})
            if not self.valid_origin():return self.send(403,{'error':'请求来源无效，请从本站操作。'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(400,{'error':'请求格式无效。'})
            try:
                payload=json.loads(raw.decode('utf-8'))
                if not isinstance(payload,dict):raise ValueError('请求格式无效。')
                if path=='/api/auth/login':
                    if set(payload)!={'username','password'}:raise ValueError('登录参数无效。')
                    token,info=store.login(payload['username'],payload['password'],self.client_address[0])
                    self.cookie(token);return self.send(200,info)
                if not session:return self.send(401,{'error':'请先登录。'})
                if self.headers.get('X-Workbench-Token')!=session['csrf']:return self.send(403,{'error':'会话校验失败，请刷新后再试。'})
                actor=session['user'];admin=actor['is_admin']
                if path=='/api/auth/logout':
                    store.logout(self.session_token);self.cookie('',True);return self.send(200,{'ok':True})
                if path=='/api/accounts':
                    if not admin:return self.send(403,{'error':'只有主账号可以新建账号。'})
                    if set(payload)!={'username','password'}:raise ValueError('新建账号参数无效。')
                    return self.send(201,{'user':store.create(payload['username'],payload['password'])})
                uid=payload.get('user_id');own=uid==actor['id']
                if not isinstance(uid,str) or not re.fullmatch(r'[a-f0-9]{32}',uid):raise ValueError('账号编号无效。')
                if not own and not admin:return self.send(403,{'error':'不能修改其他账号。'})
                allowed={'user_id','new_password','current_password'} if path.endswith('password') else {'user_id','current_password'}
                if not set(payload)<=allowed:raise ValueError('账号操作参数无效。')
                if own and not store.verify(uid,payload.get('current_password')):raise ValueError('当前密码不正确。')
                if path.endswith('password'):
                    store.change_password(uid,payload.get('new_password'))
                else:
                    router.delete(uid)
                if own:self.cookie('',True)
                return self.send(200,{'ok':True,'reauthenticate':own})
            except RuntimeError as error:return self.send(409,{'error':str(error)})
            except (ValueError,UnicodeError) as error:return self.send(400,{'error':str(error)})
            except (OSError,sqlite3.Error):return self.send(500,{'error':'本地账号存储暂不可用。'})
    return Handler
