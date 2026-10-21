"""第十一课：只在本机运行的新闻工作台，无需安装新的Python包。

python web_app.py → 浏览器访问 http://127.0.0.1:8765
HTTP服务接收操作，后台执行新闻简报或手动链接导入；密钥只在后端读取。
"""
import argparse
import configparser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import socket
import ipaddress
from urllib.parse import urlsplit, parse_qs
from app_support import safe_error
from llm_client import load_settings
from news_workflow import run_workflow
from app_support import REPORT_LOCK
from news_briefing import run_briefing
from report_titles import report_title
from mcp_search import public_search_status
from team_fixtures import get_fixtures
from team_profile import get_profile
from tactics_board import load_photo
from user_accounts import AuthStore
from account_web import UserWorkbenches,authenticated_handler,migrate_owner
from project_paths import OUTPUT_DIR, PROJECT_DIR

WEB_DIR=PROJECT_DIR/'web'
RUN_PATTERN=r'(?:workflow|briefing)_[0-9]{8}_[0-9]{6}_[0-9]{6}'


def public_record(record):
    """网页只需要展示结果，没必要把用于复核的所有输入全文再次传给浏览器。"""
    if not isinstance(record,dict) or not isinstance(record.get('articles',[]),list):
        raise ValueError('Invalid report structure.')
    result={key:record.get(key) for key in ('type','overview','search_scope','question','team','plan','window','warnings','unprocessed_candidates','searches','excluded','status','started_at','finished_at','requests','duplicates')}
    result['title']=report_title(record)
    result['settings']=record.get('settings',{})
    result['articles']=[]
    for row in record.get('articles',[]):
        clean={key:value for key,value in row.items() if key not in {'comparison','classification','_material','source_article'}}
        classified=row.get('classification',{})
        clean['assignment']=classified.get('assignment',{})
        clean['proposal']=classified.get('proposal',{})
        if row.get('comparison'):
            clean['comparison']={key:value for key,value in row['comparison'].items() if key not in {'source_articles','usage'}}
        result['articles'].append(clean)
    return result


def report_folder(run_id,output_dir):
    if not isinstance(run_id,str) or not re.fullmatch(RUN_PATTERN,run_id):
        raise ValueError('报告编号格式不正确。')
    folder=(Path(output_dir)/run_id).resolve()
    if folder.parent!=Path(output_dir).resolve(): raise ValueError('报告路径无效。')
    if not (folder/'trace.json').is_file(): raise FileNotFoundError('未找到报告。')
    return folder


def read_saved_record(folder):
    with REPORT_LOCK:
        return json.loads((folder/'trace.json').read_text(encoding='utf-8'))


