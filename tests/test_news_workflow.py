"""完整流程的离线端到端检查：复用真实读缓存/归档/比较逻辑，只模拟LLM与失败网络。"""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
import event_archive as archive
import event_updates as updates
import news_workflow as workflow
from news_storage import format_article
from project_paths import PROJECT_DIR

fetch_module=importlib.import_module('fetch_news')
BODY='The player is considering a new representative. No transfer agreement is confirmed.'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='workflow_test_',dir=str(PROJECT_DIR))
        self.root=Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,PROJECT_DIR.resolve())
        self.addCleanup(self.temp.cleanup)
        self.output=self.root/'output'; self.output.mkdir()
        self.history_path=self.root/'data'/'reading_history.json'
        self.patches=[patch.object(archive, 'EVENT_PATH', self.root / 'data' / 'events.json'), patch.object(fetch_module, 'OUTPUT_DIR', self.output)]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        for key in ('1','2','3'):
            (self.output/(key+'.txt')).write_text(format_article({'title':'Article '+key,'body':BODY,
                'url':'https://www.dongqiudi.com/articles/'+key}),encoding='utf-8')
        self.sequence=[]
        self.classifier=patch.object(archive,'call_deepseek',side_effect=self.classify_model).start()
        self.addCleanup(patch.stopall)
        self.comparer=patch.object(updates,'call_deepseek',side_effect=self.compare_model).start()

    def urls(self,*keys): return ['https://www.dongqiudi.com/articles/'+key for key in keys]

    def classify_model(self,material,*args,**kwargs):
        self.sequence.append('classify')
        payload=json.loads(material)
        candidates=payload['existing_events']
        result={'decision':'existing' if candidates else 'new',
                'event_id':candidates[0]['event_id'] if candidates else None,
                'event_title':None if candidates else 'Representative reports',
                'event_question':None if candidates else 'Who will represent this player?',
                'event_type':None if candidates else 'representation',
                'reason':'The main report concerns representation.',
                'evidence':'The player is considering a new representative.'}
        return result,{'total_tokens':10}

    def compare_model(self,material,*args,**kwargs):
        self.sequence.append('compare')
        return {'comparable':True,'reason':'Same specific event.','overlap':[],
                'added_in_target':[],'differences':[]},{'total_tokens':20}

    def run_flow(self,urls): return workflow.run_workflow(urls,'fake','fake',self.output,progress=lambda message:None)

    def test_two_new_articles_finish_archiving_before_comparisons(self):
        record,folder=self.run_flow(self.urls('1','2'))
        self.assertEqual(record['status'],'completed')
        self.assertEqual(self.sequence,['classify','classify','compare','compare'])
        self.assertEqual(record['requests'],{'classification':2,'comparison':2})
        first,second=record['articles']
        self.assertEqual(first['event_id'],second['event_id'])
        self.assertEqual(first['comparison']['baseline_article_ids'],['2'])
        self.assertEqual(second['comparison']['baseline_article_ids'],['1'])
        self.assertEqual(json.loads((folder/'trace.json').read_text(encoding='utf-8'))['status'],'completed')
        self.assertIn('参照编号：2',(folder/'report.md').read_text(encoding='utf-8'))
        self.assertFalse(self.history_path.exists())

    def test_no_baseline_is_explained_without_comparison_request(self):
        record,folder=self.run_flow(self.urls('1'))
        self.assertEqual(record['status'],'completed')
        self.assertEqual(record['articles'][0]['comparison_status'],'no_baseline')
        self.assertEqual(record['requests']['comparison'],0)
        self.comparer.assert_not_called()
        self.assertIn('不能判定新增',workflow.render_report(record))

    def test_invalid_url_does_not_block_other_articles(self):
        record,_=self.run_flow(['not a URL']+self.urls('1','2'))
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['articles'][0]['failed_stage'],'validate_url')
        self.assertEqual(record['articles'][2]['stage'],'completed')

    def test_network_failure_continues_without_retry(self):
        original=workflow.fetch_news
        def fetch(url):
            if url.endswith('/1'): raise URLError('fixture failure')
            return original(url)
        with patch.object(workflow,'fetch_news',side_effect=fetch) as mocked:
            record,_=self.run_flow(self.urls('1','2','3'))
        self.assertEqual(mocked.call_count,3)
        self.assertEqual(record['articles'][0]['failed_stage'],'fetch')
        self.assertEqual(record['articles'][2]['stage'],'completed')
        self.assertEqual(record['requests'],{'classification':2,'comparison':2})

    def test_deduplicate_canonical_urls_before_paid_work(self):
        record,_=self.run_flow(self.urls('1')+['https://www.dongqiudi.com/articles/1?utm_source=test'])
        self.assertEqual(len(record['articles']),1)
        self.assertEqual(len(record['duplicates']),1)
        self.assertEqual(record['requests']['classification'],1)

    def test_existing_manual_assignment_preserved_and_not_reclassified(self):
        event_id=archive.create_event('Representative reports','Who represents the player?','representation')
        archive.move_article('1',event_id,'Manual choice',self.output)
        archive.move_article('2',event_id,'Manual choice',self.output)
        original=archive.EVENT_PATH.read_bytes()
        self.history_path.parent.mkdir(parents=True,exist_ok=True)
        self.history_path.write_text('{"version":1,"articles":{"1":{"is_read":true}}}',encoding='utf-8')
        original_history=self.history_path.read_bytes()
        record,_=self.run_flow(self.urls('1','2'))
        self.classifier.assert_not_called()
        self.assertEqual(record['requests']['classification'],0)
        self.assertEqual(original,archive.EVENT_PATH.read_bytes())
        self.assertEqual(original_history,self.history_path.read_bytes())
        self.assertEqual(record['articles'][0]['classification']['assignment']['origin'],'manual')

    def test_uncertain_classification_is_not_forced_or_compared(self):
        uncertain={'decision':'uncertain','event_id':None,'event_title':None,'event_question':None,
                   'event_type':None,'reason':'No clear main event.','evidence':None}
        self.classifier.side_effect=None
        self.classifier.return_value=(uncertain,{'total_tokens':10})
        record,_=self.run_flow(self.urls('1'))
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['articles'][0]['comparison_status'],'skipped')
        self.assertFalse(archive.EVENT_PATH.exists())
        self.comparer.assert_not_called()

    def test_classification_failure_preserves_other_successes(self):
        real=self.classify_model
        def model(material,*args,**kwargs):
            if json.loads(material)['article_id']=='1': raise URLError('fixture')
            return real(material,*args,**kwargs)
        self.classifier.side_effect=model
        record,_=self.run_flow(self.urls('1','2'))
        self.assertEqual(record['articles'][0]['failed_stage'],'classify')
        self.assertEqual(record['articles'][1]['stage'],'completed')
        self.assertEqual(record['requests']['classification'],2)
        self.assertNotIn('1',archive.load_registry()['assignments'])
        self.assertIn('2',archive.load_registry()['assignments'])

    def test_comparison_failure_preserves_archives_and_next_comparison(self):
        real=self.compare_model
        def model(material,*args,**kwargs):
            if json.loads(material)['target']['article_id']=='1': raise URLError('fixture')
            return real(material,*args,**kwargs)
        self.comparer.side_effect=model
        record,_=self.run_flow(self.urls('1','2'))
        self.assertEqual(record['articles'][0]['failed_stage'],'compare')
        self.assertEqual(record['articles'][1]['stage'],'completed')
        self.assertEqual(len(archive.load_registry()['assignments']),2)
        self.assertEqual(record['requests']['comparison'],2)

    def test_over_five_rejected_without_requests_or_output(self):
        with self.assertRaises(ValueError): self.run_flow(self.urls('1','2','3','4','5','6'))
        self.classifier.assert_not_called()
        self.assertFalse(list(self.output.glob('workflow_*')))

    def test_over_five_baselines_explicit_failure_without_comparison(self):
        event_id=archive.create_event('Representative reports','Who represents the player?','representation')
        for key in ('1','2','3','4','5','6','7'):
            (self.output/(key+'.txt')).write_text(format_article({'title':key,'body':BODY,'url':self.urls(key)[0]}),encoding='utf-8')
            archive.move_article(key,event_id,'Fixture',self.output)
        record,_=self.run_flow(self.urls('1'))
        self.assertEqual(record['articles'][0]['failed_stage'],'prepare_comparison')
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['requests']['comparison'],0)
        self.comparer.assert_not_called()

    def test_interrupt_saves_current_stage(self):
        self.classifier.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.run_flow(self.urls('1'))
        folder=next(self.output.glob('workflow_*'))
        record=json.loads((folder/'trace.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'],'interrupted')
        self.assertEqual(record['articles'][0]['stage'],'classify')
        self.assertTrue((folder/'report.md').is_file())

    def test_report_includes_quotes_and_precise_scope(self):
        def model(material,*args,**kwargs):
            payload=json.loads(material)
            return {'comparable':True,'reason':'Same event.','overlap':[],
                    'added_in_target':[{'statement':'Reported extra context.','evidence_target':'No transfer agreement is confirmed.'}],
                    'differences':[]},{'total_tokens':20}
        self.comparer.side_effect=model
        record,folder=self.run_flow(self.urls('1','2'))
        report=(folder/'report.md').read_text(encoding='utf-8')
        self.assertIn('目标摘录：No transfer agreement is confirmed.',report)
        self.assertIn('不要求早于目标或已读',report)
        self.assertIn('相对这些参照多写的信息',report)
        self.assertEqual(len(record['usage']),4)


if __name__=='__main__': unittest.main()
