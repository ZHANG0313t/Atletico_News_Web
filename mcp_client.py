"""小型远程 MCP 客户端，兼容本项目的 Python 3.9，无需安装新依赖。

只实现本课需要的 Streamable HTTP：握手、列工具、调用工具、结束会话。
不是完整 MCP SDK：不支持 stdio、OAuth 登录、服务端反向请求或断线续传。
"""
import json
import time
from request_limits import limited_open
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from news_urls import validate_public_target

PROTOCOLS = ('2025-06-18', '2025-03-26')
MAX_BYTES = 2_000_000


class NoRedirect(HTTPRedirectHandler):
    # 认证头只发给配置的地址，不跟随重定向把密钥交给另一台服务器。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def decode_message(raw, request_id):
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('MCP 返回了无效 JSON。') from None
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        raise ValueError('MCP 返回的消息格式无效。')
    if message.get('id') != request_id:
        return None  # SSE 中可能先收到进度通知。
    if 'error' in message:
        # 不输出远端错误原文，远端可能回显认证信息。
        raise ValueError('MCP 请求被服务拒绝，请检查权限、参数和服务状态。')
    result = message.get('result')
    if not isinstance(result, dict):
        raise ValueError('MCP 没有返回有效结果。')
    return result


def read_result(response, request_id, deadline):
    """同时接受 JSON 和 SSE；收到当前请求的结果后立即返回，不等长连接关闭。"""
    content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
    if content_type == 'application/json':
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('MCP 返回过大。')
        result = decode_message(raw, request_id)
        if result is None:
            raise ValueError('MCP 响应编号不匹配。')
        return result
    if content_type != 'text/event-stream':
        raise ValueError('MCP 返回类型不受本客户端支持。')
    size = 0
    data = []
    while time.monotonic() < deadline:
        line = response.readline(MAX_BYTES + 1)
        size += len(line)
        if size > MAX_BYTES:
            raise ValueError('MCP 事件流过大。')
        if not line or line in (b'\n', b'\r\n'):
            if data:
                result = decode_message(b'\n'.join(data), request_id)
                if result is not None:
                    return result
                data = []
            if not line:
                break
        elif line.startswith(b'data:'):
            data.append(line[5:].lstrip(b' ').rstrip(b'\r\n'))
    raise ValueError('MCP 事件流结束或超时，尚未取得本次结果。')


class MCPClient:
    def __init__(self, endpoint, api_key):
        parts = urlsplit(endpoint)
        if (parts.scheme != 'https' or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError('远程 MCP 地址须为不含密钥参数的 HTTPS 地址。')
        self.endpoint = endpoint
        self.api_key = api_key
        self.session = None
        self.version = None
        self.counter = 0
        self.opener = build_opener(NoRedirect())

    def _post(self, method, params=None, notification=False):
        validate_public_target(self.endpoint)
        self.counter += 1
        message = {'jsonrpc': '2.0', 'method': method}
        if not notification:
            message['id'] = self.counter
        if params is not None:
            message['params'] = params
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream',
                   'Authorization': 'Bearer ' + self.api_key}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        if self.version:
            headers['MCP-Protocol-Version'] = self.version
        request = Request(self.endpoint, data=json.dumps(message).encode('utf-8'),
                          headers=headers, method='POST')
        try:
            deadline = time.monotonic() + 45
            with limited_open('mcp', self.opener.open, request, timeout=45) as response:
                if method == 'initialize':
                    session = response.headers.get('Mcp-Session-Id')
                    if session and (len(session) > 512 or not all(33 <= ord(c) <= 126 for c in session)):
                        raise ValueError('MCP 会话标识无效。')
                    self.session = session
                if notification:
                    return None
                return read_result(response, self.counter, deadline)
        except HTTPError as error:
            code = error.code
            error.close()
            raise ValueError('MCP HTTP %s：请检查密钥、额度、地址或服务状态。' % code) from None
        except (URLError, OSError):
            raise ValueError('MCP 网络连接失败或超时，请检查网络与代理。') from None

    def initialize(self):
        result = self._post('initialize', {'protocolVersion': PROTOCOLS[0],
            'capabilities': {}, 'clientInfo': {'name': 'football-news-agent', 'version': '1.0'}})
        self.version = result.get('protocolVersion')
        capabilities = result.get('capabilities')
        if (self.version not in PROTOCOLS or not isinstance(capabilities, dict)
                or not isinstance(capabilities.get('tools'), dict)):
            raise ValueError('MCP 版本或搜索工具能力不兼容。')
        self._post('notifications/initialized', notification=True)
        return result

    def list_tools(self):
        tools = []
        cursor = None
        seen = set()
        for _ in range(8):
            result = self._post('tools/list', {'cursor': cursor} if cursor else {})
            page = result.get('tools')
            if not isinstance(page, list) or len(tools) + len(page) > 100:
                raise ValueError('MCP 工具列表无效或过大。')
            tools.extend(page)
            cursor = result.get('nextCursor')
            if not cursor:
                return tools
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError('MCP 工具分页游标无效。')
            seen.add(cursor)
        raise ValueError('MCP 工具分页超过本课上限。')

    def call_tool(self, name, arguments):
        result = self._post('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            raise ValueError('MCP 搜索工具执行失败，未把错误信息当新闻。')
        return result

    def close(self):
        if not self.session:
            return
        try:
            request = Request(self.endpoint, method='DELETE', headers={
                'Authorization': 'Bearer ' + self.api_key, 'Mcp-Session-Id': self.session,
                'MCP-Protocol-Version': self.version or PROTOCOLS[0]})
            with self.opener.open(request, timeout=5):
                pass
        except (OSError, ValueError):
            pass  # 清理失败不能覆盖已经得到的搜索结果。
        self.session = None

    def __enter__(self):
        try:
            self.initialize()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *args):
        self.close()
