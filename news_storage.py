"""所有新闻使用同一种文本格式；数字旧编号和通用网址编号都可读。"""
from pathlib import Path
import re
from news_urls import ARTICLE_ID_PATTERN

MAX_ARTICLE_CHARS = 16000


def article_path_for(article_id, root):
    if not isinstance(article_id, str) or not re.fullmatch(ARTICLE_ID_PATTERN, article_id):
        raise ValueError('文章编号格式不正确，请使用工具返回的article_id，不要自行猜测。')
    root = Path(root).resolve()
    path = (root / f'{article_id}.txt').resolve()
    if path.parent != root:
        raise ValueError('文章路径必须位于output目录。')
    return path


def format_article(article):
    fields = [('标题', 'title'), ('作者', 'author'), ('发布时间', 'published_at'),
              ('发布平台', 'platform'), ('页面标注供稿方', 'provider'),
              ('链接', 'url'), ('页面标注来源链接（未核对）', 'source_url')]
    # 元数据不允许换行伪造其他字段，正文则保留段落。
    lines = [name + '：' + ' '.join(str(article.get(key) or '未提供').splitlines()) for name, key in fields]
    text = '\n'.join(lines) + '\n\n正文：\n' + article['body'].strip() + '\n'
    if len(text) > MAX_ARTICLE_CHARS:
        raise ValueError('文章超过本课16000字符处理上限，未保存，未截断正文。')
    return text


def read_article(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    if not text.strip() or len(text) > MAX_ARTICLE_CHARS:
        raise ValueError(f'文章为空或超过16000字符：{Path(path).name}')
    if '正文：\n' not in text:
        raise ValueError(f'找不到正文标记，请检查新闻TXT：{Path(path).name}')
    header, body = text.split('正文：\n', 1)
    if not body.strip():
        raise ValueError(f'正文为空：{Path(path).name}')
    metadata = {}
    for line in header.splitlines():
        name, separator, value = line.partition('：')
        if separator:
            metadata[name.strip()] = value.strip()
    return {'title': metadata.get('标题', Path(path).stem), 'author': metadata.get('作者', '未提供'),
            'published_at': metadata.get('发布时间', '未提供'), 'url': metadata.get('链接', '未提供'),
            'platform': metadata.get('发布平台', '未提供'), 'provider': metadata.get('页面标注供稿方', '未提供'),
            'source_url': metadata.get('页面标注来源链接（未核对）', '未提供'), 'body': body.strip()}