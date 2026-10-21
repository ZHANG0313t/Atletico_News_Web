"""第九课离线验证。模拟API，使用独立档案，不动真实新闻或阅读记录。"""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import event_archive as archive
import event_updates as updates
from news_storage import format_article
from project_paths import PROJECT_DIR


class EventUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='event_update_test_', dir=str(PROJECT_DIR))
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, PROJECT_DIR.resolve())
        self.output = self.root / 'output'; self.output.mkdir()
        self.history_path=self.root/'data'/'reading_history.json'
        self.patches=[patch.object(archive, 'EVENT_PATH', self.root / 'data' / 'events.json')]
        for item in self.patches: item.start()
        self.bodies = {'1':'The player may change his representative.',
                       '2':'No agreement has been announced.',
                       '3':'The player may change his representative. No agreement has been announced.',
                       '4':'An unrelated match report.'}
        for key, body in self.bodies.items():
            self.write_article(key, body)
        self.event_id = archive.create_event('Representative reports', 'Who will represent the player?', 'representation')
        for key in ('1','2','3'): archive.move_article(key, self.event_id, 'Fixture assignment', self.output)
        other = archive.create_event('Other event', 'A specific match', 'match')
        archive.move_article('4', other, 'Other event', self.output)
        self.result = {'comparable':True, 'reason':'Same specific event.', 'overlap':[
            {'statement':'Considering a change.', 'evidence_target':self.bodies['1'],
             'evidence_baseline':[{'article_id':'1','quote':self.bodies['1']}]},
            {'statement':'No announcement.', 'evidence_target':self.bodies['2'],
             'evidence_baseline':[{'article_id':'2','quote':self.bodies['2']}]}],
            'added_in_target':[], 'differences':[]}

    def write_article(self, key, body):
        (self.output / (key + '.txt')).write_text(format_article(
            {'title':'Fixture '+key, 'body':body,'url':'https://example.net/'+key}),encoding='utf-8')

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        assert self.root.parent == PROJECT_DIR.resolve()
        self.temp.cleanup()

    def model(self, result=None):
        return patch.object(updates,'call_deepseek',return_value=(result if result is not None else self.result,{'total_tokens':20}))

    def dispatch(self, budget=None):
        return updates.compare_event_update('3','fake','fake',self.output)

    def test_full_same_event_set_excludes_self_and_other_event(self):
        original = archive.EVENT_PATH.read_bytes()
        with self.model() as model:
            result = self.dispatch()
        payload = json.loads(model.call_args.args[0])
        self.assertEqual([item['article_id'] for item in payload['baselines']], ['1','2'])
        self.assertEqual(payload['target']['article_id'], '3')
        self.assertEqual(result['baseline_article_ids'], ['1','2'])
        self.assertEqual(result['source_articles']['target']['body'], self.bodies['3'])
        self.assertEqual(archive.EVENT_PATH.read_bytes(), original)
        self.assertFalse(self.history_path.exists())
        self.assertEqual(model.call_count, 1)

    def test_union_can_have_no_added_information(self):
        with self.model(): result = self.dispatch()
        self.assertFalse(result['comparison']['added_in_target'])
        self.assertEqual(len(result['comparison']['overlap']), 2)

    def test_unknown_and_single_article_fail_before_api_or_budget(self):
        with self.model() as model:
            for key in ('999','4'):
                budget={'comparisons':0,'max_comparisons':2}
                with self.assertRaises(ValueError):
                    updates.compare_event_update(key,'fake','fake',self.output)
                self.assertEqual(budget['comparisons'],0)
        model.assert_not_called()

    def test_over_limit_does_not_silently_choose_subset(self):
        for key in ('5','6','7','8'):
            self.write_article(key,'Another report.')
            archive.move_article(key,self.event_id,'Fixture',self.output)
        with self.model() as model, self.assertRaises(ValueError): self.dispatch()
        model.assert_not_called()

    def test_missing_baseline_fails_instead_of_comparing_titles(self):
        (self.output / '2.txt').unlink()
        with self.model() as model, self.assertRaises(OSError): self.dispatch()
        model.assert_not_called()

    def test_oversized_materials_rejected_before_api(self):
        with patch.object(updates,'MAX_MATERIAL_CHARS',10), self.model() as model, self.assertRaises(ValueError):
            self.dispatch()
        model.assert_not_called()

    def test_foreign_article_and_misattributed_quote_rejected(self):
        for key, quote in [('4',self.bodies['4']), ('1',self.bodies['2']), ('3',self.bodies['1'])]:
            bad=copy.deepcopy(self.result)
            bad['overlap'][0]['evidence_baseline']=[{'article_id':key,'quote':quote}]
            with self.subTest(key=key), self.model(bad), self.assertRaises(ValueError): self.dispatch()

    def test_invented_target_quote_rejected(self):
        bad=copy.deepcopy(self.result)
        bad['added_in_target']=[{'statement':'A signing.', 'evidence_target':'A signing is official.'}]
        with self.model(bad), self.assertRaises(ValueError): self.dispatch()

    def test_incomparable_and_invalid_schema(self):
        no={'comparable':False,'reason':'Assignment is questionable.', 'overlap':[], 'added_in_target':[], 'differences':[]}
        with self.model(no): self.assertFalse(self.dispatch()['comparison']['comparable'])
        for bad in [{**self.result,'comparable':'true'}, {**self.result,'comparable':False},
                    {**self.result,'differences':[{'statement':'Different wording.','evidence_target':self.bodies['1'],
                    'evidence_baseline':[{'article_id':'1','quote':self.bodies['1']}], 'is_contradiction':'false'}]}]:
            with self.model(bad), self.assertRaises(ValueError): self.dispatch()




if __name__ == '__main__': unittest.main()
