"""相同资源只下载一次；不同文件并行写不会出现半份内容。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError,URLError
import fetch_news
import io
from shared_io import atomic_write
from request_limits import limited_open


class SharedTests(unittest.TestCase):
    def test_same_article_eight_readers_only_one_download(self):
        with tempfile.TemporaryDirectory() as folder:
            url='https://www.dongqiudi.com/articles/12345'
            html='<h1 class="article-head__title">Fixture</h1><div class="article-body"><p>'+('Football news with reliable evidence. '*15)+'</p></div>'
            def download(url):
                time.sleep(.03)
                return dict(text=html,url=url,content_type='text/html',encoding='utf-8')
            with patch.object(fetch_news,'OUTPUT_DIR',Path(folder)),patch.object(fetch_news,'download_document',side_effect=download) as mocked:
                with ThreadPoolExecutor(8) as pool:results=list(pool.map(fetch_news.fetch_news,[url]*8))
            self.assertEqual(mocked.call_count,1)
            self.assertEqual(len({row['body'] for row in results}),1)
            self.assertEqual(sum(r['source_status']=='downloaded' for r in results),1)

    def test_atomic_replace_readers_never_observe_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.txt';a='A'*100000;b='B'*100000
            atomic_write(path,a)
            def write():
                for i in range(30):atomic_write(path,b if i%2 else a)
            with ThreadPoolExecutor(2) as pool:
                future=pool.submit(write)
                while not future.done():
                    try:self.assertIn(path.read_text(),(a,b))
                    except PermissionError:pass  # Windows改名瞬间可拒绝打开，但不能返回半份正文。
                future.result()
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_only_explicit_rate_limit_is_retried(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        error=HTTPError('https://example.test',429,'limited',{'Retry-After':'0'},io.BytesIO())
        with patch('request_limits.time.sleep'),patch('request_limits.NEXT',{'deepseek':0,'mcp':0}):
            opener=unittest.mock.Mock(side_effect=[error,Response()])
            with limited_open('deepseek',opener,None,90):pass
            self.assertEqual(opener.call_count,2)
            opener=unittest.mock.Mock(side_effect=URLError('ambiguous timeout'))
            with self.assertRaises(URLError):
                with limited_open('mcp',opener,None,45):pass
            self.assertEqual(opener.call_count,1)

    def test_model_and_search_each_respect_two_request_slots(self):
        from request_limits import SLOTS
        for provider in ('deepseek','mcp'):
            peak=[0];active=[0];lock=threading.Lock()
            def request(_):
                with SLOTS[provider]:
                    with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
                    time.sleep(.02)
                    with lock:active[0]-=1
            with ThreadPoolExecutor(8) as pool:list(pool.map(request,range(8)))
            self.assertEqual(peak[0],2)


if __name__=='__main__':unittest.main()
