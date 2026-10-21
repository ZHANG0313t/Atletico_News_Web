"""正文提取层：已验证的平台规则 + 标准结构化数据 + 通用正文候选。

网站结构不同，但最终都返回同一种文章字典。未知网站不需要先加入名单。
通用提取可能不完整，因此会记录使用的方法，失败不拿摘要冒充全文。
"""
import json
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from dqd_parser import NewsParser

PLATFORMS = {'news.qq.com': '腾讯新闻', 'cj.sina.com.cn': '新浪财经', 'cj.sina.cn': '新浪财经',
             'k.sina.com.cn': '新浪看点', 'news.sina.com.cn': '新浪新闻',
             'www.dongqiudi.com': '懂球帝', 'dongqiudi.com': '懂球帝',
             'www.msn.cn': 'MSN', 'www.msn.com': 'MSN', 'msn.cn': 'MSN', 'msn.com': 'MSN'}


def meta(soup, *names):
    for name in names:
        tag = soup.find('meta', attrs={'property': name}) or soup.find('meta', attrs={'name': name})
        if tag and tag.get('content', '').strip():
            return tag['content'].strip()
    return ''


def clean_text(fragment):
    """保留段落，移除导航、评论、脚本和已知推广区域，保留正文中的引用链接文字。"""
    soup = BeautifulSoup(str(fragment), 'html.parser')
    for node in soup.select('script, style, nav, footer, header, aside, form, button, iframe, noscript, .related, .recommend, .recommendations, .comments, #comments, .share, .advertisement, .ad, .article-notice, [hidden], [aria-hidden="true"]'):
        node.decompose()
    for node in list(soup.find_all(style=re.compile(r'display\s*:\s*none', re.I))):
        if node.parent:
            node.decompose()
    for node in list(soup.find_all('a', href=True)):
        if 'n.dongqiudi.com/webapp/plan.html' in node['href']:
            node.decompose()
    paragraphs = soup.find_all(['p', 'h2', 'h3', 'blockquote', 'li'])
    # 避免blockquote等父子标签把同一句输出两遍。
    leaves = [node for node in paragraphs if not node.find(['p', 'h2', 'h3', 'blockquote', 'li'])]
    lines = [re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip() for node in leaves]
    lines = [line for line in lines if line]
    if not lines:
        lines = [line.strip() for line in soup.get_text('\n', strip=True).splitlines() if line.strip()]
    return '\n\n'.join(lines)


def structured_articles(soup):
    """读取标准JSON-LD，只关注Article而非网页脚本里的推荐列表。"""
    found = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for root in list(nodes):
            if isinstance(root, dict):
                graph = root.get('@graph', [])
                if isinstance(graph, list):
                    nodes.extend(graph)
                if isinstance(root.get('mainEntity'), dict):
                    nodes.append(root['mainEntity'])
        for node in nodes[:100]:
            if not isinstance(node, dict):
                continue
            types = node.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any('article' in str(item).lower() for item in types):
                found.append(node)
    return found


def person_name(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get('name') or '')
    if isinstance(value, list):
        return '、'.join(filter(None, (person_name(item) for item in value)))
    return ''


def verify_article(article):
    """做基础完整性检查，不能由此证明新闻真实或正文绝对完整。"""
    title, body = article.get('title'), article.get('body')
    if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
        raise ValueError('没有提取到有效新闻标题或正文。')
    minimum = 40 if article.get('extraction_method') == 'site:sina-article-v2' else 80
    if len(''.join(body.split())) < minimum:
        raise ValueError('可提取正文过短，可能只是摘要、页面外壳、登录或验证页，未保存为新闻。')
    if title.strip().lower() in {'msn', 'access denied', '人机验证', '安全验证', '访问受限'}:
        raise ValueError('取得的可能是页面外壳或访问验证页，未保存为新闻。')
    for field in ('title', 'author', 'published_at', 'platform', 'provider', 'source_url'):
        if not isinstance(article.get(field, ''), str):
            article[field] = ''
    return article


