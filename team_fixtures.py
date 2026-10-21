"""赛程公告板：读取 FotMob 公开 HTML 与 sitemap，不调用模型或新闻 MCP。

公开页面不是保证稳定的官方 API。解析失败时明确提示；缓存过期后刷新，
若刷新失败只展示带时间与警告的旧缓存。比分与开球时间均来自数据源。
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from news_search import CHINA, parse_time
from project_paths import OUTPUT_DIR

CACHE_DIR=OUTPUT_DIR/'fixtures_cache'
from shared_io import resource_lock, atomic_write
LOCK=threading.RLock()
SOURCE_SLOTS=threading.BoundedSemaphore(4)
REFRESHING=set()
REFRESH_ERRORS={}
ALIASES={
 '马德里竞技':'atletico-madrid','马竞':'atletico-madrid','皇家马德里':'real-madrid','皇马':'real-madrid',
 '巴塞罗那':'barcelona','巴萨':'barcelona','曼联':'manchester-united','曼彻斯特联':'manchester-united',
 '曼城':'manchester-city','曼彻斯特城':'manchester-city','阿森纳':'arsenal','切尔西':'chelsea',
 '利物浦':'liverpool','热刺':'tottenham-hotspur','托特纳姆热刺':'tottenham-hotspur',
 '拜仁':'bayern-munchen','拜仁慕尼黑':'bayern-munchen','多特蒙德':'borussia-dortmund',
 '尤文图斯':'juventus','尤文':'juventus','国际米兰':'inter','国米':'inter','AC米兰':'ac-milan',
 '巴黎圣日耳曼':'paris-saint-germain','巴黎':'paris-saint-germain','大巴黎':'paris-saint-germain',
 '罗马':'roma','那不勒斯':'napoli','塞维利亚':'sevilla','皇家社会':'real-sociedad',
 '比利亚雷亚尔':'villarreal','毕尔巴鄂竞技':'athletic-club','西班牙人':'espanyol',
 '勒沃库森':'bayer-leverkusen','斯图加特':'vfb-stuttgart',
}
ZH_NAMES={slug:name for name,slug in ALIASES.items()}
ZH_NAMES.update({'atletico-madrid':'马德里竞技','real-madrid':'皇家马德里','barcelona':'巴塞罗那',
                 'manchester-united':'曼联','manchester-city':'曼城','paris-saint-germain':'巴黎圣日耳曼',
                 'bayern-munchen':'拜仁慕尼黑','inter':'国际米兰','juventus':'尤文图斯'})
LEAGUES={'LaLiga':'西甲','Champions League':'欧冠','Premier League':'英超','Copa del Rey':'国王杯',
         'Club Friendlies':'友谊赛','Bundesliga':'德甲','Serie A':'意甲','Ligue 1':'法甲','Super Cup':'超级杯'}


class FixedHostRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,response,code,message,headers,newurl):
        parts=urlsplit(newurl)
        if parts.scheme!='https' or parts.hostname!='www.fotmob.com':
            raise ValueError('赛程来源跳转到了不支持的地址。')
        return super().redirect_request(request,response,code,message,headers,newurl)


def download(path,timeout=15):
    # 调用者只能传程序构造的本站路径；不接受用户提供的网址。
    if not path.startswith('/') or path.startswith('//'):raise ValueError('赛程路径无效。')
    request=Request('https://www.fotmob.com'+path,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'en'})
    try:
        with SOURCE_SLOTS, build_opener(FixedHostRedirect()).open(request,timeout=timeout) as response:
            data=response.read(6_000_001)
        if len(data)>6_000_000:raise ValueError('赛程来源内容过大。')
        return data
    except OSError:
        raise ValueError('暂时无法连接 FotMob，请稍后再点确定。') from None


def save_cache(name,value):
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    atomic_write(CACHE_DIR/name,json.dumps(value,ensure_ascii=False))


def cached(name,max_age):
    path=CACHE_DIR/name
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or time.time()-value.get('saved_at',0)>max_age:return None
        return value.get('data')
    except (OSError,ValueError,TypeError):return None


def normalize(value):
    value=''.join(c for c in unicodedata.normalize('NFKD',value).lower() if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+','-',value).strip('-')


def parse_directory(raw):
    if b'<!DOCTYPE' in raw.upper():raise ValueError('球队目录格式无效。')
    try:root=ET.fromstring(raw)
    except ET.ParseError:raise ValueError('球队目录格式变更。') from None
    rows={}
    for node in root.findall('{*}url/{*}loc'):
        parts=urlsplit(node.text or '')
        match=re.fullmatch(r'/teams/(\d{1,8})/overview/([^/]+)',parts.path)
        if parts.hostname!='www.fotmob.com' or not match:continue
        tid,slug=match.groups();slug=unquote(slug)
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',slug):continue
        rows[tid]={'id':tid,'slug':slug,'name':ZH_NAMES.get(slug,slug.replace('-',' ').title())}
    return list(rows.values())


def directory_page(page,timeout=15):
    with resource_lock(('directory',page)):
        return _directory_page_locked(page,timeout)


def _directory_page_locked(page,timeout=15):
    name='directory_%d.json'%page
    rows=cached(name,86400)
    if rows is not None:return rows
    rows=parse_directory(download('/sitemap/en/teams/%d.xml'%page,timeout))
    if not rows:raise ValueError('来源没有返回球队目录。')
    save_cache(name,{'saved_at':time.time(),'data':rows});return rows


def resolve_team(query,team_id=None):
    if not isinstance(query,str) or not 1<=len(query.strip())<=60:raise ValueError('请输入1到60字的球队名称。')
    if team_id is not None:
        if not isinstance(team_id,str) or not re.fullmatch(r'\d{1,8}',team_id):raise ValueError('球队编号无效。')
        return {'id':team_id,'slug':'team','name':query.strip()}
    alias=next((value for name,value in ALIASES.items() if name.casefold()==query.strip().casefold()),query.strip())
    key=normalize(alias)
    if re.search(r'[\u4e00-\u9fff]',key):
        raise ValueError('这个中文译名暂未匹配，请尝试球队英文全名；常见球队可直接使用马竞、皇马、曼联等简称。')
    matches=[];started=time.monotonic()
    # 热门球队通常在第一页；其余按需查询，已查目录保存一天，避免每次重新扫描。
    for page in range(1,14):
        if time.monotonic()-started>35:break
        rows=directory_page(page,min(15,max(2,35-(time.monotonic()-started))))
        exact=[r for r in rows if normalize(r['slug'])==key]
        if len(exact)==1:return exact[0]
        if len(exact)>1:return {'choices':exact[:12]}
        matches.extend(r for r in rows if key in normalize(r['slug']) or key==normalize(r['name']))
        # 有匹配时返回候选，不把男队/女队/青年队或同名俱乐部悄悄视为同一支。
        if matches:return {'choices':matches[:12]}
    if matches:return {'choices':matches[:12]}
    raise ValueError('暂未找到该球队，请尝试英文全名或更完整的中文名称；较少见的中文译名可能尚未支持。')


def badge_url(team_id):
    value=str(team_id)
    return 'https://images.fotmob.com/image_resources/logo/teamlogo/'+value+'.png' if re.fullmatch(r'\d{1,8}',value) else None


def local_team(item):
    tid=str(item.get('id',''));name=str(item.get('name') or '球队名称待定')[:100]
    slug=normalize(name)
    return {'id':tid,'name':ZH_NAMES.get(slug,name),'badge':badge_url(tid)}


def parse_fixtures(raw,expected_id,now=None):
    now=now or datetime.now(CHINA)
    soup=BeautifulSoup(raw,'html.parser');script=soup.find('script',id='__NEXT_DATA__')
    try:
        data=json.loads(script.string or script.get_text())
        team=data['props']['pageProps']['fallback']['team-'+str(expected_id)]
        details=team['details'];fixtures=team['fixtures']['allFixtures']['fixtures']
        if str(details['id'])!=str(expected_id) or not isinstance(fixtures,list):raise ValueError()
    except (AttributeError,KeyError,TypeError,ValueError):
        raise ValueError('FotMob 球队页面结构变更，暂时无法提取赛程；可打开来源页面查看。') from None
    rows=[];seen=set()
    for fixture in fixtures[:300]:
        try:
            fid=str(fixture['id']);status=fixture['status'];home=fixture['home'];away=fixture['away']
            if fid in seen or str(expected_id) not in (str(home['id']),str(away['id'])):continue
            dt,_=parse_time(status.get('utcTime'))
            if dt is None:continue
            reason=str((status.get('reason') or {}).get('short','')).lower()
            state=('cancelled' if status.get('cancelled') else 'postponed' if 'postp' in reason else
                   'finished' if status.get('finished') else 'live' if status.get('started') else 'scheduled')
            hs,aws=home.get('score'),away.get('score')
            score=('%s - %s'%(hs,aws)) if state in ('finished','live') and type(hs) is int and type(aws) is int else None
            result=None
            if state=='finished' and score:
                ours,theirs=(hs,aws) if str(home['id'])==str(expected_id) else (aws,hs)
                result='win' if ours>theirs else 'loss' if ours<theirs else 'draw'
            page=fixture.get('pageUrl','')
            url='https://www.fotmob.com'+page if re.fullmatch(r'/matches/[A-Za-z0-9/_-]+(?:#[0-9]+)?',page) else None
            tournament=fixture.get('tournament') or {};league=str(tournament.get('name') or '赛事待定')[:100]
            seen.add(fid)
            rows.append({'id':fid,'home':local_team(home),'away':local_team(away),'score':score,'result':result,
                'state':state,'competition':LEAGUES.get(league,league),'utc_time':dt.isoformat(),
                'time':dt.astimezone(CHINA).strftime('%m-%d %H:%M'),'url':url})
        except (KeyError,TypeError,ValueError):continue
    if fixtures and not rows:raise ValueError('赛程条目未能通过检查，未把解析失败显示成暂无比赛。')
    past=sorted([r for r in rows if r['state']=='finished' and parse_time(r['utc_time'])[0]<=now],key=lambda r:r['utc_time'],reverse=True)[:5]
    future=sorted([r for r in rows if r['state']!='finished' and (parse_time(r['utc_time'])[0]>=now or r['state'] in ('live','postponed'))],key=lambda r:r['utc_time'])[:5]
    slug=details.get('seopath') or 'team'
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',slug):slug='team'
    return {'team':{'id':str(expected_id),'name':ZH_NAMES.get(slug,details.get('name','球队')),'badge':badge_url(expected_id)},
            'past':past,'future':future,'source':'FotMob 公开球队页面',
            'source_url':'https://www.fotmob.com/teams/'+str(expected_id)+'/fixtures/'+slug,
            'updated_at':datetime.now(CHINA).isoformat(),'stale':False,
            'note':'北京时间 · 过去最多5场、未来最多5场；赛程可能调整，以来源页面为准。'}


def refresh_team(team):
    """每支球队最多一个后台刷新；外网请求不占用全局缓存锁。"""
    tid=team['id']
    try:
        raw=download('/teams/'+tid+'/fixtures/'+team['slug'])
        result=parse_fixtures(raw,tid)
        with LOCK:
            save_cache('team_'+tid+'.json',{'saved_at':time.time(),'data':result})
            REFRESH_ERRORS.pop(tid,None)
    except (ValueError,OSError):
        with LOCK:REFRESH_ERRORS[tid]=time.time()
    finally:
        with LOCK:REFRESHING.discard(tid)


def get_fixtures(query,team_id=None):
    # 先解析本地已知编号，随后只在读写文件、检查状态时加锁。
    team=resolve_team(query,team_id)
    if 'choices' in team:return team
    tid=team['id'];name='team_'+tid+'.json'
    with LOCK:
        fresh=cached(name,900)
        if fresh is not None:
            return {**fresh,'refreshing':False}
        previous=cached(name,90*86400)
        if previous:
            failed_recently=time.time()-REFRESH_ERRORS.get(tid,0)<90
            if tid not in REFRESHING and not failed_recently:
                REFRESHING.add(tid)
                threading.Thread(target=refresh_team,args=(team,),daemon=True).start()
            updating=tid in REFRESHING
            return {**previous,'stale':True,'refreshing':updating,
                'warning':'已先显示保存的赛程，正在后台更新。' if updating else '本次刷新失败，当前展示旧缓存，请注意上次更新时间。'}
    # 首次查询没有任何缓存才需要等待网络。不同球队可以各自加载。
    with resource_lock(('fixture',tid)):
        fresh=cached(name,900)
        if fresh is not None:return {**fresh,'refreshing':False}
        raw=download('/teams/'+tid+'/fixtures/'+team['slug'])
        result=parse_fixtures(raw,tid)
        save_cache(name,{'saved_at':time.time(),'data':result})
        return {**result,'refreshing':False}
