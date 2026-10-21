"""战术板图片通道：只读取 FotMob 的数字编号头像/队徽。

浏览器把跨网站照片直接画入 canvas 后，可能无法导出图片。这个固定来源
通道让页面和照片来自同一个本地服务，便于将真实照片一起保存进 JPG。
不接受任意网址，不使用模型，不读取个人报告。
"""
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from project_paths import OUTPUT_DIR
from shared_io import resource_lock, atomic_write

CACHE_DIR = OUTPUT_DIR / 'tactics_image_cache'
LOCK = threading.Lock()
SLOTS = threading.BoundedSemaphore(4)
LIMIT = 2000000


def raster_type(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':return 'image/webp'
    raise ValueError('图片来源没有返回可用的照片。')


class ImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urlsplit(newurl)
        if p.scheme != 'https' or p.hostname != 'images.fotmob.com':
            raise ValueError('图片跳转地址不受支持。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_photo(kind, image_id):
    # 先校验参数，再按图片编号合并同时到来的下载。
    if kind not in ('player','team') or not isinstance(image_id,str) or not re.fullmatch(r'[0-9]{1,8}',image_id):
        raise ValueError('图片参数无效。')
    with resource_lock(('photo',kind,image_id)):
        return _load_photo_locked(kind,image_id)


def _load_photo_locked(kind, image_id):
    if kind not in ('player','team') or not isinstance(image_id,str) or not re.fullmatch(r'[0-9]{1,8}',image_id):
        raise ValueError('图片参数无效。')
    path = CACHE_DIR / (kind + '_' + image_id + '.bin')
    try:
        if time.time() - path.stat().st_mtime < 7 * 86400:
            raw = path.read_bytes()
            if len(raw) <= LIMIT:return raw,raster_type(raw)
    except (OSError,ValueError):pass
    folder = 'playerimages/' if kind == 'player' else 'logo/teamlogo/'
    url = 'https://images.fotmob.com/image_resources/' + folder + image_id + '.png'
    try:
        # 限制外部图片并发；图片加载失败时，前端会保留姓名占位。
        with SLOTS:
            with build_opener(ImageRedirect()).open(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=12) as response:
                raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:raise ValueError('图片文件过大。')
        mime = raster_type(raw)
        atomic_write(path,raw)
        return raw,mime
    except (OSError,ValueError):
        raise ValueError('这张照片暂时无法读取。') from None
