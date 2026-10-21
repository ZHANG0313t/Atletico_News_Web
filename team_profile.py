"""球队档案：公开赛季统计和球员照片；独立于新闻 Agent，不消耗模型额度。

先读球队页面确认可选赛事/赛季，再按来源给出的赛事编号读取统计。
当前阵容与历史出场名单分开，避免把今天的球员套到旧赛季。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import json
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from bs4 import BeautifulSoup
from project_paths import OUTPUT_DIR
from news_search import CHINA
from team_fixtures import resolve_team, download, badge_url, ZH_NAMES, LEAGUES, SOURCE_SLOTS
from shared_io import atomic_write

CACHE_DIR = OUTPUT_DIR / 'team_profile_cache'
LOCK = threading.RLock()
KEY_LOCKS = {}



def numeric(value):
    """来源缺失保持 None；不能把没有提供的数据当作零。"""
    return value if type(value) in (int, float) and 0 <= value < 1000000 else None


def safe_id(value):
    value = str(value)
    if not re.fullmatch(r'[0-9]{1,8}', value):
        raise ValueError('球队或赛事编号无效。')
    return value


def photo_url(pid):
    return 'https://images.fotmob.com/image_resources/playerimages/' + safe_id(pid) + '.png'


def cached(key, seconds):
    try:
        doc = json.loads((CACHE_DIR / (key + '.json')).read_text(encoding='utf-8'))
        if time.time() - doc['saved_at'] <= seconds:
            return doc['data']
    except (OSError, ValueError, KeyError, TypeError):
        pass


def remember(key, data):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / (key + '.json')
    atomic_write(path,json.dumps({'saved_at':time.time(), 'data':data}, ensure_ascii=False))


def once(key, build, age=10800):
    # 同一球队/赛季只让一个请求下载；不同球队不互相阻塞。
    with LOCK:
        lock = KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:
        value = cached(key, age)
        if value is not None:
            return value
        try:
            value = build()
            remember(key, value)
            return value
        except (ValueError, OSError):
            old = cached(key, 30 * 86400)
            if old is not None:
                return {**old, 'stale':True, 'warning':'来源暂时不可用，当前展示上次保存的数据，请留意更新时间。'}
            raise


class StatsRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname != 'data.fotmob.com' or urlsplit(newurl).scheme != 'https':
            raise ValueError('统计来源重定向无效。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def stat_file(path):
    # 不接受客户端传来的 URL，只能下载代码选定的三个统计字段。
    if not re.fullmatch(r'/stats/[0-9]+/season/[0-9]+/(goals|goal_assist|mins_played)\.json', path):
        raise ValueError('统计路径无效。')
    try:
        req = Request('https://data.fotmob.com' + path, headers={'User-Agent':'Mozilla/5.0'})
        with SOURCE_SLOTS, build_opener(StatsRedirect()).open(req, timeout=15) as response:
            raw = response.read(4000001)
        if len(raw) > 4000000:
            raise ValueError('统计文件过大。')
        if raw[:2] == b'\x1f\x8b':
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                raw = zipped.read(8000001)
        if len(raw) > 8000000:
            raise ValueError('解压后的统计文件过大。')
        return json.loads(raw)
    except (OSError, ValueError, EOFError):
        raise ValueError('暂时无法读取球员统计，请稍后重试。') from None


def team_data(raw, tid):
    try:
        script = BeautifulSoup(raw, 'html.parser').find('script', id='__NEXT_DATA__')
        data = json.loads(script.string)['props']['pageProps']['fallback']['team-' + tid]
        if str(data['details']['id']) != tid:
            raise ValueError()
        return data
    except (AttributeError, TypeError, KeyError, ValueError):
        raise ValueError('球队资料页面结构已变更，未展示未经确认的数据。') from None


def role(positions):
    values = [p for p in positions or [] if type(p) is int]
    if 11 in values:return '门将'
    if any(30 <= p < 40 for p in values):return '后卫'
    if any(50 <= p < 80 for p in values):return '中场'
    if any(100 <= p < 110 for p in values):return '前锋'
    return '未知'


def catalog_from(data, tid):
    details = data['details'];stats = data.get('stats') or {}
    options = []
    for item in stats.get('tournamentSeasons', []):
        if not isinstance(item, dict):continue
        try:
            season = item['season']
            if not re.fullmatch(r'20[0-9]{2}(?:/20[0-9]{2})?', season):continue
            tournament = safe_id(item['tournamentId']);league = safe_id(item['parentLeagueId'])
            name = str(item.get('leagueName') or item['name'])[:80]
            options.append({'id':tournament, 'season':season, 'league_id':league,
                            'competition':LEAGUES.get(name, name)})
        except (KeyError, TypeError, ValueError):continue
    # 用主联赛确定最近三季，避免单年制的“2025世俱杯”挤掉2024/2025。
    primary = str(stats.get('primaryLeagueId') or '')
    base = {o['season'] for o in options if o['league_id'] == primary}
    seasons = sorted(base or {o['season'] for o in options}, reverse=True)[:3]
    options = [o for o in options if o['season'] in seasons]
    if not options:raise ValueError('该球队暂未提供可读取的赛季统计。')
    roster = [];coach = []
    groups = (data.get('squad') or {}).get('squad') or []
    titles = {'keepers':'门将','defenders':'后卫','midfielders':'中场','attackers':'前锋','forwards':'前锋'}
    for group in groups:
        for member in group.get('members', []):
            try:
                pid = safe_id(member['id']);name = str(member['name'])[:100]
                if group.get('title') == 'coach':coach.append(name);continue
                roster.append({'id':pid,'name':name,'original_name':name,
                    'photo':photo_url(pid),'number':numeric(member.get('shirtNumber')),
                    'position':titles.get(group.get('title'), '未知'),
                    'country':str(member.get('cname') or member.get('ccode') or '—')[:80]})
            except (KeyError, TypeError, ValueError):continue
    slug = details.get('seopath') or 'team'
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', slug):slug = 'team'
    venue = ((data.get('overview') or {}).get('venue') or {}).get('widget') or {}
    return {'team':{'id':tid,'name':ZH_NAMES.get(slug,details.get('name','球队')),'badge':badge_url(tid)},
        'options':options, 'default_tournament':str(stats.get('tournamentId') or options[0]['id']),
        'latest_season':str(details.get('latestSeason') or seasons[0]),'roster':roster,
        'coach':'、'.join(coach), 'venue':str(venue.get('name') or '暂无数据')[:100],
        'source_url':'https://www.fotmob.com/teams/' + tid + '/stats/' + slug,
        'updated_at':datetime.now(CHINA).isoformat(), 'stale':False}


def get_catalog(query, team_id=None):
    team = resolve_team(query, team_id)
    if 'choices' in team:return team
    tid = safe_id(team['id'])
    return once('catalog_v2_' + tid, lambda:catalog_from(team_data(download('/teams/' + tid + '/stats/' + team['slug']),tid),tid))


def summary_from(data, tid):
    lines = ((data.get('tournamentStats') or {}).get('Table') or {}).get('Lines') or []
    for row in lines:
        if str(row.get('TeamId')) == tid:
            result = {k:numeric(row.get(v)) for k,v in {'wins':'Won','draws':'Drawn','losses':'Lost',
                'goals':'Scored','conceded':'Conceeded','points':'Points','rank':'Position'}.items()}
            result['played'] = sum(result[k] for k in ('wins','draws','losses')) if all(result[k] is not None for k in ('wins','draws','losses')) else None
            return result
    # 杯赛没有联赛积分表时，不把缺失的排名/积分变成0。
    return {}


def season_players(files, tid):
    players = {};warnings = []
    fields = {'mins_played':'minutes','goals':'goals','goal_assist':'assists'}
    for stat, doc in files.items():
        if doc is None:
            warnings.append({'mins_played':'出场时间','goals':'进球','goal_assist':'助攻'}[stat] + '榜单暂未读取，缺失值显示 —。')
            continue
        lists = [x for x in doc.get('TopLists',[]) if isinstance(x,dict) and x.get('StatName') == stat] if isinstance(doc,dict) else []
        if not lists:
            warnings.append('来源未返回完整的' + stat + '字段。');continue
        for row in lists[0].get('StatList', []):
            if not isinstance(row,dict):continue
            if str(row.get('TeamId')) != tid:continue
            try:
                pid = safe_id(row['ParticiantId']);name = str(row['ParticipantName'])[:100]
                p = players.setdefault(pid, {'id':pid, 'name':name,'original_name':name,
                    'photo':photo_url(pid),'position':role(row.get('Positions')), 'appearances':None,
                    'minutes':None,'goals':None,'assists':None})
                p[fields[stat]] = numeric(row.get('StatValue'))
                if stat == 'mins_played':p['appearances'] = numeric(row.get('MatchesPlayed'))
            except (KeyError, TypeError, ValueError):continue
    return sorted(players.values(), key=lambda p: (-(p['minutes'] or 0),p['name'])), warnings


def get_season(query, team_id, tournament_id):
    catalog = get_catalog(query, team_id)
    tournament_id = safe_id(tournament_id)
    option = next((o for o in catalog['options'] if o['id'] == tournament_id),None)
    if option is None:raise ValueError('该赛事不在这支球队最近三个赛季的统计范围内。')
    tid = catalog['team']['id']
    def build():
        try:
            raw = download('/api/data/teamseasonstats?teamId=' + tid + '&tournamentId=' + tournament_id + '&isTeamSubTab=false')
            data = json.loads(raw)
            if not isinstance(data,dict) or str(data.get('tournamentId')) != tournament_id or str(data.get('currentTournament')) != tournament_id:
                raise ValueError()
        except (ValueError, TypeError, OSError):
            raise ValueError('指定赛季未能通过校验，请稍后重试；不会用当前赛季代替。') from None
        urls = {x.get('name'):x.get('fetchAllUrl') for x in data.get('players',[]) if isinstance(x,dict)}
        def read(stat):
            expected = '/stats/' + option['league_id'] + '/season/' + tournament_id + '/' + stat + '.json'
            if urls.get(stat) != 'https://data.fotmob.com' + expected:return stat,None
            try:return stat,stat_file(expected)
            except (ValueError, OSError):return stat,None
        # 三个相互独立的字段并行读取，无模型和付费搜索调用。
        with ThreadPoolExecutor(max_workers=3) as pool:
            files = dict(pool.map(read, ['mins_played','goals','goal_assist']))
        players,warnings = season_players(files, tid)
        if not players:raise ValueError('该赛事暂未提供可用球员统计，请切换其他赛事或稍后重试。')
        return {'team':catalog['team'],'selection':option,'summary':summary_from(data,tid),
            'players':players,'warnings':warnings, 'source_url':catalog['source_url'],
            'updated_at':datetime.now(CHINA).isoformat(),'stale':False,
            'note':'仅统计所选赛事。名单来自球员统计榜单，不是完整注册名单；— 表示来源未提供或榜单无该项记录，不等于零。'}
    return once('season_' + tid + '_' + tournament_id, build)


def get_profile(payload):
    if not isinstance(payload,dict) or not {'team'} <= set(payload) or not set(payload) <= {'team','team_id','tournament_id'}:
        raise ValueError('球队档案参数无效。')
    if 'tournament_id' in payload:
        if not payload.get('team_id'):raise ValueError('请先选择球队。')
        return get_season(payload['team'],payload['team_id'],payload['tournament_id'])
    return get_catalog(payload['team'],payload.get('team_id'))
