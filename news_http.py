"""网页获取层：公开地址检查、重定向、大小限制、编码识别。"""
import gzip
import io
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError
from bs4 import UnicodeDammit
from news_urls import validate_public_target

MAX_BYTES = 5 * 1024 * 1024


class ArticleRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, request, response, code, message, headers, newurl):
        target = validate_public_target(newurl)
        return super().redirect_request(request, response, code, message, headers, target)


def download_document(url):
    target = validate_public_target(url)
    request = Request(target, headers={'User-Agent': 'Mozilla/5.0',
                                      'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9'})
    try:
        with build_opener(ArticleRedirectHandler()).open(request, timeout=25) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {'text/html', 'application/xhtml+xml', 'application/json', 'text/plain'}:
                raise ValueError('链接返回的不是新闻网页或文章数据（可能是图片、视频或下载文件）。')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('网页超过5MB读取上限。')
            if response.headers.get('Content-Encoding', '').lower() == 'gzip':
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('网页解压后超过5MB读取上限。')
            # 国内老网页可能是GBK；结合响应头与HTML声明识别，不强行当UTF-8。
            charset = response.headers.get_content_charset()
            decoded = UnicodeDammit(raw, [charset] if charset else [], is_html=content_type != 'application/json')
            if decoded.unicode_markup is None:
                raise ValueError('无法识别网页文字编码。')
            return {'text': decoded.unicode_markup, 'url': response.url,
                    'content_type': content_type, 'encoding': decoded.original_encoding}
    except HTTPError as error:
        raise ValueError(f'新闻网页返回HTTP {error.code}，可能已删除、需要登录或限制程序访问。') from None
    except URLError:
        raise ValueError('新闻网页连接失败，请检查网址、网络、代理或证书环境。') from None