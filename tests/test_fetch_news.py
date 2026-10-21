"""多来源获取与兼容性离线测试；模拟网页，不使用模型密钥或真实网络。"""
import json
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import fetch_news as fetch
import news_urls
from news_http import ArticleRedirectHandler
from news_extract import extract_html_article
from news_storage import read_article
from project_paths import PROJECT_DIR

BODY = 'This is a detailed football report with evidence and uncertainty. ' * 4


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fetch_test_', dir=str(PROJECT_DIR))
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, PROJECT_DIR.resolve())
        self.output_patch = patch.object(fetch, 'OUTPUT_DIR', self.root)
        self.output_patch.start()

    def tearDown(self):
        self.output_patch.stop()
        assert self.root.parent == PROJECT_DIR.resolve()
        self.temp.cleanup()

    def html(self):
        return '<h1 class="article-head__title">Fixture title</h1><p class="article-head__author-name">Fixture author</p><p class="article-head__author-sub">2026-10-17</p><div class="article-body"><p>' + BODY + '</p></div>'

    def document(self, url, html=None):
        return {'text': self.html() if html is None else html, 'url':url, 'content_type':'text/html', 'encoding':'utf-8'}

    def test_old_dqd_url_and_numeric_id_preserved(self):
        url, article_id = fetch.normalize_news_url('https://dongqiudi.com/articles/12345.html/?from=share#top')
        self.assertEqual(url, 'https://www.dongqiudi.com/articles/12345')
        self.assertEqual(article_id, '12345')

    def test_unregistered_site_allowed_without_enumeration(self):
        url, article_id = fetch.normalize_news_url('https://sports.example.net/story?id=42&utm_source=test#top')
        self.assertEqual(url, 'https://sports.example.net/story?id=42')
        self.assertRegex(article_id, r'^web_[0-9a-f]{24}$')
        self.assertNotEqual(article_id, fetch.normalize_news_url('https://sports.example.net/story?id=43')[1])

    def test_tracking_and_msn_title_variants_deduplicate(self):
        first = fetch.normalize_news_url('https://www.msn.cn/zh-cn/news/other/title-one/ar-AA2e9akA?ocid=x&cvid=y&ei=9')
        second = fetch.normalize_news_url('https://www.msn.cn/zh-cn/news/other/title-two/ar-AA2e9akA')
        self.assertEqual(first, second)
        a = fetch.normalize_news_url('https://cj.sina.com.cn/articles/view/1/abc?from=sports&subch=global')
        b = fetch.normalize_news_url('https://cj.sina.com.cn/articles/view/1/abc')
        self.assertEqual(a,b)

    def test_reject_private_or_credential_urls(self):
        for url in ['http://127.0.0.1/a','http://192.168.1.1/news','http://localhost/a',
                    'file:///E:/Agent/local_config.ini','https://user:pass@example.com/a',
                    'https://example.com:444/a',123]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch.normalize_news_url(url)

    def test_dns_private_target_rejected(self):
        answers = [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with patch.object(news_urls.socket,'getaddrinfo',return_value=answers), self.assertRaises(ValueError):
            news_urls.validate_public_target('https://example.com/news')

    def test_redirect_checked_before_following(self):
        with self.assertRaises(ValueError):
            ArticleRedirectHandler().redirect_request(None,None,302,'',{},'http://127.0.0.1/private')

    def test_download_save_then_cache_without_network(self):
        url='https://www.dongqiudi.com/articles/12345'
        with patch.object(fetch,'download_document',return_value=self.document(url)) as download:
            first = fetch.fetch_news(url)
            original = (self.root/'12345.txt').read_bytes()
            second = fetch.fetch_news(url+'.html?from=share')
        self.assertEqual(download.call_count,1)
        self.assertEqual(first['source_status'],'downloaded')
        self.assertEqual(second['source_status'],'local_cache')
        self.assertIsNone(second['fetched_at_utc'])
        self.assertEqual((self.root/'12345.txt').read_bytes(),original)
        self.assertEqual(first['body'],second['body'])
        self.assertEqual(first['extraction_method'],second['extraction_method'])
        self.assertEqual(first['fetched_at_utc'],second['cached_fetched_at_utc'])
        self.assertTrue((self.root/'12345.meta.json').exists())


    def test_generic_article_can_be_saved_and_read(self):
        url='https://sports.example.net/story?id=42'
        html='<h1>Generic report</h1><article><p>'+BODY+'</p><aside>Unrelated recommendation</aside></article>'
        with patch.object(fetch,'download_document',return_value=self.document(url,html)):
            article=fetch.fetch_news(url)
        self.assertNotIn('Unrelated recommendation',article['body'])
        saved=read_article(self.root/(article['article_id']+'.txt'))
        self.assertEqual(saved['body'],article['body'])

    def test_json_ld_standard_article_body(self):
        data={'@type':'NewsArticle','headline':'JSON-LD title','articleBody':BODY,'datePublished':'2026-10-09T01:00:00Z','author':{'name':'Fixture author'}}
        html='<script type="application/ld+json">'+json.dumps(data)+'</script>'
        result=extract_html_article(html,'https://another.example.net/story')
        self.assertEqual(result['extraction_method'],'standard:json-ld')
        self.assertEqual(result['published_at'],data['datePublished'])

    def test_generic_density_fallback_labeled_for_review(self):
        html='<h1>Generic report</h1><div class="unknown-layout"><p>'+BODY+'</p><p>'+BODY+'</p></div>'
        result=extract_html_article(html,'https://another.example.net/story')
        self.assertEqual(result['extraction_method'],'generic:heuristic-review-needed')

    def test_msn_shell_uses_matching_public_content(self):
        url='https://www.msn.cn/zh-cn/news/other/ar-AA2e9akA'
        data={'id':'AA2e9akA','type':'article','renderingRestriction':0,'title':'MSN fixture title',
              'body':'<p>'+BODY+'</p>','publishedDateTime':'2026-10-13T15:25:13Z',
              'provider':{'name':'Fixture provider'},'authors':[{'name':'Fixture author'}],
              'sourceHref':'https://publisher.example.net/story'}
        with patch.object(fetch,'download_document',side_effect=[self.document(url,'<title>MSN</title>'),
             {'text':json.dumps(data),'url':'https://assets.msn.cn/content/view/v2/Detail/zh-cn/AA2e9akA'}]) as download:
            result=fetch.fetch_news(url)
        self.assertEqual(download.call_count,2)
        self.assertEqual(result['provider'],'Fixture provider')
        self.assertEqual(result['platform'],'MSN')
        self.assertEqual(result['published_at'],data['publishedDateTime'])
        self.assertTrue((self.root/(result['article_id']+'.content.json')).exists())

    def test_msn_wrong_article_id_not_saved(self):
        url='https://www.msn.cn/zh-cn/news/other/ar-AA2e9akA'
        with patch.object(fetch,'download_document',side_effect=[self.document(url,'<title>MSN</title>'),
             {'text':json.dumps({'id':'WRONG','body':BODY}),'url':'https://assets.msn.cn/content/test'}]), self.assertRaises(ValueError):
            fetch.fetch_news(url)
        self.assertFalse(list(self.root.iterdir()))

    def test_invalid_page_not_saved_as_article(self):
        url='https://public.example.net/news'
        with patch.object(fetch,'download_document',return_value=self.document(url,'<title>Login</title><p>Please sign in</p>')):
            with self.assertRaises(ValueError):
                fetch.fetch_news(url)
        self.assertFalse(list(self.root.iterdir()))

    def test_long_body_not_silently_truncated_or_saved(self):
        url='https://public.example.net/news'
        html='<h1>Long news report</h1><article><p>'+BODY*100+'</p></article>'
        with patch.object(fetch,'download_document',return_value=self.document(url,html)), self.assertRaises(ValueError):
            fetch.fetch_news(url)
        self.assertFalse(list(self.root.iterdir()))

    def test_network_failure_not_saved(self):
        with patch.object(fetch,'download_document',side_effect=ValueError('News HTTP 403')):
            with self.assertRaises(ValueError):
                fetch.fetch_news('https://public.example.net/news')
        self.assertFalse(list(self.root.iterdir()))

    def test_bad_existing_cache_not_overwritten(self):
        path=self.root/'12345.txt'
        path.write_text('invalid cache',encoding='utf-8')
        with patch.object(fetch,'download_document') as download, self.assertRaises(ValueError):
            fetch.fetch_news('https://www.dongqiudi.com/articles/12345')
        download.assert_not_called()
        self.assertEqual(path.read_text(encoding='utf-8'),'invalid cache')





if __name__=='__main__':
    unittest.main()