def extract_html_article(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    host = urlsplit(url).hostname or ''
    schemas = structured_articles(soup)
    schema = schemas[0] if schemas else {}
    h1 = soup.select_one('h1.main-title, h1.article-title, h1[itemprop="headline"]') or soup.find('h1')
    article = {
        'title': (h1.get_text(' ', strip=True) if h1 else '') or str(schema.get('headline') or '') or meta(soup, 'og:title', 'twitter:title') or (soup.title.get_text(strip=True) if soup.title else ''),
        'author': person_name(schema.get('author')) or meta(soup, 'article:author', 'author'),
        'published_at': str(schema.get('datePublished') or '') or meta(soup, 'article:published_time', 'weibo: article:create_at', 'pubdate', 'publishdate', 'date'),
        'platform': PLATFORMS.get(host, host), 'provider': '', 'source_url': '', 'body': '',
    }
    if host in {'www.dongqiudi.com', 'dongqiudi.com'}:
        parser = NewsParser()
        parser.feed(html)
        parser.close()
        parsed = parser.get_article()
        if parsed.get('body'):
            article.update(parsed)
            article['extraction_method'] = 'site:dongqiudi'
    # 新浪新版的栏目logo也是h1，正文外层还包含热榜和页脚；只取具体文章容器。
    if host in {'k.sina.com.cn', 'news.sina.com.cn', 'cj.sina.com.cn', 'cj.sina.cn'} and not article['body']:
        node = soup.select_one('#artibody, #article.article')
        if node:
            article['body'] = clean_text(node)
            article['extraction_method'] = 'site:sina-article-v2'
    # 已验证的平台规则。通用网站不需要命中这些分支。
    selector = '.rich_media_content' if host in {'news.qq.com', 'view.inews.qq.com'} else '#artibody' if 'sina.' in host else None
    if not article['body'] and selector:
        node = soup.select_one(selector)
        if node:
            article['body'] = clean_text(node)
            article['extraction_method'] = 'site:qq' if selector == '.rich_media_content' else 'site:sina'
    if not article['body']:
        for item in schemas:
            if isinstance(item.get('articleBody'), str) and item['articleBody'].strip():
                article['body'] = clean_text(item['articleBody'])
                article['extraction_method'] = 'standard:json-ld'
                break
    if not article['body']:
        selectors = ['[itemprop="articleBody"]', 'article', '.article-content', '.article_content',
                     '.post-content', '.entry-content', '.news-content', '.content-body', '#articleContent', 'main', '[role="main"]']
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = clean_text(node)
                if len(''.join(text.split())) >= 80:
                    article['body'] = text
                    article['extraction_method'] = 'standard:article-container'
                    break
    if not article['body']:
        # 最后才用正文密度启发式。不会直接把整个body当正文。
        candidates = []
        for node in soup.find_all(['div', 'section'])[:1000]:
            paragraphs = node.find_all('p')
            if not 2 <= len(paragraphs) <= 80:
                continue
            text_length = sum(len(p.get_text(strip=True)) for p in paragraphs)
            link_length = sum(len(a.get_text(strip=True)) for a in node.find_all('a'))
            if text_length < 80 or link_length > text_length * 0.4:
                continue
            score = text_length - link_length * 2 - len(str(node)) * 0.015
            candidates.append((score, node))
        if candidates:
            node = max(candidates, key=lambda item: item[0])[1]
            article['body'] = clean_text(node)
            article['extraction_method'] = 'generic:heuristic-review-needed'
    if not article['body']:
        raise ValueError('网页可访问，但未识别出正文。可能需要JavaScript、登录、验证，或该页面不是文章。没有用摘要冒充全文。')
    return verify_article(article)


def extract_msn_article(data, expected_id):
    """MSN外壳不含正文，使用它自身展示文章所用的公开内容数据。"""
    if not isinstance(data, dict) or str(data.get('id', '')).lower() != expected_id.lower():
        raise ValueError('MSN返回的内容编号不匹配，未保存。')
    if data.get('type') not in (None, 'article') or data.get('renderingRestriction') not in (None, 0):
        raise ValueError('这条MSN内容不是可直接读取的公开文章，未保存。')
    body = data.get('body')
    if not isinstance(body, str) or not body.strip():
        raise ValueError('MSN未返回正文，可能已下架或需要登录，未保存。')
    provider = data.get('provider') or {}
    source_url = str(data.get('sourceHref') or '')
    if urlsplit(source_url).scheme not in ('http', 'https'):
        source_url = ''
    article = {'title': str(data.get('title') or ''), 'body': clean_text(body),
               'author': person_name(data.get('authors')), 'published_at': str(data.get('publishedDateTime') or ''),
               'platform': 'MSN', 'provider': person_name(provider), 'source_url': source_url,
               'extraction_method': 'site:msn-public-content'}
    return verify_article(article)