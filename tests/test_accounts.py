"""账号、会话、后端报告隔离与账号权限的离线集成测试。"""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from user_accounts import AuthStore,password_record
from project_paths import PROJECT_DIR
from event_archive import USER_EVENT_PATH,event_path
from account_web import migrate_owner
import web_app

PASSWORD='FixturePassword123'
RUN='briefing_20261019_120000_123456'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(PROJECT_DIR));self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=AuthStore(self.root/'data/accounts.sqlite3')
        salt,digest=password_record(PASSWORD);self.owner_id=self.store.bootstrap_hash('Owner',salt,digest)
        self.owner_workbench=web_app.Workbench(self.root/'output');self.owner_workbench.output_dir.mkdir()
        folder=self.owner_workbench.output_dir/RUN;folder.mkdir()
        (folder/'trace.json').write_text(json.dumps({'type':'briefing','title':'Owner private report','question':'Private','status':'completed',
            'started_at':'2026-10-19T12:00:00+08:00','articles':[],'requests':{'planning':1,'organizing':1}}),encoding='utf-8')
        (folder/'report.md').write_text('Owner private report',encoding='utf-8')
        self.server=web_app.make_server(0,self.owner_workbench,self.store)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.shutdown)

    def shutdown(self):self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self,path,method='GET',payload=None,session=None,extra=None):
        headers={'Origin':self.origin,'Content-Type':'application/json'}
        if session:headers.update({'Cookie':session['cookie'],'X-Workbench-Token':session['csrf']})
        if extra:headers.update(extra)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        body=json.dumps(payload).encode() if payload is not None else None
        connection.request(method,path,body=body,headers=headers);response=connection.getresponse()
        result=(response.status,response.read(),dict(response.getheaders()));connection.close();return result

    def login(self,name='Owner',password=PASSWORD):
        status,body,headers=self.request('/api/auth/login','POST',{'username':name,'password':password})
        self.assertEqual(status,200)
        info=json.loads(body);return {'cookie':headers['Set-Cookie'].split(';')[0],'csrf':info['csrf'],'user':info['user'],'headers':headers}

    def test_unauthenticated_page_redirect_and_api_gates(self):
        status,_,headers=self.request('/');self.assertEqual(status,303);self.assertEqual(headers['Location'],'/login')
        for path in ('/api/status','/api/reports','/api/reports/'+RUN,'/api/reports/'+RUN+'/download','/api/accounts'):
            self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.request('/login')[0],200)
        self.assertEqual(self.request('/api/briefings','POST',{'question':'no'})[0],401)

    def test_cookie_security_password_hash_and_logout_revocation(self):
        session=self.login();cookie=session['headers']['Set-Cookie']
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        self.assertEqual(self.request('/api/status',session=session)[0],200)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM users').fetchone();self.assertNotEqual(row['digest'],PASSWORD)
            token=session['cookie'].split('=',1)[1]
            self.assertNotEqual(db.execute('SELECT token_hash FROM sessions').fetchone()[0],token)
        self.assertEqual(self.request('/api/auth/logout','POST',{},session)[0],200)
        self.assertEqual(self.request('/api/reports',session=session)[0],401)

    def test_new_user_empty_history_and_cannot_guess_owner_report(self):
        owner=self.login();status,body,_=self.request('/api/accounts','POST',{'username':'Reader','password':PASSWORD},owner)
        self.assertEqual(status,201);reader=self.login('Reader')
        self.assertEqual(json.loads(self.request('/api/reports',session=reader)[1])['reports'],[])
        for suffix in ('','/download'):self.assertEqual(self.request('/api/reports/'+RUN+suffix,session=reader)[0],404)
        self.assertEqual(json.loads(self.request('/api/status',session=reader)[1])['job'],None)
        self.assertEqual(len(json.loads(self.request('/api/reports',session=owner)[1])['reports']),1)
        self.assertEqual(self.request('/api/accounts',session=reader)[0],403)
        self.assertEqual(self.request('/api/accounts','POST',{'username':'Other','password':PASSWORD},reader)[0],403)
        self.assertEqual(self.request('/api/accounts/password','POST',{'user_id':self.owner_id,'new_password':'ChangedPassword123'},reader)[0],403)

    def test_reset_and_delete_invalidate_sessions_and_new_same_name_is_empty(self):
        user=self.store.create('Reader',PASSWORD);owner=self.login();old=self.login('Reader')
        self.assertEqual(self.request('/api/accounts/password','POST',{'user_id':user['id'],'new_password':'NewPassword123'},owner)[0],200)
        self.assertEqual(self.request('/api/status',session=old)[0],401)
        new=self.login('Reader','NewPassword123')
        self.assertEqual(self.request('/api/accounts/delete','POST',{'user_id':user['id']},owner)[0],200)
        self.assertEqual(self.request('/api/status',session=new)[0],401)
        recreated=self.store.create('Reader',PASSWORD);self.assertNotEqual(recreated['id'],user['id'])
        with self.assertRaises(ValueError):self.store.delete(self.owner_id)

    def test_self_change_requires_current_password_and_csrf(self):
        owner=self.login()
        payload={'user_id':self.owner_id,'current_password':'wrong','new_password':'NewPassword123'}
        self.assertEqual(self.request('/api/accounts/password','POST',payload,owner)[0],400)
        payload['current_password']=PASSWORD
        self.assertEqual(self.request('/api/accounts/password','POST',payload,owner,{'X-Workbench-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/accounts/password','POST',payload,owner,{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/accounts/password','POST',payload,owner)[0],200)
        self.assertEqual(self.request('/api/status',session=owner)[0],401)
        self.login(password='NewPassword123')

    def test_wrong_login_and_throttling(self):
        for _ in range(6):self.assertEqual(self.request('/api/auth/login','POST',{'username':'Owner','password':'wrong'})[0],400)
        status,body,_=self.request('/api/auth/login','POST',{'username':'Owner','password':PASSWORD})
        self.assertEqual(status,400);self.assertIn('频繁',json.loads(body)['error'])

    def test_private_event_path_is_context_local(self):
        original=event_path();private=self.root/'private/events.json';token=USER_EVENT_PATH.set(private)
        try:self.assertEqual(event_path(),private)
        finally:USER_EVENT_PATH.reset(token)
        self.assertEqual(event_path(),original)

    def test_background_job_state_is_not_shared_between_accounts(self):
        reader=self.store.create('Reader',PASSWORD);owner=self.login();other=self.login('Reader')
        self.owner_workbench.job={'id':'private-job','status':'running','messages':['private message']}
        own=json.loads(self.request('/api/status',session=owner)[1]);foreign=json.loads(self.request('/api/status',session=other)[1])
        self.assertEqual(own['job']['id'],'private-job');self.assertIsNone(foreign['job'])
        router=self.server.user_workbenches
        router.local.session={'user':reader,'csrf':'fixture'}
        workbench=router.current()
        # Worker threads set the account's event file independently of the request thread.
        called=[]
        def fake_worker(*args,**kwargs):called.append(event_path())
        job={'id':'fixture','status':'running','messages':[]}
        original=event_path()
        with patch.object(web_app.Workbench,'_run',side_effect=fake_worker):
            router.execute(reader['id'],'import',[],job,lambda state:None)
        self.assertEqual(called,[self.store.path.parent/'users'/reader['id']/'events.json'])
        self.assertEqual(event_path(),original)

    def test_five_users_run_sixth_queues_and_can_cancel_without_api_call(self):
        import time
        from test_task_queue import until
        self.store.create('Reader',PASSWORD);third=self.store.create('Waiting',PASSWORD)
        owner=self.login();reader=self.login('Reader');waiting=self.login('Waiting')
        extra_users=[self.store.create('Parallel%d'%i,PASSWORD) for i in range(3)]
        sessions=[owner,reader]+[self.login(u['username']) for u in extra_users]
        release=threading.Event();seen=[];seen_lock=threading.Lock()
        def fake(urls,key,model,output_dir,progress,on_snapshot):
            with seen_lock:seen.append((str(output_dir),event_path()))
            progress('private progress')
            release.wait(5)
            record={'status':'completed','articles':[]}
            return record,Path(output_dir)/RUN
        with patch.object(web_app,'load_settings',return_value=('fake','fake')),patch.object(web_app,'run_workflow',side_effect=fake):
            try:
                for session in sessions:
                    self.assertEqual(self.request('/api/jobs','POST',{'urls':['https://example.net/news']},session)[0],202)
                until(lambda:len(seen)==5)
                status,raw,_=self.request('/api/jobs','POST',{'urls':['https://example.net/wait']},waiting)
                self.assertEqual(status,202);job_id=json.loads(raw)['job_id']
                state=json.loads(self.request('/api/status',session=waiting)[1])['job']
                self.assertEqual(state['status'],'queued');self.assertEqual(state['ahead'],0)
                self.assertEqual(len(seen),5)
                self.assertEqual(self.request('/api/jobs','POST',{'urls':['https://example.net/dup']},reader)[0],409)
                self.assertEqual(self.request('/api/accounts/delete','POST',{'user_id':third['id']},owner)[0],409)
                self.assertEqual(self.request('/api/jobs/cancel','POST',{'job_id':job_id},reader)[0],409)
                self.assertEqual(self.request('/api/jobs/cancel','POST',{'job_id':job_id},waiting)[0],200)
                self.assertEqual(len(seen),5)
                self.assertEqual({p.parent.name for _,p in seen},{session['user']['id'] for session in sessions})
            finally:
                release.set()
                until(lambda:all(not self.server.user_workbenches.running(session['user']['id']) for session in sessions))

    def test_lan_host_and_origin_checks_still_require_login_and_csrf(self):
        # 不必依赖测试机器的WiFi地址，即可验证允许列表和跨站拦截。
        port=str(self.server.server_port);host='192.168.1.23:'+port
        self.server.allowed_hosts.add('192.168.1.23')
        owner=self.login()
        extra={'Host':host,'Origin':'http://'+host}
        self.assertEqual(self.request('/api/status',session=owner,extra=extra)[0],200)
        self.assertEqual(self.request('/api/status',extra=extra)[0],401)
        for origin in ('https://evil.test','http://192.168.1.24:'+port):
            self.assertEqual(self.request('/api/fixtures','POST',{'team':'马竞'},owner,{'Host':host,'Origin':origin})[0],403)
        self.assertEqual(self.request('/api/status',session=owner,extra={'Host':'evil.test:'+port})[0],403)
        self.assertEqual(self.request('/api/fixtures','POST',{'team':'马竞'},owner,{**extra,'X-Workbench-Token':'wrong'})[0],403)

    def test_legacy_reports_migrate_only_once_to_owner(self):
        root=self.owner_workbench.output_dir;data_root=self.store.path.parent/'users'
        migrate_owner(self.store,root,data_root)
        target=root/'users'/self.owner_id/RUN/'report.md'
        self.assertEqual(target.read_text(encoding='utf-8'),'Owner private report')
        self.assertTrue((root/RUN/'report.md').exists())
        other=self.store.create('EmptyReader',PASSWORD)
        self.assertFalse((root/'users'/other['id']/RUN).exists())
        target.write_text('User copy preserved',encoding='utf-8')
        migrate_owner(self.store,root,data_root)
        self.assertEqual(target.read_text(encoding='utf-8'),'User copy preserved')


if __name__=='__main__':unittest.main()
