"""分别限制模型与MCP的请求数。只对明确的HTTP 429最多重试两次。

超时可能意味着远端已经处理，不能盲目重发付费请求。
"""
from contextlib import contextmanager
import threading
import time
from urllib.error import HTTPError

SLOTS = {'deepseek': threading.BoundedSemaphore(2), 'mcp': threading.BoundedSemaphore(2)}
LOCK = threading.Lock()
NEXT = {'deepseek': 0., 'mcp': 0.}


@contextmanager
def limited_open(provider, opener, request, timeout):
    with SLOTS[provider]:
        for attempt in range(3):
            # 各提供方分别按至少0.25秒间隔发出请求，不共享模型对话内容。
            with LOCK:
                delay = max(0, NEXT[provider] - time.monotonic())
                NEXT[provider] = time.monotonic() + delay + .25
            if delay:
                time.sleep(delay)
            try:
                response = opener(request, timeout=timeout)
                break
            except HTTPError as error:
                if error.code != 429 or attempt == 2:
                    raise
                try:
                    wait = float(error.headers.get('Retry-After', 2 ** (attempt+1)))
                except (ValueError, TypeError):
                    wait = 2 ** (attempt+1)
                error.close()
                time.sleep(max(1, min(wait, 10)))
        with response:
            yield response
