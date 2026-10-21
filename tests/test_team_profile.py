"""球队档案关键校验：赛季不串、名单不串、缺失不补零、缓存回退。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import team_profile as p


def catalog_data():
    options=[{'tournamentId':str(i),'parentLeagueId':'87','leagueName':'LaLiga','season':s}
             for i,s in [(1,'2026/2027'),(2,'2025/2026'),(3,'2024/2025'),(4,'2023/2024')]]
    options.append({'tournamentId':'8','parentLeagueId':'78','leagueName':'World Cup','season':'2025'})
    return {'details':{'id':9906,'seopath':'atletico-madrid','latestSeason':'2026/2027'},
        'stats':{'primaryLeagueId':87,'tournamentId':'1','tournamentSeasons':options},
        'squad':{'squad':[{'title':'attackers','members':[{'id':123,'name':'Current Player','shirtNumber':9}]}]},
        'overview':{}}


class ProfileTests(unittest.TestCase):
    def test_recent_three_league_seasons_not_displaced_by_single_year_cup(self):
        c=p.catalog_from(catalog_data(),'9906')
        self.assertEqual([o['id'] for o in c['options']],['1','2','3'])
        self.assertEqual(c['roster'][0]['photo'],'https://images.fotmob.com/image_resources/playerimages/123.png')
        self.assertNotIn('goals',c['roster'][0])

    def test_wrong_team_html_rejected(self):
        raw='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'fallback':{'team-9906':{'details':{'id':1}}}}}})+'</script>'
        with self.assertRaises(ValueError):p.team_data(raw,'9906')

    def test_historical_players_only_own_team_and_missing_not_zero(self):
        files={'mins_played':{'TopLists':[{'StatName':'mins_played','StatList':[
          {'ParticiantId':7,'ParticipantName':'Past Player','TeamId':9906,'StatValue':99,'MatchesPlayed':2,'Positions':[105]},
          {'ParticiantId':8,'ParticipantName':'Other Team','TeamId':8634,'StatValue':90}]}]},'goals':None,'goal_assist':None}
        players,warnings=p.season_players(files,'9906')
        self.assertEqual(len(players),1);self.assertEqual(players[0]['name'],'Past Player')
        self.assertEqual(players[0]['appearances'],2);self.assertIsNone(players[0]['goals'])
        self.assertEqual(players[0]['position'],'前锋');self.assertEqual(len(warnings),2)

    def test_summary_uses_selected_team(self):
        d={'tournamentStats':{'Table':{'Lines':[{'TeamId':9906,'Won':22,'Drawn':10,'Lost':6,'Scored':68,'Conceeded':30,'Position':3,'Points':76}]}}}
        self.assertEqual(p.summary_from(d,'9906')['played'],38)
        self.assertEqual(p.summary_from(d,'9906')['goals'],68)
        self.assertEqual(p.summary_from(d,'8634'),{})

    def test_cannot_inject_url_or_paths(self):
        with self.assertRaises(ValueError):p.stat_file('/stats/87/season/1/../../secret')
        with self.assertRaises(ValueError):p.photo_url('../123')
        with self.assertRaises(ValueError):p.get_profile({'team':'test','url':'https://evil.test'})

    def test_reject_unsupported_season_before_network(self):
        with patch.object(p,'get_catalog',return_value=p.catalog_from(catalog_data(),'9906')),patch.object(p,'download') as download:
            with self.assertRaises(ValueError):p.get_season('Test','9906','9999')
            download.assert_not_called()

    def test_reject_current_data_returned_for_historical_request(self):
        with tempfile.TemporaryDirectory() as root,patch.object(p,'CACHE_DIR',Path(root)),patch.object(p,'get_catalog',return_value=p.catalog_from(catalog_data(),'9906')),patch.object(p,'download',return_value=json.dumps({'tournamentId':'1','currentTournament':'1'}).encode()):
            with self.assertRaises(ValueError):p.get_season('Test','9906','3')

    def test_cache_refresh_failure_preserves_timestamp_and_marks_stale(self):
        with tempfile.TemporaryDirectory() as root,patch.object(p,'CACHE_DIR',Path(root)):
            p.remember('test',{'updated_at':'original','value':1})
            with patch.object(p.time,'time',return_value=p.time.time()+100):
                result=p.once('test',lambda:(_ for _ in ()).throw(ValueError('offline')),age=1)
            self.assertTrue(result['stale']);self.assertEqual(result['updated_at'],'original')


if __name__=='__main__':unittest.main()
