"""事件归档离线测试：模型响应模拟，全部档案放在项目内隔离临时目录。"""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import event_archive as archive
from news_storage import format_article
from project_paths import PROJECT_DIR

BODY = 'The player is considering a new representative. No transfer agreement has been reported.'


class EventArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='event_test_', dir=str(PROJECT_DIR))
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, PROJECT_DIR.resolve())
        self.output = self.root / 'output'; self.output.mkdir()
        self.path = self.root / 'data' / 'events.json'
        self.history_path=self.root/'data'/'reading_history.json'
        self.patches=[patch.object(archive, 'EVENT_PATH', self.root / 'data' / 'events.json')]
        for item in self.patches: item.start()
        for article_id in ('1', '2'):
            (self.output / (article_id + '.txt')).write_text(format_article(
                {'title':'Fixture ' + article_id,'body':BODY,'url':'https://example.net/' + article_id}),encoding='utf-8')
        self.proposal = {'decision':'new','event_id':None,'event_title':'Player representation reports',
                         'event_question':'Who may represent this player? Excludes claims that a club transfer is agreed.',
                         'event_type':'representation','reason':'The main report concerns representation.',
                         'evidence':'The player is considering a new representative.'}

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        assert self.root.parent == PROJECT_DIR.resolve()
        self.temp.cleanup()

    def classify(self, article_id='1', proposal=None):
        with patch.object(archive,'call_deepseek',return_value=(proposal or self.proposal,{'total_tokens':10})) as model:
            result=archive.classify_article(article_id,'fake','fake',self.output)
        return result,model

    def test_new_archive_has_reason_quote_and_no_read_mutation(self):
        result,model=self.classify()
        self.assertEqual(result['status'],'archived')
        self.assertRegex(result['event_id'],r'^evt_[0-9a-f]{12}$')
        self.assertEqual(result['assignment']['evidence'],self.proposal['evidence'])
        self.assertEqual(result['event']['event_type'],'representation')
        self.assertFalse(self.history_path.exists())
        self.assertEqual(model.call_count,1)

    def test_existing_event_reused_and_list_show_work(self):
        first,_=self.classify()
        proposal={**self.proposal,'decision':'existing','event_id':first['event_id'],
                  'event_title':None,'event_question':None,'event_type':None}
        second,_=self.classify('2',proposal)
        self.assertEqual(first['event_id'],second['event_id'])
        self.assertEqual(len(archive.list_events()['events']),1)
        shown=archive.get_event(first['event_id'],self.output)
        self.assertEqual({a['article_id'] for a in shown['articles']},{'1','2'})

    def test_duplicate_classification_does_not_call_model_or_duplicate_change(self):
        self.classify()
        original=self.path.read_bytes()
        result,model=self.classify()
        self.assertEqual(result['status'],'already_archived')
        model.assert_not_called()
        self.assertEqual(self.path.read_bytes(),original)

    def test_same_definition_new_proposal_reuses_event(self):
        first,_=self.classify()
        second,_=self.classify('2')
        self.assertEqual(first['event_id'],second['event_id'])
        self.assertEqual(len(archive.load_registry()['events']),1)

    def test_uncertain_does_not_create_event(self):
        proposal={'decision':'uncertain','event_id':None,'event_title':None,'event_question':None,
                  'event_type':None,'reason':'Main question cannot be determined.','evidence':None}
        result,_=self.classify(proposal=proposal)
        self.assertEqual(result['status'],'uncertain')
        self.assertFalse(self.path.exists())

    def test_invalid_event_id_and_invented_quote_not_saved(self):
        bad_existing={**self.proposal,'decision':'existing','event_id':'evt_000000000000',
                      'event_title':None,'event_question':None,'event_type':None}
        bad_quote={**self.proposal,'evidence':'This sentence is invented.'}
        bad_type={**self.proposal,'decision':[]}
        for proposal in (bad_existing,bad_quote,bad_type):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                self.classify(proposal=proposal)
            self.assertFalse(self.path.exists())

    def test_existing_proposal_cannot_redefine_event(self):
        first,_=self.classify()
        proposal={**self.proposal,'decision':'existing','event_id':first['event_id']}
        original=self.path.read_bytes()
        with self.assertRaises(ValueError): self.classify('2',proposal)
        self.assertEqual(self.path.read_bytes(),original)

    def test_manual_correction_preserves_history_and_is_not_overwritten(self):
        first,_=self.classify()
        new_id=archive.create_event('Specific transfer rumour','Will this player join club B?','transfer')
        corrected=archive.move_article('1',new_id,'User identifies transfer as the main question.',self.output)
        self.assertEqual(corrected['assignment']['origin'],'manual')
        result,model=self.classify()
        model.assert_not_called()
        self.assertEqual(result['event_id'],new_id)
        change=archive.load_registry()['changes'][-1]
        self.assertEqual(change['from_event'],first['event_id'])
        self.assertEqual(change['to_event'],new_id)
        self.assertFalse(self.history_path.exists())

    def test_invalid_manual_target_preserves_previous_assignment(self):
        self.classify(); original=self.path.read_bytes()
        with self.assertRaises(ValueError):
            archive.move_article('1','evt_000000000000','Fixture reason',self.output)
        self.assertEqual(self.path.read_bytes(),original)

    def test_manual_correction_during_model_wait_wins(self):
        event_id=archive.create_event('Manually chosen question','Does the player have a new representative?','representation')
        def response(*args,**kwargs):
            archive.move_article('1',event_id,'Explicit correction during model wait.',self.output)
            return self.proposal,{}
        with patch.object(archive,'call_deepseek',side_effect=response):
            result=archive.classify_article('1','fake','fake',self.output)
        self.assertEqual(result['status'],'kept_current_assignment')
        self.assertEqual(result['assignment']['origin'],'manual')
        self.assertEqual(result['event_id'],event_id)

    def test_corrupt_registry_not_reset(self):
        self.path.parent.mkdir(); self.path.write_text('broken JSON',encoding='utf-8')
        with self.assertRaises(ValueError): archive.load_registry()
        self.assertEqual(self.path.read_text(encoding='utf-8'),'broken JSON')

    def test_write_failure_preserves_registry(self):
        self.classify(); original=self.path.read_bytes()
        with patch.object(archive.os,'replace',side_effect=OSError('fixture failure')),self.assertRaises(OSError):
            archive.create_event('Another concrete event','Another specific question?','other')
        self.assertEqual(self.path.read_bytes(),original)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))
        self.assertFalse(self.path.with_suffix('.lock').exists())




if __name__=='__main__':
    unittest.main()
