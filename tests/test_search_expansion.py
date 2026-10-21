"""Expanded search budgets, independent batches and report-title regressions."""
import copy
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import news_briefing as briefing
from news_search import CHINA
from report_titles import report_title
from project_paths import PROJECT_DIR

class ExpansionTests(unittest.TestCase):
    def setUp(self):
        overview_patch=patch.object(briefing,'generate_overview',return_value=({'status':'completed','paragraphs':[],'note':'Fixture'},{}))
        overview_patch.start();self.addCleanup(overview_patch.stop)
        provider_patch=patch.object(briefing,'load_mcp_settings',return_value={'provider':'bing'})
        provider_patch.start();self.addCleanup(provider_patch.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='expansion_test_',dir=str(PROJECT_DIR))
        self.root=Path(self.temp.name).resolve();self.assertEqual(self.root.parent,PROJECT_DIR.resolve())
        self.addCleanup(self.temp.cleanup)
        self.plan={'team':'Fixture FC','hours':72,'aliases':['Fixture FC','Fixture'],'queries':['Fixture FC','Fixture match'],'title':'Fixture weekly news'}
        now=datetime.now(CHINA)
        self.candidates=[{'article_id':str(i),'url':'https://www.dongqiudi.com/articles/'+str(i),
            'title':'Fixture match '+str(i),'snippet':'Fixture search snippet','search_source':'Fixture',
            'search_reported_at':(now-timedelta(minutes=i+1)).isoformat(),'date_precision':'second'} for i in range(24)]
        self.batches=[];self.body='The report describes this team and a late goal.'
        self.organize_count=0;self.fail_batch=None
        def search(query):return {'query':query,'provider':'Fixture search','articles':copy.deepcopy(self.candidates)}
        def fetch(url):return {'title':'Fixture report','body':self.body,'published_at':self.candidates[0]['search_reported_at'],
            'source_status':'downloaded','extraction_method':'site:fixture','url':url}
        def model(material,key,model,system_prompt,max_tokens):
            if system_prompt==briefing.PLAN_PROMPT:return copy.deepcopy(self.plan),{}
            self.organize_count+=1
            rows=json.loads(material)['articles'];self.batches.append([r['article_id'] for r in rows])
            if self.fail_batch==self.organize_count:raise ValueError('Fixture failed batch')
            return {'items':[{'article_id':r['article_id'],'relevant':True,'category':'match',
                'summary':'A match report about the team.','evidence_ids':[r['body_segments'][0]['id']]} for r in rows]},{}
        for name,value in [('search_news',search),('fetch_news',fetch),('call_deepseek',model)]:
            item=patch.object(briefing,name,side_effect=value);item.start();self.addCleanup(item.stop)

    def run_brief(self,limit=16,depth='expanded'):
        return briefing.run_briefing('Team news','fake','fake',output_dir=self.root,progress=lambda _:None,
                                      max_articles=limit,search_depth=depth)

    def test_six_query_budget_deduplicates_aliases(self):
        queries=briefing.build_queries(self.plan,'expanded')
        self.assertLessEqual(len(queries),6);self.assertGreater(len(queries),2)
        self.assertEqual(len(queries),len(set(queries)))
        self.assertEqual(briefing.build_queries(self.plan,'standard'),self.plan['queries'])

    def test_sixteen_articles_are_two_independent_batches(self):
        r,_=self.run_brief()
        self.assertEqual(len(r['articles']),16)
        self.assertEqual([len(batch) for batch in self.batches],[8,8])
        self.assertEqual(len(set(self.batches[0])&set(self.batches[1])),0)
        self.assertEqual(r['requests']['organizing'],2)
        self.assertEqual(r['settings']['max_model_requests'],4)
        self.assertEqual(r['unprocessed_candidates'],8)
        self.assertIn('16',r['warnings'][0])

    def test_twenty_four_articles_use_at_most_four_model_requests(self):
        r,_=self.run_brief(24)
        self.assertEqual(len(r['articles']),24)
        self.assertEqual([len(batch) for batch in self.batches],[8,8,8])
        self.assertEqual(r['requests']['planning']+r['requests']['organizing'],4)
        self.assertEqual(r['unprocessed_candidates'],0)

    def test_later_batch_failure_preserves_earlier_summaries(self):
        self.fail_batch=2
        r,_=self.run_brief()
        self.assertEqual(r['status'],'partial')
        self.assertEqual(sum(a['analysis_status']=='completed' for a in r['articles']),8)
        self.assertEqual(sum(a['analysis_status']=='needs_review' for a in r['articles']),8)
        self.assertTrue(all(a['evidence'] for a in r['articles'][:8]))

    def test_few_candidates_do_not_pay_for_empty_batches(self):
        self.candidates=self.candidates[:3]
        r,_=self.run_brief(24)
        self.assertEqual(r['requests']['organizing'],1)
        self.assertEqual(len(r['articles']),3)

    def test_invalid_budgets_rejected_before_output(self):
        for limit,depth in [(32,'standard'),(True,'expanded'),(16,'unknown')]:
            with self.subTest(limit=limit,depth=depth),self.assertRaises(ValueError):self.run_brief(limit,depth)
        self.assertFalse(list(self.root.glob('briefing_*')))

    def test_model_title_reused_without_additional_call(self):
        r,_=self.run_brief(8,'standard')
        self.assertEqual(r['title'],self.plan['title'])
        self.assertEqual(r['requests']['planning']+r['requests']['organizing'],2)

class TitleTests(unittest.TestCase):
    def test_existing_briefing_fallback_uses_team_period_and_question(self):
        r={'type':'briefing','question':'看看马竞最近三天伤病情况','plan':{'team':'马德里竞技','hours':72}}
        self.assertEqual(report_title(r),'马竞近三天伤病动态')

    def test_existing_import_uses_event_title(self):
        self.assertEqual(report_title({'articles':[{'event':{'title':'赛后采访整理'}}]}),'赛后采访整理')

    def test_generated_titles_are_short_and_single_line(self):
        title=report_title({'title':'A very long title '*10+'\nnew line'})
        self.assertLessEqual(len(title),26);self.assertNotIn('\n',title)

    def test_empty_import_title_still_readable(self):
        self.assertEqual(report_title({'articles':[]}),'新闻链接导入')

if __name__=='__main__':unittest.main()