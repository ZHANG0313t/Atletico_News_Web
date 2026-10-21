"""Local web API tests: isolated output, mocked model workflow, no paid requests."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import web_app
from user_accounts import AuthStore,password_record
from project_paths import PROJECT_DIR

RUN='workflow_20261018_101010_123456'

class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='web_test_',dir=str(PROJECT_DIR))
        self.root=Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,PROJECT_DIR.resolve())
        self.w=web_app.Workbench(self.root)
        self.auth=AuthStore(self.root/'accounts.sqlite3')
        salt,digest=password_record('FixturePassword123')
        self.auth.bootstrap_hash('FixtureOwner',salt,digest)
        self.server=web_app.make_server(0,self.w,auth_store=self.auth)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        status,body,headers=self.request('/api/auth/login','POST',{'username':'FixtureOwner','password':'FixturePassword123'})
        self.assertEqual(status,200)
        self.session_cookie=headers['Set-Cookie'].split(';')[0]
        self.session_csrf=json.loads(body)['csrf']
        self.record={'status':'completed','started_at':'2026-10-18T10:10:10+08:00',
                     'articles':[{'url':'https://example.net/story','article_id':'1','title':'Fixture',
                                  'stage':'completed','comparison_status':'no_baseline'}],
                     'requests':{'classification':0,'comparison':0},'duplicates':[]}
        folder=self.root/RUN;folder.mkdir()
        (folder/'trace.json').write_text(json.dumps(self.record),encoding='utf-8')
        (folder/'report.md').write_text('# Fixture report',encoding='utf-8')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self,path,method='GET',payload=None,headers=None):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        h={'Origin':self.origin,'Content-Type':'application/json','X-Workbench-Token':getattr(self,'session_csrf',self.w.token)}
        if getattr(self,'session_cookie',None):h['Cookie']=self.session_cookie
        if headers: h.update(headers)
        body=json.dumps(payload).encode() if payload is not None else None
        client.request(method,path,body=body,headers=h)
        response=client.getresponse();data=response.read();status=response.status
        response_headers=dict(response.getheaders());client.close()
        return status,data,response_headers

    def wait_done(self):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            state=self.w.status()
            if state and state['status']!='running':return state
            time.sleep(.01)
        self.fail('Background task did not finish')

    def fake_workflow(self,urls,key,model,output_dir,progress,on_snapshot):
        folder=Path(output_dir)/RUN
        progress('Fixture progress')
        on_snapshot(self.record,folder)
        return self.record,folder

    def test_tactics_page_and_image_channel(self):
        for path in ('/tactics','/tactics.js','/tactics.css','/tactics-entry.css','/tactics-drawing.js','/tactics-drawing.css'):
            self.assertEqual(self.request(path)[0],200)
        with patch('web_app.load_photo',return_value=(b'fixture','image/png')) as image:
            status,body,headers=self.request('/api/tactics-image?kind=player&id=123')
            self.assertEqual(status,200);self.assertEqual(body,b'fixture')
            self.assertEqual(headers['Content-Type'],'image/png')
            image.assert_called_once_with('player','123')
            self.assertEqual(self.request('/api/tactics-image?kind=player&id=123&id=456')[0],400)
            self.assertEqual(image.call_count,1)

    def test_team_profile_page_and_api(self):
        status,body,_=self.request('/team-profile')
        self.assertEqual(status,200)
        self.assertIn(b'profile-season',body)
        with patch('web_app.get_profile',return_value={'options':[]}) as profile:
            status,body,_=self.request('/api/team-profile','POST',{'team':'Atletico Madrid'})
            self.assertEqual(status,200)
            profile.assert_called_once_with({'team':'Atletico Madrid'})
            status,_,_=self.request('/api/team-profile','POST',{'team':'Atletico Madrid'},headers={'Origin':'https://other.test'})
            self.assertEqual(status,403)
            self.assertEqual(profile.call_count,1)

    def test_static_page_and_js(self):
        for route,mime in [('/','text/html'),('/app.js','text/javascript'),('/style.css','text/css')]:
            status,body,headers=self.request(route)
            self.assertEqual(status,200)
            self.assertIn(mime,headers['Content-Type'])
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertIn(b'id="urls"',self.request('/')[1])

    def test_fixtures_endpoint_and_assets(self):
        for route in ('/fixtures.js','/fixtures.css'):
            self.assertEqual(self.request(route)[0],200)
        with patch.object(web_app,'get_fixtures',return_value={'team':{'id':'9906'},'past':[],'future':[]}) as get:
            status,body,_=self.request('/api/fixtures','POST',{'team':'马竞'})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['team']['id'],'9906')
            get.assert_called_once_with('马竞',None)
        with patch.object(web_app,'get_fixtures') as get:
            self.assertEqual(self.request('/api/fixtures','POST',{'team':'马竞','unexpected':True})[0],400)
            self.assertEqual(self.request('/api/fixtures','POST',{'team':'马竞'},{'Origin':'https://evil.example'})[0],403)
            get.assert_not_called()

    def test_history_and_download(self):
        status,body,_=self.request('/api/reports')
        self.assertEqual(json.loads(body)['reports'][0]['id'],RUN)
        status,body,_=self.request('/api/reports/'+RUN)
        self.assertEqual(json.loads(body)['record']['articles'][0]['title'],'Fixture')
        status,body,headers=self.request('/api/reports/'+RUN+'/download')
        self.assertEqual(body,b'# Fixture report')
        self.assertIn('attachment',headers['Content-Disposition'])

    def test_unknown_files_and_path_traversal_blocked(self):
        self.assertEqual(self.request('/local_config.ini')[0],404)
        self.assertEqual(self.request('/api/reports/..%2Flocal_config.ini')[0],400)
        with self.assertRaises(ValueError):web_app.report_folder('../local_config.ini',self.root)

    def test_wrong_host_and_cross_origin_blocked(self):
        self.assertEqual(self.request('/api/status',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/jobs','POST',{'urls':['https://example.net/story']},{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/jobs','POST',{'urls':['https://example.net/story']},{'X-Workbench-Token':'wrong'})[0],403)

    def test_invalid_input_never_starts_work(self):
        for value in [[],['x']*6,'not a list',[None]]:
            self.assertEqual(self.request('/api/jobs','POST',{'urls':value})[0],400)
        self.assertIsNone(self.w.status())

    def test_worker_progress_and_completion_through_http(self):
        with patch.object(web_app,'load_settings',return_value=('fake-secret','fake-model')),patch.object(web_app,'run_workflow',side_effect=self.fake_workflow) as runner:
            status,body,_=self.request('/api/jobs','POST',{'urls':['https://example.net/story']})
            self.assertEqual(status,202)
            state=self.wait_done()
        self.assertEqual(state['status'],'completed')
        self.assertEqual(state['run_id'],RUN)
        self.assertIn('Fixture progress',state['messages'])
        self.assertNotIn('fake-secret',json.dumps(state))
        self.assertEqual(runner.call_count,1)
        self.assertEqual(json.loads(self.request('/api/status')[1])['job']['id'],json.loads(body)['job_id'])

    def test_busy_task_rejected(self):
        started=threading.Event();release=threading.Event()
        def blocked(*args,**kwargs):
            started.set();release.wait(3)
            return self.fake_workflow(*args,**kwargs)
        with patch.object(web_app,'load_settings',return_value=('fake','fake')),patch.object(web_app,'run_workflow',side_effect=blocked):
            self.assertEqual(self.request('/api/jobs','POST',{'urls':['https://example.net/a']})[0],202)
            self.assertTrue(started.wait(1))
            self.assertEqual(self.request('/api/jobs','POST',{'urls':['https://example.net/b']})[0],409)
            release.set();self.wait_done()

    def test_configuration_failure_is_visible(self):
        with patch.object(web_app,'load_settings',side_effect=ValueError('Missing local configuration')):
            self.w.submit(['https://example.net/story']);state=self.wait_done()
        self.assertEqual(state['status'],'failed')
        self.assertIn('Missing local configuration',state['error'])

    def test_restart_does_not_claim_old_task_is_running(self):
        record={**self.record,'status':'running'}
        (self.root/RUN/'trace.json').write_text(json.dumps(record),encoding='utf-8')
        loaded=json.loads(self.request('/api/reports/'+RUN)[1])['record']
        self.assertEqual(loaded['status'],'interrupted')
        self.assertEqual(self.w.history()[0]['status'],'interrupted')

    def test_public_record_omits_full_inputs(self):
        record={**self.record,'articles':[{'stage':'completed','classification':{'assignment':{'reason':'Fixture'}},
                 'comparison':{'comparison':{'comparable':True},'source_articles':{'body':'Large raw input'},'usage':{}}}]}
        result=web_app.public_record(record)
        self.assertNotIn('source_articles',result['articles'][0]['comparison'])
        self.assertEqual(result['articles'][0]['assignment']['reason'],'Fixture')

    def test_briefing_endpoint_runs_search_workflow(self):
        record={**self.record,'type':'briefing','team':'Fixture FC','plan':{'team':'Fixture FC'}}
        def fake(question,key,model,team,hours,output_dir,progress,on_snapshot,**settings):
            on_snapshot(record,Path(output_dir)/RUN)
            return record,Path(output_dir)/RUN
        with patch.object(web_app,'load_settings',return_value=('fake','fake')),patch.object(web_app,'run_briefing',side_effect=fake) as runner:
            status,_,_=self.request('/api/briefings','POST',{'question':'Recent team news','team':'Fixture FC','hours':72})
            self.assertEqual(status,202)
            state=self.wait_done()
        self.assertEqual(state['record']['type'],'briefing')
        self.assertEqual(runner.call_args.kwargs['hours'],72)

    def test_invalid_briefing_input_rejected(self):
        for payload in ({'question':'','team':'Fixture FC','hours':24},
                        {'question':'News','team':'Fixture FC','hours':999},
                        {'question':'News','team':'Fixture FC','hours':True}):
            self.assertEqual(self.request('/api/briefings','POST',payload)[0],400)
        self.assertIsNone(self.w.status())

    def test_briefing_history_and_download(self):
        folder=self.root/'briefing_20261018_111111_123456';folder.mkdir()
        record={**self.record,'type':'briefing','question':'Team news'}
        (folder/'trace.json').write_text(json.dumps(record),encoding='utf-8')
        (folder/'report.md').write_text('# News timeline',encoding='utf-8')
        self.assertEqual(self.w.history()[0]['id'],folder.name)
        self.assertEqual(self.w.history()[0]['type'],'briefing')
        status,body,_=self.request('/api/reports/'+folder.name)
        self.assertEqual(json.loads(body)['record']['type'],'briefing')
        self.assertEqual(self.request('/api/reports/'+folder.name+'/download')[1],b'# News timeline')

if __name__=='__main__':unittest.main()