class Workbench:
    """单个账号的执行环境；全站排队交给持久任务队列。"""
    def __init__(self,output_dir=OUTPUT_DIR):
        self.output_dir=Path(output_dir)
        self.lock=threading.Lock()
        self.job=None
        self.on_change=None
        self.token=secrets.token_urlsafe(24)

    def status(self):
        with self.lock:
            # 经JSON复制，避免后台写进度时HTTP线程引用同一个可变字典。
            return json.loads(json.dumps(self.job)) if self.job else None

    @staticmethod
    def validate_urls(urls):
        if (not isinstance(urls,list) or not 1<=len(urls)<=5
                or any(not isinstance(url,str) or not url.strip() or len(url)>6000 for url in urls)):
            raise ValueError("请输入1到5条新闻链接，每行一条。")
        return [url.strip() for url in urls]

    @staticmethod
    def validate_briefing(question,team,hours,max_articles=16,search_depth="expanded"):
        if (not isinstance(question,str) or not 1<=len(question.strip())<=800
                or not isinstance(team,str) or not 1<=len(team.strip())<=60
                or type(hours) is not int or hours not in (24,72,168)
                or type(max_articles) is not int or max_articles not in (8,16,24)
                or search_depth not in ("standard","expanded")):
            raise ValueError("球队、问题或时间范围无效。")
        return dict(question=question.strip(),team=team.strip(),hours=hours,max_articles=max_articles,search_depth=search_depth)

    def changed(self):
        if self.on_change:
            self.on_change(self.status())

    def submit(self,urls):
        if (not isinstance(urls,list) or not 1<=len(urls)<=5
                or any(not isinstance(url,str) or not url.strip() or len(url)>6000 for url in urls)):
            raise ValueError('请输入1到5条新闻链接，每行一条。')
        with self.lock:
            if self.job and self.job['status']=='running': raise RuntimeError('已有任务在处理，请等待完成。')
            self.job={'id':secrets.token_hex(8),'status':'running','run_id':None,'record':None,
                      'messages':['准备读取本地配置并处理新闻……'],'error':None}
            job_id=self.job['id']
        threading.Thread(target=self._run,args=([url.strip() for url in urls],job_id),daemon=True).start()
        return job_id

    def submit_briefing(self,question,team,hours,max_articles=16,search_depth='expanded'):
        if (not isinstance(question,str) or not 1<=len(question.strip())<=800
                or not isinstance(team,str) or not 1<=len(team.strip())<=60
                or type(hours) is not int or hours not in (24,72,168)
                or type(max_articles) is not int or max_articles not in (8,16,24)
                or search_depth not in ('standard','expanded')):
            raise ValueError('Invalid team, question or time window.')
        with self.lock:
            if self.job and self.job['status']=='running': raise RuntimeError('A task is already running.')
            self.job={'id':secrets.token_hex(8),'status':'running','run_id':None,'record':None,
                      'messages':['Preparing the news search...'],'error':None}
            job_id=self.job['id']
        threading.Thread(target=self._run,args=({'question':question.strip(),'team':team.strip(),'hours':hours,'max_articles':max_articles,'search_depth':search_depth},job_id,'briefing'),daemon=True).start()
        return job_id

    def _run(self,urls,job_id,kind='import'):
        def progress(message):
            with self.lock:
                self.job['messages']=(self.job['messages']+[str(message)])[-20:]
            self.changed()
        def snapshot(record,folder):
            with self.lock:
                self.job['run_id']=folder.name
                self.job['record']=public_record(record)
            self.changed()
        try:
            api_key,model=load_settings()
            if kind=='briefing':
                record,folder=run_briefing(urls['question'],api_key,model,team=urls['team'],hours=urls['hours'],max_articles=urls['max_articles'],search_depth=urls['search_depth'],
                                           output_dir=self.output_dir,progress=progress,on_snapshot=snapshot)
            else:
                record,folder=run_workflow(urls,api_key,model,output_dir=self.output_dir,
                                           progress=progress,on_snapshot=snapshot)
            with self.lock:
                self.job.update(status=record['status'],run_id=folder.name,record=public_record(record))
        except (ValueError,OSError,configparser.Error) as error:
            print('Task error:',type(error).__name__,getattr(error,'errno',None),getattr(error,'winerror',None),flush=True)
            with self.lock: self.job.update(status='failed',error=safe_error(error))
        except Exception:
            # 意外程序错误不把完整调用栈或本地配置发到页面。
            with self.lock: self.job.update(status='failed',error='程序出现异常，已保存的结果仍保留；请检查本地运行记录。')

        finally:
            self.changed()

    def history(self):
        reports=[]
        job=self.status()
        for folder in sorted(list(self.output_dir.glob('workflow_*'))+list(self.output_dir.glob('briefing_*')),key=lambda path:path.name.split('_',1)[-1],reverse=True):
            if len(reports)>=30: break
            try:
                folder=report_folder(folder.name,self.output_dir)
                record=read_saved_record(folder)
                if not isinstance(record,dict): continue
                if record.get('status')=='running' and (not job or job.get('run_id')!=folder.name):
                    record['status']='interrupted'
                reports.append({'id':folder.name,'title':report_title(record),'type':record.get('type','import'),'question':record.get('question',''),'started_at':record.get('started_at',''),
                                'status':record.get('status','unknown'),'count':len(record.get('articles',[]))})
            except (ValueError,OSError): continue
        return reports


