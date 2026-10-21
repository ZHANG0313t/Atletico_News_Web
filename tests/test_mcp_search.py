"""离线协议/Agent/时间核查回归：不需要真实密钥，不调用付费接口。"""
import copy
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import mcp_client as protocol
import mcp_search as search
import news_briefing as briefing
from news_search import CHINA, select_candidates

TOOL = {'name': 'tavily_search', 'inputSchema': {'type': 'object',
    'properties': {name: {} for name in ('query', 'topic', 'days', 'max_results', 'search_depth')},
    'required': ['query']}}
NOW = datetime.now(CHINA)
ITEM = {'title': 'Fixture match', 'url': 'https://example.com/news/test',
        'content': 'Fixture team won a match.', 'published_date': NOW.isoformat()}


def call(query='Fixture team', name='tavily_search', cid='c1'):
    return {'id': cid, 'type': 'function', 'function': {'name': name,
        'arguments': json.dumps({'query': query})}}


class Response(io.BytesIO):
    def __init__(self, raw, content_type='application/json', session=None):
        super().__init__(raw)
        self.headers = {'Content-Type': content_type}
        if session:
            self.headers['Mcp-Session-Id'] = session


class ProtocolTests(unittest.TestCase):
    def test_sse_returns_at_matching_response_without_waiting_for_close(self):
        raw = b': ping\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\ndata: {"jsonrpc":"2.0","id":7,"result":{"tools":[]}}\n\nUNREAD'
        response = Response(raw, 'text/event-stream')
        self.assertEqual(protocol.read_result(response, 7, time.monotonic()+5), {'tools': []})
        self.assertEqual(response.read(), b'UNREAD')

    def test_json_rpc_error_does_not_echo_remote_secret(self):
        with self.assertRaises(ValueError) as error:
            protocol.decode_message(b'{"jsonrpc":"2.0","id":1,"error":{"message":"secret-token"}}', 1)
        self.assertNotIn('secret-token', str(error.exception))

    def test_wrong_json_id_is_rejected(self):
        with self.assertRaises(ValueError):
            protocol.read_result(Response(b'{"jsonrpc":"2.0","id":2,"result":{}}'),1,time.monotonic()+5)

    def test_endpoint_cannot_contain_key_or_use_http(self):
        for url in ('http://example.com/mcp', 'https://example.com/mcp?key=secret', 'https://user@example.com/mcp'):
            with self.assertRaises(ValueError):
                protocol.MCPClient(url, 'fake')

    @patch.object(protocol, 'validate_public_target', return_value='https://example.com/mcp')
    def test_real_handshake_headers_pagination_call_and_delete(self, _):
        requests = []
        client = protocol.MCPClient('https://example.com/mcp', 'fake-private-key')
        def open_request(request, timeout):
            requests.append(request)
            if request.method == 'DELETE':
                return Response(b'')
            body = json.loads(request.data)
            method = body['method']
            if method == 'notifications/initialized':
                return Response(b'')
            if method == 'initialize':
                result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}}
            elif method == 'tools/list':
                result = {'tools': [TOOL], 'nextCursor': 'page2'} if not body['params'] else {'tools': []}
            else:
                result = {'content': [{'type': 'text', 'text': '{}'}]}
            return Response(json.dumps({'jsonrpc':'2.0','id':body['id'],'result':result}).encode(), session='test-session')
        with patch.object(client.opener, 'open', side_effect=open_request):
            with client:
                self.assertEqual(client.list_tools(), [TOOL])
                self.assertIn('content', client.call_tool('tavily_search', {'query':'test'}))
        self.assertEqual([json.loads(r.data)['method'] for r in requests[:-1]],
            ['initialize','notifications/initialized','tools/list','tools/list','tools/call'])
        self.assertEqual(requests[-1].method, 'DELETE')
        self.assertEqual(requests[2].get_header('Mcp-session-id'), 'test-session')
        self.assertEqual(requests[2].get_header('Mcp-protocol-version'), '2025-06-18')
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer fake-private-key')

    def test_unsupported_version_stops_before_tools(self):
        client = protocol.MCPClient('https://example.com/mcp','fake')
        with patch.object(client, '_post', return_value={'protocolVersion':'2099-01-01','capabilities':{'tools':{}}}) as post:
            with self.assertRaises(ValueError):
                client.initialize()
            self.assertEqual(post.call_count, 1)

    def test_http_error_is_redacted(self):
        client = protocol.MCPClient('https://example.com/mcp','fake')
        with patch.object(protocol, 'validate_public_target'), patch.object(client.opener,'open',
                side_effect=HTTPError('https://example.com/?key=secret',401,'secret',{},io.BytesIO(b''))):
            with self.assertRaises(ValueError) as error:
                client.initialize()
        self.assertIn('401', str(error.exception))
        self.assertNotIn('secret', str(error.exception))


