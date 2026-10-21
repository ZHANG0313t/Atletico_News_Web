"""新浪页面范围与编号证据的回归检查，不联网、不读取真实档案。"""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import event_archive as archive
import event_updates as updates
from evidence_segments import segment_article
from news_extract import extract_html_article
from news_storage import format_article
from project_paths import PROJECT_DIR
fetch=importlib.import_module('fetch_news')

BODY='The match ended with a late goal. The scorer was praised by the reporter.'


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='repair_test_',dir=str(PROJECT_DIR))
        self.root=Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,PROJECT_DIR.resolve())
        self.addCleanup(self.temp.cleanup)
        for target,field,value in [(archive,'EVENT_PATH',self.root/'data'/'events.json'),(fetch,'OUTPUT_DIR',self.root)]:
            item=patch.object(target,field,value); item.start(); self.addCleanup(item.stop)
        for key in ('1','2','3'):
            (self.root/(key+'.txt')).write_text(format_article({'title':'Match '+key,'body':BODY,
                'url':'https://www.dongqiudi.com/articles/'+key}),encoding='utf-8')
        self.event_id=archive.create_event('Specific match','What happened in this match?','match')
        for key in ('1','2'): archive.move_article(key,self.event_id,'Fixture',self.root)

    def html(self,body=BODY):
        return ('<h1 class="channel-logo">新浪看点</h1><h1 class="main-title">具体比赛标题</h1>'
                '<div class="article-content"><div id="article" class="article"><p>'+body+
                '</p><div class="article-notice">特别声明：平台免责声明</div></div>'
                '<p>推荐：其他无关新闻一</p><p>推荐：其他无关新闻二</p><footer>联系我们</footer></div>')

    def test_sina_main_heading_and_exact_article_container(self):
        article=extract_html_article(self.html(),'https://k.sina.com.cn/article_fixture.html')
        self.assertEqual(article['title'],'具体比赛标题')
        self.assertEqual(article['body'],BODY)
        self.assertEqual(article['extraction_method'],'site:sina-article-v2')

    def test_short_real_sina_body_is_not_padded_by_recommendations(self):
        body='这是具体比赛赛后球员发言的报道，交代了双方对手和比赛结果，并引用了球员公开发表的短句。'
        article=extract_html_article(self.html(body),'https://k.sina.com.cn/article_fixture.html')
        self.assertEqual(article['body'],body)
        self.assertLess(len(body),80)

    def test_unknown_site_short_body_still_rejected(self):
        with self.assertRaises(ValueError):
            extract_html_article('<h1>Article</h1><article><p>Too short.</p></article>','https://example.net/story')

    def test_ai_generation_label_remains_in_body(self):
        article=extract_html_article(self.html(BODY+' 本文由AI生成'),'https://k.sina.com.cn/article_fixture.html')
        self.assertIn('本文由AI生成',article['body'])

    def test_old_cache_reparsed_offline_with_backup_once(self):
        url='https://k.sina.com.cn/article_fixture.html'
        _,key=fetch.normalize_news_url(url)
        old=format_article({'title':'新浪看点','body':BODY+' UNRELATED OLD FOOTER','url':url})
        path=self.root/(key+'.txt'); path.write_text(old,encoding='utf-8')
        path.with_suffix('.html').write_text(self.html(),encoding='utf-8')
        with patch.object(fetch,'download_document') as network:
            result=fetch.fetch_news(url)
            self.assertTrue(result['reextracted_from_saved_html'])
            again=fetch.fetch_news(url)
        network.assert_not_called()
        self.assertEqual(result['source_status'],'local_cache')
        self.assertIsNone(result['fetched_at_utc'])
        self.assertEqual(result['body'],BODY)
        self.assertFalse(again['reextracted_from_saved_html'])
        self.assertEqual(path.with_suffix('.before-sina-v2.txt').read_text(encoding='utf-8'),old)

    def test_segments_are_short_and_exact_without_truncation(self):
        body='开头。'+('长句'*180)+'！末尾还有一句。'
        segmented,catalog=segment_article({'body':body,'title':'Fixture'},'T')
        self.assertEqual(''.join(catalog.values()),body)
        self.assertTrue(all(len(value)<=140 for value in catalog.values()))
        self.assertNotIn('body',segmented)
        self.assertTrue(all(value in body for value in catalog.values()))

    def proposal(self,evidence):
        return {'decision':'existing','event_id':self.event_id,'event_title':None,'event_question':None,
                'event_type':None,'reason':'Same match.','evidence':evidence}

    def test_classification_resolves_id_before_strict_validation(self):
        with patch.object(archive,'call_deepseek',return_value=(self.proposal('T0001'),{})):
            result=archive.classify_article('3','fake','fake',self.root)
        self.assertEqual(result['assignment']['evidence'],BODY)

    def test_unknown_evidence_id_is_rejected_without_assignment(self):
        with patch.object(archive,'call_deepseek',return_value=(self.proposal('T9999'),{})), self.assertRaises(ValueError):
            archive.classify_article('3','fake','fake',self.root)
        self.assertNotIn('3',archive.load_registry()['assignments'])

    def comparison(self,quote='B1_0001'):
        return {'comparable':True,'reason':'Same match.','overlap':[
            {'statement':'Shared report.','evidence_target':'T0001','evidence_baseline':[{'article_id':'2','quote':quote}]}],
            'added_in_target':[],'differences':[]}

    def test_comparison_resolves_correct_source_ids_and_retains_original_inputs(self):
        with patch.object(updates,'call_deepseek',return_value=(self.comparison(),{})) as model:
            result=updates.compare_event_update('1','fake','fake',self.root)
        self.assertEqual(result['comparison']['overlap'][0]['evidence_target'],BODY)
        self.assertEqual(result['comparison']['overlap'][0]['evidence_baseline'][0]['quote'],BODY)
        self.assertEqual(result['source_articles']['target']['body'],BODY)
        self.assertIn('body_segments',json.loads(model.call_args.args[0])['target'])

    def test_target_id_cannot_be_used_for_baseline_or_fake_literal(self):
        for quote in ('T0001','B2_0001','The match had a different result.'):
            with self.subTest(quote=quote), patch.object(updates,'call_deepseek',return_value=(self.comparison(quote),{})), self.assertRaises(ValueError):
                updates.compare_event_update('1','fake','fake',self.root)


if __name__=='__main__': unittest.main()
