"""通用新闻入口：网址 → 稳定编号 → 缓存或公开网页 → 统一正文。

已验证懂球帝、腾讯、新浪、MSN，同时尝试标准文章结构与通用正文提取。
新网站不必预先登记；能访问网页不代表一定能准确提取全文。
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from project_paths import OUTPUT_DIR
from news_urls import normalize_news_url
from news_storage import article_path_for, format_article, read_article
from news_http import ArticleRedirectHandler, download_document
from news_extract import extract_html_article, extract_msn_article
from shared_io import resource_lock, atomic_write


def fetch_news(url):
    canonical_url, article_id = normalize_news_url(url)
    with resource_lock(OUTPUT_DIR / article_id):
        return _fetch_news_locked(canonical_url)


def _fetch_news_locked(url):
    canonical_url, article_id = normalize_news_url(url)
    text_path = article_path_for(article_id, OUTPUT_DIR)
    html_path = text_path.with_suffix('.html')
    metadata_path = text_path.with_suffix('.meta.json')
    for path in (html_path, metadata_path):
        if path.resolve().parent != OUTPUT_DIR.resolve():
            raise ValueError('保存路径必须位于output目录。')
    if text_path.exists():
        article = read_article(text_path)
        metadata = {}
        if metadata_path.exists():
            try:
                stored = json.loads(metadata_path.read_text(encoding='utf-8'))
                if isinstance(stored, dict):
                    metadata = stored
            except (ValueError, OSError):
                pass  # 旧的或损坏的附加元数据不冒充有效提取记录，也不覆盖正文。
        # 已保存HTML也能离线修复旧提取结果，不需要重新联网或删除用户文件。
        reparsed = False
        host = urlsplit(canonical_url).hostname or ''
        if (host in {'k.sina.com.cn', 'news.sina.com.cn', 'cj.sina.com.cn', 'cj.sina.cn'}
                and html_path.exists() and metadata.get('extraction_method') != 'site:sina-article-v2'):
            candidate = extract_html_article(html_path.read_text(encoding='utf-8'), canonical_url)
            if candidate.get('extraction_method') == 'site:sina-article-v2':
                candidate['url'] = canonical_url
                repaired_text = format_article(candidate)
                backup = text_path.with_suffix('.before-sina-v2.txt')
                if not backup.exists():
                    with backup.open('x', encoding='utf-8') as handle:
                        handle.write(text_path.read_text(encoding='utf-8'))
                atomic_write(text_path, repaired_text)
                metadata.update(extraction_method=candidate['extraction_method'],
                                reextracted_from_saved_html=True)
                atomic_write(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
                article = candidate
                reparsed = True
        return {'article_id': article_id, 'source_status': 'local_cache', 'fetched_at_utc': None,
                'cached_fetched_at_utc': metadata.get('fetched_at_utc'),
                'reextracted_from_saved_html': reparsed,
                'extraction_method': metadata.get('extraction_method', 'unknown:legacy-cache'),
                'requested_url': canonical_url, **article,
                'note': '复用本地文本，本次没有联网，不是重新获取的最新版本；提取方法未知时请人工检查正文。'}

    document = download_document(canonical_url)
    raw = document['text']
    extra_document = None
    host = urlsplit(document['url']).hostname or ''
    if host in {'msn.cn', 'www.msn.cn', 'msn.com', 'www.msn.com'}:
        # 编号来自用户MSN链接，不从标题臆测新闻内容，也不自动访问其标注的外部来源。
        match = re.search(r'/([a-z]{2}-[a-z]{2})/.*/ar-([A-Za-z0-9]+)', document['url'], re.I)
        if not match:
            raise ValueError('未找到MSN文章编号，请提供具体文章链接。')
        market, msn_id = match.groups()
        service_host = 'assets.msn.cn' if host.endswith('.cn') else 'assets.msn.com'
        endpoint = f'https://{service_host}/content/view/v2/Detail/{market}/{msn_id}'
        extra_document = download_document(endpoint)
        article = extract_msn_article(json.loads(extra_document['text']), msn_id)
        content_url = extra_document['url']
    else:
        article = extract_html_article(raw, document['url'])
        content_url = document['url']
    article['url'] = canonical_url
    text = format_article(article)  # 先验证格式和大小，成功后才写入新闻TXT
    fetched_at = datetime.now(timezone.utc).isoformat()
    metadata = {'article_id': article_id, 'requested_url': canonical_url,
                'resolved_url': document['url'], 'content_url': content_url,
                'fetched_at_utc': fetched_at, 'extraction_method': article['extraction_method'],
                'platform': article['platform'], 'provider': article['provider'],
                'source_url': article['source_url'],
                'note': '页面标注供稿方和来源链接未经独立核对；通用提取也需要人工检查完整性。'}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(html_path, raw)
    if extra_document:
        data_path = text_path.with_suffix('.content.json')
        if data_path.resolve().parent != OUTPUT_DIR.resolve():
            raise ValueError('内容数据保存路径不正确。')
        atomic_write(data_path, extra_document['text'])
    atomic_write(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
    atomic_write(text_path, text)
    return {'article_id': article_id, 'source_status': 'downloaded', 'fetched_at_utc': fetched_at,
            **article, 'note': metadata['note']}