"""本地配置与诊断。python mcp_setup.py；密钥输入不回显，不调用 DeepSeek。

默认只做 MCP 握手和工具发现，成功后才将网页搜索切换到 MCP。
--check 只检查已有配置；--test-search 额外执行一次真实搜索，会消耗 Tavily 额度。
--use-bing 切回旧搜索。配置文件与 DeepSeek 的配置分开。
"""
import argparse
import configparser
import getpass
import os
import sys

from mcp_client import MCPClient
from mcp_search import (CONFIG_PATH, DEFAULT_ENDPOINT, load_mcp_settings,
                        discover_search, search_arguments, parse_search_result)


def write_config(provider, key, endpoint=DEFAULT_ENDPOINT):
    config = configparser.ConfigParser(interpolation=None)
    config['search'] = {'provider': provider}
    config['tavily'] = {'url': endpoint, 'api_key': key}
    temporary = CONFIG_PATH.with_suffix('.ini.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        config.write(handle)
    os.replace(str(temporary), str(CONFIG_PATH))


def check(settings, query=None):
    if not settings['api_key'] or settings['api_key'] == 'YOUR_TAVILY_API_KEY':
        raise ValueError('尚未配置 Tavily 密钥，请先运行 python mcp_setup.py。')
    print('正在连接远程 MCP……', flush=True)
    with MCPClient(settings['endpoint'], settings['api_key']) as client:
        tool = discover_search(client)
        print('MCP 握手成功；发现搜索工具：' + tool['name'], flush=True)
        if query:
            result = parse_search_result(client.call_tool(tool['name'], search_arguments(tool, query, 72)), query)
            print('真实搜索完成：%d条候选。日期缺失的候选会在正式任务中读取页面后核对。' % len(result['articles']))
            for row in result['articles'][:5]:
                print('- ' + row['title'])
                print('  ' + row['url'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true')
    group.add_argument('--test-search', action='store_true')
    group.add_argument('--use-bing', action='store_true')
    args = parser.parse_args()
    # 明确拒绝把密钥作为命令行参数，避免密钥留在终端历史中。
    if args.check or args.test_search:
        check(load_mcp_settings(), '马德里竞技' if args.test_search else None)
    elif args.use_bing:
        try:
            settings = load_mcp_settings()
        except ValueError:
            settings = {'api_key': '', 'endpoint': DEFAULT_ENDPOINT}
        write_config('bing', settings['api_key'], settings['endpoint'])
        print('已切回 Bing。网页下一次任务生效。')
    else:
        if not sys.stdin.isatty():
            raise ValueError('请在 VS Code 的终端中运行本命令，以便安全输入密钥。')
        print('请在 https://app.tavily.com 获取 API key。不要填写 DeepSeek 密钥。')
        key = getpass.getpass('粘贴 Tavily API key（输入不显示），然后回车：').strip()
        if not key.startswith('tvly-') or len(key) < 12 or any(c.isspace() for c in key):
            raise ValueError('密钥格式不符，未保存；请复制 Tavily 控制台中的 API key。')
        settings = {'endpoint': DEFAULT_ENDPOINT, 'api_key': key}
        check(settings)  # 验证成功后才保存，不覆盖已能工作的配置。
        write_config('tavily_mcp', key)
        print('已保存本地配置，并启用 Tavily MCP。刷新工作台，提交一条新闻需求即可。')
        print('本次只做连接与工具发现，没有调用 DeepSeek 或执行付费搜索。')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    try:
        main()
    except (ValueError, OSError, KeyboardInterrupt, EOFError):
        # 详细网络错误在客户端已转为不含密钥的固定文字；不输出 traceback。
        error = sys.exc_info()[1]
        print('配置或检查未完成：' + (str(error) if isinstance(error, ValueError) else '操作取消或本地读写失败。'))
        raise SystemExit(1)
