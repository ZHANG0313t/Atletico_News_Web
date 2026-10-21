"""综述的依据范围、日期顺序、失败隔离与自动生成检查；不调用真实 API。"""
import copy
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import news_overview as overview
import news_briefing as briefing
from news_search import CHINA
from project_paths import PROJECT_DIR


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(CHINA)
        self.rows=[{'article_id':str(i),'title':'Match report','published_at':(self.now-timedelta(hours=i)).isoformat(),
            'analysis_status':'completed','relevant':True,'read_status':'body','analysis_scope':'正文',
            'summary':'The team won a match.','evidence':['The team won a match.'],'category':'match'} for i in (1,2)]
        self.record={'question':'team news','articles':self.rows}

    def test_materials_sorted_by_publication_and_exclude_failed_irrelevant(self):
        self.rows += [{**self.rows[0],'article_id':'bad','analysis_status':'needs_review'},
                      {**self.rows[0],'article_id':'unrelated','relevant':False},
                      {**self.rows[0],'article_id':'no_evidence','evidence':[]}]
        with patch.object(overview,'call_deepseek',return_value=({'paragraphs':[{'text':'球队赢得比赛，多篇报道聚焦这场比赛。','article_ids':['2','1']}]},{})) as model:
            result,_=overview.generate_overview(self.record,'fake','fake')
        material=json.loads(model.call_args.args[0])['news_oldest_first']
        self.assertEqual([r['article_id'] for r in material],['2','1'])
        self.assertEqual(result['source_count'],2)
        self.assertIn('未通过',result['note'])

    def test_unknown_source_id_and_oversized_text_rejected(self):
        for paragraphs in ([{'text':'Invented','article_ids':['not_in_batch']}],
                [{'text':'x'*301,'article_ids':['1']}],[],[{'text':'text','article_ids':[]}],
                [{'text':'x'*250,'article_ids':['1']}]*3):
            with self.subTest(paragraphs=paragraphs),patch.object(overview,'call_deepseek',return_value=({'paragraphs':paragraphs},{})),self.assertRaises(ValueError):
                overview.generate_overview(self.record,'fake','fake')

    def test_search_only_material_keeps_scope_and_note(self):
        self.rows[0].update(read_status='snippet_only',analysis_scope='仅搜索标题')
        with patch.object(overview,'call_deepseek',return_value=({'paragraphs':[{'text':'一个视频标题提到比赛。','article_ids':['1']}]},{})) as model:
            result,_=overview.generate_overview(self.record,'fake','fake')
        inputs=json.loads(model.call_args.args[0])['news_oldest_first']
        self.assertEqual(inputs[1]['scope'],'仅搜索标题')
        self.assertIn('未读取全文或识别视频',result['note'])

    def test_no_usable_news_does_not_call_model(self):
        self.record['articles']=[]
        with patch.object(overview,'call_deepseek') as model:
            result,usage=overview.generate_overview(self.record,'fake','fake')
        self.assertEqual(result['status'],'empty');self.assertIsNone(usage);model.assert_not_called()

    def test_fused_result_does_not_mutate_original_articles(self):
        original=copy.deepcopy(self.record)
        with patch.object(overview,'call_deepseek',return_value=({'paragraphs':[{'text':'球队赢得比赛。','article_ids':['1','1','2']}]},{})):
            result,_=overview.generate_overview(self.record,'fake','fake')
        self.assertEqual(result['paragraphs'][0]['article_ids'],['1','2'])
        self.assertEqual(self.record,original)

    def test_complete_workflow_generates_once_and_failure_preserves_news(self):
        plan={'team':'Fixture','hours':24,'queries':['Fixture'],'aliases':['Fixture']}
        candidate={'article_id':'1','title':'Match','url':'https://www.dongqiudi.com/articles/1',
            'snippet':'The team won a match.','search_reported_at':(self.now-timedelta(hours=1)).isoformat(),'search_source':'Fixture'}
        article={'title':'Match','body':'The team won a match.','published_at':candidate['search_reported_at'],
                 'source_status':'downloaded','extraction_method':'site:fixture'}
        def organize(rows,*args):
            for row in rows:row.update(analysis_status='completed',summary='The team won a match.',evidence=['The team won a match.'])
            return {}
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory(dir=str(PROJECT_DIR)) as folder, \
                    patch.object(briefing,'load_mcp_settings',return_value={'provider':'bing'}), \
                    patch.object(briefing,'plan_request',return_value=(plan,{})), \
                    patch.object(briefing,'search_news',return_value={'query':'Fixture','provider':'Fixture','articles':[candidate]}), \
                    patch.object(briefing,'fetch_news',return_value=article),patch.object(briefing,'organize',side_effect=organize), \
                    patch.object(overview,'call_deepseek',side_effect=ValueError('fixture') if fail else None,
                        return_value=({'paragraphs':[{'text':'这批新闻围绕球队获胜展开。','article_ids':['1']}]},{'total_tokens':20})) as model:
                record,path=briefing.run_briefing('news','fake','fake',output_dir=Path(folder),progress=lambda _:None)
                self.assertEqual(len(record['articles']),1)
                self.assertEqual(model.call_count,1)
                self.assertEqual(record['requests']['overview'],1)
                self.assertEqual(record['overview']['status'],'failed' if fail else 'completed')
                self.assertEqual(record['status'],'partial' if fail else 'completed')
                self.assertIn('本次新闻综述',(path/'report.md').read_text(encoding='utf-8'))
                self.assertEqual(json.loads((path/'trace.json').read_text(encoding='utf-8'))['overview'],record['overview'])


if __name__=='__main__':unittest.main()
