"""赛程真实结构的离线检查，不访问网站，不依赖模型或付费密钥。"""
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import team_fixtures as fixtures
from news_search import CHINA


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        item=patch.object(fixtures,'CACHE_DIR',Path(self.temp.name));item.start();self.addCleanup(item.stop)
        fixtures.REFRESHING.clear();fixtures.REFRESH_ERRORS.clear()
        self.now=datetime(2026,10,19,12,tzinfo=CHINA)

    def fixture(self,fid,delta,finished=True,hs=2,aws=1):
        return {'id':fid,'home':{'id':9906,'name':'Atletico Madrid','score':hs},
                'away':{'id':8633,'name':'Real Madrid','score':aws},'tournament':{'name':'LaLiga'},
                'pageUrl':'/matches/real-madrid-vs-atletico-madrid/example#'+str(fid),
                'status':{'utcTime':(self.now+timedelta(days=delta)).isoformat(),
                          'finished':finished,'started':finished,'cancelled':False}}

    def page(self,rows,tid=9906):
        team={'details':{'id':tid,'name':'Atletico Madrid','seopath':'atletico-madrid'},
              'fixtures':{'allFixtures':{'fixtures':rows}}}
        data={'props':{'pageProps':{'fallback':{'team-'+str(tid):team}}}}
        return ('<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>').encode()

    def test_last_and_next_five_order_and_real_zero_scores(self):
        rows=[self.fixture(i,-i,hs=0,aws=0) for i in range(1,9)]
        rows += [self.fixture(100+i,i,False,0,0) for i in range(1,9)]
        result=fixtures.parse_fixtures(self.page(rows),9906,self.now)
        self.assertEqual(len(result['past']),5);self.assertEqual(len(result['future']),5)
        self.assertEqual(result['past'][0]['id'],'1');self.assertEqual(result['future'][0]['id'],'101')
        self.assertEqual(result['past'][0]['score'],'0 - 0');self.assertIsNone(result['future'][0]['score'])
        self.assertEqual(result['past'][0]['result'],'draw')
        self.assertTrue(result['team']['badge'].endswith('/9906.png'))

    def test_wrong_team_and_broken_markup_not_empty_success(self):
        for raw in (b'<html>login</html>',self.page([self.fixture(1,-1)],8633)):
            with self.assertRaises(ValueError):fixtures.parse_fixtures(raw,9906,self.now)

    def test_statuses_and_unrelated_fixture_filtered(self):
        live=self.fixture(1,0,False);live['status']['started']=True
        cancelled=self.fixture(2,1,False);cancelled['status']['cancelled']=True
        postponed=self.fixture(3,-1,False);postponed['status']['reason']={'short':'Postp'}
        unrelated=self.fixture(4,-2);unrelated['home']['id']=2;unrelated['away']['id']=3
        result=fixtures.parse_fixtures(self.page([live,cancelled,postponed,unrelated]),9906,self.now)
        self.assertEqual({r['state'] for r in result['future']},{'live','cancelled','postponed'})
        self.assertEqual(result['past'],[])

    def test_directory_primary_locations_only_and_unsafe_slug_rejected(self):
        raw=b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.fotmob.com/teams/9906/overview/atletico-madrid</loc></url><url><loc>https://evil.example/teams/1/overview/test</loc></url></urlset>'
        rows=fixtures.parse_directory(raw);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['name'],'马德里竞技')
        with self.assertRaises(ValueError):fixtures.parse_directory(b'<!DOCTYPE x><x/>')

    def test_alias_and_ambiguous_name_choices(self):
        rows=[{'id':'9906','slug':'atletico-madrid','name':'马竞'}, {'id':'8633','slug':'real-madrid','name':'皇马'}]
        with patch.object(fixtures,'directory_page',return_value=rows):
            self.assertEqual(fixtures.resolve_team('马德里竞技')['id'],'9906')
            self.assertEqual(len(fixtures.resolve_team('Madrid')['choices']),2)
        for value in ('../../secret','-1','1?url=x'):
            with self.assertRaises(ValueError):fixtures.resolve_team('team',value)

    def test_cache_reused_and_refresh_failure_explicitly_stale(self):
        sample={'team':{'id':'9906'},'past':[],'future':[],'updated_at':'old','stale':False}
        team={'id':'9906','slug':'atletico-madrid','name':'马竞'}
        with patch.object(fixtures,'resolve_team',return_value=team),patch.object(fixtures,'cached',return_value=sample),patch.object(fixtures,'download') as download:
            result=fixtures.get_fixtures('马竞');self.assertFalse(result['refreshing']);download.assert_not_called()
        with patch.object(fixtures,'resolve_team',return_value=team),patch.object(fixtures,'cached',side_effect=[None,sample]),patch.object(fixtures.threading,'Thread') as thread:
            result=fixtures.get_fixtures('马竞');self.assertTrue(result['stale']);self.assertTrue(result['refreshing']);thread.assert_called_once()
        with patch.object(fixtures,'download',side_effect=ValueError('offline')):
            fixtures.refresh_team(team)
        with patch.object(fixtures,'resolve_team',return_value=team),patch.object(fixtures,'cached',side_effect=[None,sample]),patch.object(fixtures.threading,'Thread') as thread:
            result=fixtures.get_fixtures('马竞');self.assertFalse(result['refreshing']);self.assertIn('刷新失败',result['warning']);thread.assert_not_called()
        self.assertFalse(sample['stale'])

    def test_expired_data_returns_before_network_finishes_and_refresh_is_deduplicated(self):
        team={'id':'9906','slug':'atletico-madrid','name':'马竞'}
        old={'team':{'id':'9906'},'past':[],'future':[],'updated_at':'old','stale':False}
        fixtures.save_cache('team_9906.json',{'saved_at':time.time()-1000,'data':old})
        entered=threading.Event();release=threading.Event();threads=[];real_thread=threading.Thread
        def download(path):
            entered.set();release.wait(2);return self.page([self.fixture(1,-1)])
        def factory(*args,**kwargs):
            thread=real_thread(*args,**kwargs);threads.append(thread);return thread
        with patch.object(fixtures,'resolve_team',return_value=team),patch.object(fixtures,'download',side_effect=download),patch.object(fixtures.threading,'Thread',side_effect=factory):
            try:
                result=fixtures.get_fixtures('马竞');self.assertTrue(result['refreshing']);self.assertEqual(result['updated_at'],'old')
                self.assertTrue(entered.wait(1));self.assertFalse(release.is_set())
                again=fixtures.get_fixtures('马竞');self.assertTrue(again['refreshing']);self.assertEqual(len(threads),1)
            finally:
                release.set()
                for thread in threads:thread.join(timeout=3)
        self.assertNotEqual(fixtures.cached('team_9906.json',900)['updated_at'],'old')
        self.assertFalse(fixtures.REFRESHING)

    def test_beijing_time_and_source_link(self):
        row=self.fixture(1,1,False);row['status']['utcTime']='2026-10-20T19:00:00.000Z'
        result=fixtures.parse_fixtures(self.page([row]),9906,self.now)
        self.assertEqual(result['future'][0]['time'],'10-21 03:00')
        self.assertEqual(result['source_url'],'https://www.fotmob.com/teams/9906/fixtures/atletico-madrid')


if __name__=='__main__':unittest.main()