class SearchTests(unittest.TestCase):
    def test_json_and_official_text_results(self):
        for result in ({'structuredContent':{'results':[ITEM]}},
                {'content':[{'type':'text','text':json.dumps({'results':[ITEM]})}]}):
            rows = search.parse_search_result(result, 'team')['articles']
            self.assertEqual(len(rows),1)
            self.assertIsNotNone(rows[0]['search_reported_at'])
        text = 'Request ID: example\nDetailed Results:\n\nTitle: Fixture match\nID: item1\nURL: https://example.com/news/test\nContent: Fixture team won a match.'
        rows = search.parse_search_result({'content':[{'type':'text','text':text}]},'team')['articles']
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0]['search_reported_at'])

    def test_remote_error_text_not_treated_as_empty_success(self):
        with self.assertRaises(ValueError):
            search.parse_search_result({'content':[{'type':'text','text':'API error secret'}]},'test')

    def test_missing_date_candidates_are_read_only_in_mcp_mode(self):
        result = search.parse_search_result({'structuredContent':{'results':[{**ITEM,'published_date':None}]}},'team')
        start = NOW-timedelta(hours=72)
        self.assertEqual(len(select_candidates([result],start,NOW)[0]),0)
        self.assertEqual(len(select_candidates([result],start,NOW,allow_missing_dates=True)[0]),1)

    def test_no_config_is_bing_and_missing_mcp_key_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'settings.ini'
            self.assertEqual(search.load_mcp_settings(path)['provider'],'bing')
            path.write_text('[search]\nprovider=tavily_mcp\n',encoding='utf-8')
            with self.assertRaises(ValueError):
                search.load_mcp_settings(path)

    def test_arguments_use_discovered_schema_and_bounded_news_search(self):
        arguments = search.search_arguments(TOOL,'team',72)
        self.assertEqual(arguments['topic'],'news')
        self.assertEqual(arguments['days'],3)
        self.assertEqual(arguments['max_results'],10)
        self.assertNotIn('time_range',arguments)

    def run_loop(self, replies, limit=3, failure=None):
        record = {'requests':{'search_decisions':0,'search':0},'usage':[],'searches':[],'warnings':[]}
        with patch.object(search,'MCPClient') as cls, patch.object(search,'request_decision',side_effect=replies) as decision:
            client = cls.return_value.__enter__.return_value
            client.list_tools.return_value = [TOOL]
            client.call_tool.return_value = {'structuredContent':{'results':[ITEM]}}
            if failure:
                client.call_tool.side_effect=failure
            results = search.run_mcp_search('news',{'hours':72},{'endpoint':'fake','api_key':'fake-secret'},
                record,lambda:None,lambda _:None,'deepseek-secret','fake',limit)
            sent = [copy.deepcopy(c.args[0]) for c in decision.call_args_list]
        return results,record,sent

    def test_model_can_search_then_change_query_then_stop(self):
        results,record,sent = self.run_loop([
            ({'role':'assistant','content':None,'tool_calls':[call()]},{}),
            ({'role':'assistant','content':None,'tool_calls':[call('Fixture injuries',cid='c2')]},{}),
            ({'role':'assistant','content':'done'},{}),])
        self.assertEqual(len(results),2)
        self.assertEqual(record['requests'],{'search_decisions':3,'search':2})
        self.assertEqual(record['search_agent']['stop_reason'],'model_finished')
        self.assertEqual([e['query'] for e in record['search_agent']['events']],['Fixture team','Fixture injuries'])
        self.assertNotIn('fake-secret',json.dumps(record))
        self.assertNotIn('fake-secret',json.dumps(sent[0]))
        self.assertTrue(any(m['role']=='tool' for m in sent[-1]))

    def test_duplicate_queries_and_unknown_tools_consume_budget(self):
        results,record,_ = self.run_loop([
            ({'role':'assistant','tool_calls':[call(),call(cid='c2'),call(name='tavily_crawl',cid='c3'),call('extra',cid='c4')]},{}),])
        self.assertEqual(len(results),1)
        self.assertEqual(record['requests']['search'],1)
        self.assertEqual(record['search_agent']['stop_reason'],'budget_reached')
        self.assertEqual(sum(e['ok'] for e in record['search_agent']['events']),1)

    def test_failed_later_decision_preserves_search_results(self):
        results,record,_ = self.run_loop([
            ({'role':'assistant','tool_calls':[call()]},{}),ValueError('fixture')])
        self.assertEqual(len(results),1)
        self.assertEqual(record['search_agent']['stop_reason'],'decision_error')

    def test_web_status_never_exposes_key(self):
        with patch.object(search,'load_mcp_settings',return_value={'provider':'tavily_mcp','api_key':'private'}):
            self.assertEqual(search.public_search_status()['provider'],'tavily_mcp')
            self.assertNotIn('private',json.dumps(search.public_search_status()))


