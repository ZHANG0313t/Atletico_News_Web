"""启动器不重复起服务，且Windows批处理文件使用CRLF。"""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch
from project_paths import PROJECT_DIR

spec=importlib.util.spec_from_file_location('news_launcher',PROJECT_DIR/'tools/launch_news_station.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_existing_lan_service_shows_working_address(self):
        output=io.StringIO()
        with patch.object(launcher,'probe',return_value=True),patch.object(launcher.socket,'getaddrinfo',return_value=[(2,1,6,'',('10.237.51.98',0))]),contextlib.redirect_stdout(output):
            self.assertTrue(launcher.show_existing(lan=True))
        self.assertIn('http://10.237.51.98:8765/',output.getvalue())
        self.assertIn('不需要重复启动',output.getvalue())

    def test_local_only_service_explains_how_to_switch(self):
        output=io.StringIO()
        with patch.object(launcher,'probe',side_effect=lambda host,port:host=='127.0.0.1'),patch.object(launcher.socket,'getaddrinfo',return_value=[(2,1,6,'',('10.237.51.98',0))]),contextlib.redirect_stdout(output):
            self.assertTrue(launcher.show_existing(lan=True))
        self.assertIn('Ctrl+C',output.getvalue())
        self.assertNotIn('同网络设备访问：',output.getvalue())

    def test_no_service_allows_normal_start(self):
        with patch.object(launcher,'probe',return_value=False):
            self.assertFalse(launcher.show_existing(lan=True))

    def test_windows_launchers_are_crlf_without_bom_and_keep_window(self):
        for name in ('启动局域网新闻站.cmd','配置局域网防火墙.cmd'):
            data=(PROJECT_DIR/name).read_bytes()
            self.assertNotIn(b'\n',data.replace(b'\r\n',b''),name)
            self.assertFalse(data.startswith(b'\xef\xbb\xbf'))
            self.assertIn(b'pause',data)
