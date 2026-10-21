"""新闻发现工具：查询Bing公开新闻RSS，返回候选链接，不把搜索摘要当正文。

这是可替换的搜索适配器。公开订阅可能改版或覆盖不足，不保证全网完整。
"""
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlencode,urlsplit,parse_qs
from urllib.request import Request,build_opener
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from news_http import ArticleRedirectHandler
from news_urls import normalize_news_url,validate_public_target

CHINA=timezone(timedelta(hours=8))
MAX_RESULTS=8


def parse_time(value):
    """返回带时区时间与精度。无法确认的时间不编造。"""
    if not isinstance(value,str) or not value.strip():return None,None
    value=value.strip()
    try:
        dt=parsedate_to_datetime(value)
        if dt:return dt.replace(tzinfo=dt.tzinfo or CHINA),'second'
    except (ValueError,TypeError,OverflowError):pass
    match=re.search(r'(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})(?:日)?(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?',value)
    if not match:return None,None
    year,month,day,hour,minute,second=match.groups()
    try:
        if 'T' in value:
            try:
                dt=datetime.fromisoformat(value.replace('Z','+00:00'))
                return dt.replace(tzinfo=dt.tzinfo or CHINA),'second' if second else 'minute'
            except ValueError:pass
        return datetime(int(year),int(month),int(day),int(hour or 0),int(minute or 0),int(second or 0),tzinfo=CHINA),'day' if hour is None else 'second'
    except ValueError:return None,None


def within_window(dt,start,end):
    return dt is not None and start<=dt<=end


def direct_news_url(url):
    """RSS链接常是Bing跳转页；只解开其已知url字段，不让网页推荐触发额外访问。"""
    parts=urlsplit(url or '')
    if parts.hostname in {'www.bing.com','bing.com','cn.bing.com'} and parts.path=='/news/apiclick.aspx':
        values=parse_qs(parts.query).get('url',[])
        if len(values)!=1:raise ValueError('搜索结果没有具体新闻地址。')
        url=values[0]
    return normalize_news_url(url)


def parse_rss(raw):
    if len(raw)>600000 or b'<!DOCTYPE' in raw.upper():raise ValueError('搜索返回过大或格式不受支持。')
    try:root=ET.fromstring(raw)
    except ET.ParseError:raise ValueError('搜索没有返回可读新闻订阅，可能暂时受限。') from None
    if root.tag.split('}')[-1] not in {'rss','feed'}:raise ValueError('搜索返回了网页外壳，不能当作新闻结果。')
    found=[]
    for item in root.findall('.//item')[:30]:
        title=(item.findtext('title') or '').strip()
        if not title:continue
        try:url,article_id=direct_news_url(item.findtext('link'))
        except (ValueError,TypeError):continue
        dt,precision=parse_time(item.findtext('pubDate'))
        source=''
        for child in item:
            if child.tag.split('}')[-1].lower()=='source':source=(child.text or '').strip()
        description=BeautifulSoup(item.findtext('description') or '', 'html.parser').get_text(' ',strip=True)[:800]
        found.append({'article_id':article_id,'url':url,'title':title,'snippet':description,
                      'search_reported_at':dt.isoformat() if dt else None,'search_source':source,
                      'date_precision':precision})
    return found


def search_news(query):
    if not isinstance(query,str) or not 1<=len(query.strip())<=150:raise ValueError('搜索关键词须为1到150字。')
    url='https://www.bing.com/news/search?'+urlencode({'q':query.strip(),'format':'rss'})
    # 与正文读取一样检查公网地址和重定向；没有自动网络重试。
    target=validate_public_target(url)
    request=Request(target,headers={'User-Agent':'Mozilla/5.0','Accept':'application/rss+xml,application/xml;q=0.9'})
    with build_opener(ArticleRedirectHandler()).open(request,timeout=20) as response:raw=response.read(600001)
    return {'query':query,'provider':'Bing新闻公开订阅','articles':parse_rss(raw),
            'scope':'仅此新闻索引返回的候选，不代表全网覆盖；日期为搜索提供的标注，尚需页面核对。'}


def select_candidates(results,start,end,limit=MAX_RESULTS,allow_missing_dates=False):
    """规范URL去重、先按搜索时间筛选；下一步读正文后还要核对页面发布时间。"""
    seen=set();candidates=[];excluded=[]
    for result in results:
        for row in result['articles']:
            if row['article_id'] in seen:continue
            seen.add(row['article_id'])
            dt,_=parse_time(row.get('search_reported_at'))
            reason=None
            if not within_window(dt,start,end) and not (allow_missing_dates and dt is None):reason='搜索标注时间不在范围内或缺失'
            elif re.search(r'半全场|赔率|盘口|投注|竞彩推荐|竞彩足球',row['title']):reason='疑似投注推荐标题'
            if reason:excluded.append({'title':row['title'],'url':row['url'],'reason':reason});continue
            candidates.append(row)
    candidates.sort(key=lambda row:parse_time(row['search_reported_at'])[0] or start,reverse=True)
    return candidates[:limit],excluded,len(candidates)-min(len(candidates),limit)