"""Search and briefing regression tests: no internet or real API keys."""
import copy
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from news_search import CHINA,parse_time,parse_rss,select_candidates,direct_news_url
import news_briefing as briefing
import web_app
from project_paths import PROJECT_DIR

class SearchTests(unittest.TestCase):
    def test_known_bing_wrapper_unwrapped(self):
        url,aid=direct_news_url('http://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fwww.dongqiudi.com%2Farticles%2F123')
        self.assertEqual(aid,'123')
        self.assertEqual(url,'https://www.dongqiudi.com/articles/123')

    def test_private_result_rejected(self):
        with self.assertRaises(ValueError):direct_news_url('http://127.0.0.1/private')

    def test_rss_parses_source_date_and_plain_snippet(self):
        raw=b'<rss xmlns:n="http://www.bing.com/news"><channel><item><title>Match</title><link>https://example.net/news</link><pubDate>Sat, 17 Oct 2026 06:55:00 GMT</pubDate><description>&lt;p&gt;Snippet&lt;/p&gt;</description><n:Source>Fixture source</n:Source></item></channel></rss>'
        rows=parse_rss(raw)
        self.assertEqual(rows[0]['snippet'],'Snippet')
        self.assertEqual(rows[0]['search_source'],'Fixture source')
        self.assertTrue(rows[0]['search_reported_at'].startswith('2026-10-17'))

    def test_html_and_dtd_not_accepted_as_feed(self):
        for raw in (b'<html>blocked</html>',b'<!DOCTYPE test><rss/>',b'not XML'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_rss(raw)

    def test_iso_and_chinese_local_dates_have_timezones(self):
        for raw in ('2026-10-17T09:30:00Z','published 2026-10-17 17:30','2026年10月17日 17:30'):
            value,_=parse_time(raw);self.assertIsNotNone(value.tzinfo)
        self.assertIsNone(parse_time('yesterday')[0])
        self.assertEqual(parse_time('2026-10-17')[1],'day')

    def test_dedupe_and_exclude_old_unknown_or_future(self):
        now=datetime.now(CHINA);start=now-timedelta(days=1)
        def row(key,hours):return {'article_id':key,'url':'https://example.net/'+key,'title':'Fixture '+key,'search_reported_at':(now-timedelta(hours=hours)).isoformat()}
        good=row('1',1)
        results=[{'articles':[good,dict(good),row('2',25),row('3',-1),{**row('4',1),'search_reported_at':None}]}]
        kept,excluded,unprocessed=select_candidates(results,start,now)
        self.assertEqual([r['article_id'] for r in kept],['1']);self.assertEqual(len(excluded),3)
        self.assertEqual(unprocessed,0)

    def test_eight_result_cap_counts_unprocessed(self):
        now=datetime.now(CHINA)
        rows=[{'article_id':str(i),'url':'https://example.net/'+str(i),'title':'Fixture','search_reported_at':(now-timedelta(minutes=i)).isoformat()} for i in range(12)]
        kept,_,extra=select_candidates([{'articles':rows}],now-timedelta(days=1),now)
        self.assertEqual(len(kept),8);self.assertEqual(extra,4)


class BriefingTests(unittest.TestCase):
    def setUp(self):
        overview_patch=patch.object(briefing,'generate_overview',return_value=({'status':'completed','paragraphs':[],'note':'Fixture'},{}))
        overview_patch.start();self.addCleanup(overview_patch.stop)
        provider_patch=patch.object(briefing,'load_mcp_settings',return_value={'provider':'bing'})
        provider_patch.start();self.addCleanup(provider_patch.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='briefing_test_',dir=str(PROJECT_DIR))
        self.root=Path(self.temp.name).resolve();self.assertEqual(self.root.parent,PROJECT_DIR.resolve())
        self.addCleanup(self.temp.cleanup)
        now=datetime.now(CHINA)
        self.candidates=[{'article_id':str(i),'url':'https://www.dongqiudi.com/articles/'+str(i),
             'title':'Fixture match '+str(i),'snippet':'A search snippet about the match.',
             'search_source':'Fixture source','search_reported_at':(now-timedelta(hours=i)).isoformat(),'date_precision':'second'} for i in (1,2)]
        self.plan={'team':'Fixture FC','hours':24,'aliases':['Fixture FC'],'queries':['Fixture FC']}
        self.body='A late goal decided the match. The team won the game.'
        self.calls=[]
        for field,value in [('search_news',self.search),('fetch_news',self.fetch),('call_deepseek',self.model)]:
            obj=patch.object(briefing,field,side_effect=value);obj.start();self.addCleanup(obj.stop)

    def search(self,query):return {'query':query,'provider':'Fixture search','articles':copy.deepcopy(self.candidates)}
    def fetch(self,url):
        return {'article_id':url.rsplit('/',1)[-1],'title':'Fixture match','body':self.body,
                'published_at':self.candidates[0]['search_reported_at'],'source_status':'downloaded',
                'extraction_method':'site:fixture','url':url,'platform':'Fixture source'}
    def model(self,material,key,model,system_prompt,max_tokens):
        self.calls.append(system_prompt)
        if system_prompt==briefing.PLAN_PROMPT:return copy.deepcopy(self.plan),{'total_tokens':5}
        inputs=json.loads(material)['articles']
        return {'items':[{'article_id':row['article_id'],'relevant':True,'category':'match',
             'summary':'The report describes a late goal.','evidence_ids':[row['body_segments'][0]['id']]} for row in inputs]},{'total_tokens':10}
    def run_brief(self):return briefing.run_briefing('Recent Fixture FC news','fake','fake',output_dir=self.root,progress=lambda _:None)

    def test_end_to_end_wide_categories_without_archive(self):
        record,folder=self.run_brief()
        self.assertEqual(record['status'],'completed')
        self.assertEqual(record['requests'],{'planning':1,'search':1,'organizing':1,'overview':1})
        self.assertEqual(len(record['articles']),2)
        self.assertTrue(all(r['category']=='match' and r['evidence'] for r in record['articles']))
        self.assertFalse((self.root/'data'/'events.json').exists())
        self.assertEqual(json.loads((folder/'trace.json').read_text(encoding='utf-8'))['type'],'briefing')
        self.assertTrue((folder/'report.md').is_file())

    def test_old_actual_page_date_not_hidden_by_fresh_search_date(self):
        original=self.fetch
        def old(url):return {**original(url),'published_at':(datetime.now(CHINA)-timedelta(days=10)).isoformat()}
        with patch.object(briefing,'fetch_news',side_effect=old):record,_=self.run_brief()
        self.assertFalse(record['articles']);self.assertEqual(len(record['excluded']),2)
        self.assertEqual(record['requests']['organizing'],0)

    def test_failed_body_keeps_explicit_snippet_only_cards(self):
        with patch.object(briefing,'fetch_news',side_effect=URLError('fixture')):record,_=self.run_brief()
        self.assertEqual(record['status'],'partial')
        self.assertTrue(all(r['read_status']=='snippet_only' for r in record['articles']))
        self.assertTrue(all('search_reported_at' in r for r in record['articles']))
        self.assertTrue(all(r['evidence'][0] in r['snippet'] for r in record['articles']))

    def test_unknown_page_date_marked_as_search_date(self):
        original=self.fetch
        with patch.object(briefing,'fetch_news',side_effect=lambda url:{**original(url),'published_at':'Unknown'}):record,_=self.run_brief()
        self.assertTrue(all(r['time_basis'].startswith('搜索') for r in record['articles']))

    def test_no_matches_does_not_expand_window_or_invent_news(self):
        self.candidates=[];record,_=self.run_brief()
        self.assertEqual(record['articles'],[])
        self.assertEqual(record['window']['hours'],24)
        self.assertEqual(record['requests']['organizing'],0)

    def test_all_searches_fail_with_saved_failed_record(self):
        with patch.object(briefing,'search_news',side_effect=URLError('fixture')),self.assertRaises(ValueError):self.run_brief()
        record=json.loads(next(self.root.glob('briefing_*/trace.json')).read_text(encoding='utf-8'))
        self.assertEqual(record['status'],'failed')
        self.assertEqual(record['articles'],[])

    def test_model_error_keeps_links_without_fabricated_summary(self):
        original=self.model
        def model(*args,**kwargs):
            if kwargs['system_prompt']==briefing.ORGANIZE_PROMPT:raise ValueError('Fixture invalid JSON')
            return original(*args,**kwargs)
        with patch.object(briefing,'call_deepseek',side_effect=model):record,_=self.run_brief()
        self.assertEqual(record['status'],'partial')
        self.assertEqual(len(record['articles']),2)
        self.assertTrue(all(r['analysis_status']=='needs_review' for r in record['articles']))

    def test_foreign_evidence_id_does_not_block_other_article(self):
        original=self.model
        def model(*args,**kwargs):
            result,usage=original(*args,**kwargs)
            if kwargs['system_prompt']==briefing.ORGANIZE_PROMPT:result['items'][0]['evidence_ids']=['N2_0001']
            return result,usage
        with patch.object(briefing,'call_deepseek',side_effect=model):record,_=self.run_brief()
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['articles'][0]['analysis_status'],'needs_review')
        self.assertEqual(record['articles'][1]['analysis_status'],'completed')

    def test_irrelevant_candidate_filtered(self):
        original=self.model
        def model(*args,**kwargs):
            result,usage=original(*args,**kwargs)
            if kwargs['system_prompt']==briefing.ORGANIZE_PROMPT:result['items'][0]['relevant']=False
            return result,usage
        with patch.object(briefing,'call_deepseek',side_effect=model):record,_=self.run_brief()
        self.assertEqual(len(record['articles']),1)
        self.assertTrue(record['excluded'])

    def test_invalid_plan_stops_before_search(self):
        self.plan['hours']=999
        with self.assertRaises(ValueError):self.run_brief()
        record=json.loads(next(self.root.glob('briefing_*/trace.json')).read_text(encoding='utf-8'))
        self.assertEqual(record['requests']['search'],0)

    def test_planning_search_organizing_limits(self):
        self.plan['queries']=['Fixture FC','Fixture FC match','Fixture FC team']
        now=datetime.now(CHINA)
        self.candidates=[{**self.candidates[0],'article_id':str(i),'url':'https://www.dongqiudi.com/articles/'+str(i),
            'search_reported_at':(now-timedelta(minutes=i+1)).isoformat()} for i in range(12)]
        record,_=self.run_brief()
        self.assertEqual(record['requests'],{'planning':1,'search':3,'organizing':1,'overview':1})
        self.assertEqual(len(record['articles']),8)
        self.assertEqual(record['unprocessed_candidates'],4)
        self.assertEqual(record['status'],'completed')

    def test_public_briefing_record_omits_source_body_and_preserves_metadata(self):
        record,_=self.run_brief();public=web_app.public_record(record)
        self.assertEqual(public['type'],'briefing')
        self.assertIn('window',public)
        self.assertNotIn('_material',public['articles'][0])
        self.assertNotIn('source_article',public['articles'][0])

if __name__=='__main__':unittest.main()