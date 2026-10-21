"""通用网址与文章编号：支持公开HTTP/HTTPS新闻链接，不依赖平台名单。"""
import hashlib
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

ARTICLE_ID_PATTERN = r"(?:[0-9]{1,12}|web_[0-9a-f]{24})"
TRACKING_KEYS = {'from', 'ocid', 'cvid', 'ei', 'spm', 'share', 'sharefrom', 'share_source', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'}


def normalize_news_url(url):
    """去掉明确的分享跟踪参数；保留可能决定正文的参数（例如id）。"""
    if not isinstance(url, str) or not url.strip() or len(url) > 6000:
        raise ValueError('请提供有效的HTTP或HTTPS新闻网址。')
    # 用户从Markdown复制时可能带\&，这不是网址本身的一部分。
    parts = urlsplit(url.strip().replace('\\&', '&'))
    host = (parts.hostname or '').rstrip('.').lower()
    if (parts.scheme not in ('http', 'https') or not host or parts.username is not None
            or parts.password is not None or parts.port not in (None, 80, 443)):
        raise ValueError('只接受不含账号密码、使用标准端口的HTTP/HTTPS公开网页。')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
        raise ValueError('不能读取本机或内网地址。')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('不能读取本机、内网或保留地址。')
    host = host.encode('idna').decode('ascii')
    netloc = ('[' + host + ']') if ':' in host else host
    if parts.port is not None and parts.port != (443 if parts.scheme == 'https' else 80):
        netloc += ':' + str(parts.port)
    path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
    # 保留懂球帝已有数字编号与文件，前面几课的内容继续可用。
    dqd = re.fullmatch(r'/articles/([0-9]{1,12})(?:\.html)?/?', path)
    if host in {'www.dongqiudi.com', 'dongqiudi.com'} and dqd:
        return 'https://www.dongqiudi.com/articles/' + dqd.group(1), dqd.group(1)
    if host in {'www.msn.cn', 'msn.cn', 'www.msn.com', 'msn.com'}:
        msn = re.search(r'/ar-([A-Za-z0-9]+)(?:/)?$', path)
        if msn:
            market = re.match(r'/([a-z]{2}-[a-z]{2})/', path, re.I)
            market = market.group(1).lower() if market else 'zh-cn'
            # MSN用内容编号定位文章，标题变化和分享参数不产生新记录。
            path = f'/{market}/news/other/ar-{msn.group(1)}'
    params = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in TRACKING_KEYS or key.lower().startswith('utm_'):
            continue
        if host in {'cj.sina.com.cn', 'cj.sina.cn'} and key.lower() == 'subch':
            continue
        if host.endswith(('.msn.cn', '.msn.com')) and key.lower() in {'ocid', 'cvid', 'ei'}:
            continue
        params.append((key, value))
    canonical = urlunsplit((parts.scheme.lower(), netloc, path, urlencode(sorted(params)), ''))
    article_id = 'web_' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]
    return canonical, article_id


def validate_public_target(url):
    """实际请求前解析域名，拒绝落到内网；重定向目标也执行同样检查。"""
    canonical, _ = normalize_news_url(url)
    parts = urlsplit(canonical)
    try:
        entries = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError('新闻网站域名解析失败，请检查网址或网络。') from None
    if not entries or any(not ipaddress.ip_address(item[4][0]).is_global for item in entries):
        raise ValueError('新闻网址解析到了非公网地址，已停止请求。')
    return canonical


def extract_user_urls(question):
    """从用户本次问题中取得网址，供调度器限制工具读取范围。"""
    found = []
    for raw in re.findall(r'https?://[^\s<>"\[\]]+', question):
        raw = raw.rstrip('.,;:!?，。；：！？、)]}）】>')
        try:
            canonical, _ = normalize_news_url(raw)
        except ValueError:
            continue
        if canonical not in found:
            found.append(canonical)
    return found