class BriefingIntegrationTests(unittest.TestCase):
    def test_mcp_branch_with_undated_results_requires_page_dates(self):
        from project_paths import PROJECT_DIR
        record_settings = {'provider':'tavily_mcp','api_key':'fixture','endpoint':'https://example.com/mcp'}
        plan = {'team':'Fixture','hours':72,'queries':['Fixture'],'aliases':['Fixture'],'title':'Fixture news'}
        item = {**ITEM,'published_date':None}
        result = search.parse_search_result({'structuredContent':{'results':[item]}},'Fixture')
        for page_date, fetch_error, expected in ((NOW.isoformat(),False,1),(None,False,0),(None,True,0),
                ((NOW-timedelta(days=10)).isoformat(),False,0)):
            with self.subTest(page_date=page_date,fetch_error=fetch_error), tempfile.TemporaryDirectory(dir=str(PROJECT_DIR)) as folder:
                def search_stage(q,p,s,record,save,progress,k,m,limit):
                    record['requests']['search_decisions']+=1
                    record['requests']['search']+=1
                    record['searches'].append({'query':'Fixture','count':1,'provider':'Tavily MCP'})
                    return [result]
                article = {'title':ITEM['title'],'body':ITEM['content'],'published_at':page_date,
                    'source_status':'downloaded','extraction_method':'site:fixture'}
                with patch.object(briefing,'load_mcp_settings',return_value=record_settings), \
                        patch.object(briefing,'plan_request',return_value=(plan,{})), \
                        patch.object(briefing,'run_mcp_search',side_effect=search_stage), \
                        patch.object(briefing,'fetch_news',side_effect=OSError('fixture') if fetch_error else None,return_value=article), \
                        patch.object(briefing,'organize',return_value={}):
                    record,_ = briefing.run_briefing('Fixture news','fake','fake',output_dir=Path(folder),progress=lambda _:None)
                self.assertEqual(len(record['articles']),expected)
                self.assertEqual(record['settings']['max_model_requests'],6)
                self.assertIn('Tavily',record['search_scope'])
                self.assertNotIn('fixture',json.dumps(record.get('settings')))


if __name__=='__main__':
    unittest.main()