def handler_for(workbench):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass  # 不在终端重复打印轮询请求。

        def send(self,status,data,content_type='application/json; charset=utf-8',download=None):
            if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            for key,value in getattr(self,'extra_headers',{}).items(): self.send_header(key,value)
            self.extra_headers={}
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://images.fotmob.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download: self.send_header('Content-Disposition','attachment; filename="'+download+'"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                pass  # The browser may close or navigate away during a response.

        def valid_host(self):
            allowed=getattr(self.server,'allowed_hosts',{'127.0.0.1'})
            authorities={host+':'+str(self.server.server_port) for host in allowed}
            if self.headers.get('Host') not in authorities:
                self.send(403,{'error':'请使用启动时显示的本站地址访问。'})
                return False
            return True

        def valid_origin(self):
            return self.headers.get('Origin')=='http://'+self.headers.get('Host','')

        def do_GET(self):
            if not self.valid_host(): return
            path=urlsplit(self.path).path
            static={'/':'index.html','/app.js':'app.js','/fixtures.js':'fixtures.js','/accounts.js':'accounts.js','/fixtures.css':'fixtures.css','/style.css':'style.css','/assets/atletico-background.jpg':'assets/atletico-background.jpg'}
            static.update({'/team-profile':'team-profile.html','/team-profile.js':'team-profile.js',
                           '/team-profile.css':'team-profile.css','/profile-link.js':'profile-link.js'})
            static.update({'/tactics':'tactics.html','/tactics.js':'tactics.js','/tactics.css':'tactics.css','/tactics-entry.css':'tactics-entry.css'})
            if path in {'/tactics-drawing.js','/tactics-drawing.css'}:
                mime=('text/css' if path.endswith('.css') else 'text/javascript')+'; charset=utf-8'
                return self.send(200,(WEB_DIR/path.lstrip('/')).read_bytes(),mime)
            if path in {'/team-profile','/team-profile.js','/team-profile.css','/profile-link.js','/tactics','/tactics.js','/tactics.css','/tactics-entry.css'}:
                mime=('text/html' if path in {'/team-profile','/tactics'} else 'text/css' if path.endswith('.css') else 'text/javascript')+'; charset=utf-8'
                return self.send(200,(WEB_DIR/static[path]).read_bytes(),mime)
            if path in static:
                file=WEB_DIR/static[path]
                mime={'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/fixtures.js':'text/javascript; charset=utf-8','/accounts.js':'text/javascript; charset=utf-8','/fixtures.css':'text/css; charset=utf-8','/style.css':'text/css; charset=utf-8','/assets/atletico-background.jpg':'image/jpeg'}[path]
                return self.send(200,file.read_bytes(),mime)
            if path=='/api/tactics-image':
                try:
                    params=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                    if set(params)!={'kind','id'} or any(len(v)!=1 for v in params.values()):raise ValueError('图片参数无效。')
                    raw,mime=load_photo(params['kind'][0],params['id'][0])
                    return self.send(200,raw,mime)
                except ValueError as error:return self.send(400,{'error':str(error)})
            if path=='/api/status': return self.send(200,{'job':workbench.status(),'token':workbench.token,'search':public_search_status()})
            if path=='/api/reports': return self.send(200,{'reports':workbench.history()})
            match=re.fullmatch(r'/api/reports/([^/]+)(/download)?',path)
            if match:
                try:
                    folder=report_folder(match[1],workbench.output_dir)
                    if match[2]:
                        with REPORT_LOCK:
                            report_bytes=(folder/'report.md').read_bytes()
                        return self.send(200,report_bytes,'text/markdown; charset=utf-8',folder.name+'.md')
                    record=read_saved_record(folder)
                    # 服务重启后，磁盘中残留的running任务并没有继续执行。
                    job=workbench.status()
                    if record.get('status')=='running' and (not job or job.get('run_id')!=folder.name):
                        record['status']='interrupted'
                    return self.send(200,{'record':public_record(record),'run_id':folder.name})
                except ValueError: return self.send(400,{'error':'报告格式不正确。'})
                except OSError: return self.send(404,{'error':'未找到完整的报告文件。'})
            self.send(404,{'error':'未找到这个页面。'})

        def do_POST(self):
            # Read the bounded request body before replying, avoiding a reset on Windows.
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32768:
                    return self.send(400,{'error':'Invalid request size.'})
                raw_body=self.rfile.read(size)
            except (ValueError,OSError):
                return self.send(400,{'error':'Invalid request body.'})
            if not self.valid_host(): return
            if not self.valid_origin() or self.headers.get('X-Workbench-Token')!=workbench.token:
                return self.send(403,{'error':'请刷新本地工作台后再提交。'})
            if self.path not in {'/api/jobs','/api/briefings','/api/fixtures','/api/team-profile','/api/jobs/cancel'}: return self.send(404,{'error':'未找到操作。'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32768: raise ValueError('输入过长或为空。')
                payload=json.loads(raw_body.decode('utf-8'))
                if self.path=='/api/jobs/cancel':
                    if not isinstance(payload,dict) or set(payload)!={'job_id'} or not isinstance(payload['job_id'],str):
                        raise ValueError('取消任务参数无效。')
                    return self.send(200,{'job':workbench.cancel(payload['job_id'])})
                if self.path=='/api/team-profile':
                    return self.send(200,get_profile(payload))
                if self.path=='/api/fixtures':
                    if not isinstance(payload,dict) or not {'team'}<=set(payload) or not set(payload)<={'team','team_id'}:
                        raise ValueError('赛程查询参数无效。')
                    return self.send(200,get_fixtures(payload['team'],payload.get('team_id')))
                if self.path=='/api/briefings':
                    if (not isinstance(payload,dict) or not {'question','team','hours'}<=set(payload) or not set(payload)<={'question','team','hours','max_articles','search_depth'}): raise ValueError('Invalid search request.')
                    job_id=workbench.submit_briefing(payload['question'],payload['team'],payload['hours'],payload.get('max_articles',16),payload.get('search_depth','expanded'))
                else:
                    if not isinstance(payload,dict) or set(payload)!={'urls'}: raise ValueError('Invalid import request.')
                    job_id=workbench.submit(payload['urls'])
                self.send(202,{'job_id':job_id})
            except (ValueError,UnicodeError) as error: self.send(400,{'error':str(error)})
            except RuntimeError as error: self.send(409,{'error':str(error)})
    return Handler


def lan_addresses():
    """只接受这台电脑实际拥有的IPv4地址，拒绝任意Host反射。"""
    values=set()
    for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        address=row[4][0]
        ip=ipaddress.ip_address(address)
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            values.add(address)
    return sorted(values)


class NewsServer(ThreadingHTTPServer):
    # Windows不复用监听端口，避免两个服务同时响应同一个网址。
    allow_reuse_address=False
    request_queue_size=32

    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

    def server_close(self):
        super().server_close()
        if hasattr(self,'user_workbenches'):
            self.user_workbenches.queue.close()


def make_server(port=8765,workbench=None,auth_store=None,lan=False):
    store=auth_store or AuthStore(PROJECT_DIR/'data'/'accounts.sqlite3')
    root=workbench.output_dir if workbench is not None else OUTPUT_DIR
    data_root=store.path.parent/'users'
    if workbench is None:migrate_owner(store,root,data_root)
    router=UserWorkbenches(store,Workbench,root,data_root,provided=workbench)
    handler=authenticated_handler(handler_for(router),router,store,WEB_DIR)
    try:
        server=NewsServer(('0.0.0.0' if lan else '127.0.0.1',port),handler)
    except Exception:
        router.queue.close()
        raise
    server.allowed_hosts={'127.0.0.1',*(lan_addresses() if lan else [])}
    server.accounts=store;server.user_workbenches=router
    router.queue.start()
    return server


def main():
    parser=argparse.ArgumentParser(description='本机足球新闻工作台')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--lan',action='store_true',help='允许同局域网设备访问；默认仍只在本机监听')
    args=parser.parse_args()
    server=make_server(args.port,lan=args.lan)
    print(f'工作台已启动：http://127.0.0.1:{server.server_port}',flush=True)
    if args.lan:
        for address in sorted(server.allowed_hosts-{'127.0.0.1'}):
            print('局域网地址：http://'+address+':'+str(server.server_port),flush=True)
    print('浏览器打开上面的地址。关闭网页不会停止任务；结束服务请在此终端按Ctrl+C。',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: print('本地服务已停止。',flush=True)
    finally: server.server_close()


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    try: main()
    except OSError: print('启动失败：请检查端口是否被占用；可改用 python web_app.py --port 8766',flush=True); raise SystemExit(1)
