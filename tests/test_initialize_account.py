"""纯源码下载后，可以创建自己的管理员，并防止再次初始化覆盖账号。"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from project_paths import PROJECT_DIR
from user_accounts import AuthStore
from web_app import Workbench,make_server

spec=importlib.util.spec_from_file_location('initialize_account',PROJECT_DIR/'tools/initialize_account.py')
initializer=importlib.util.module_from_spec(spec);spec.loader.exec_module(initializer)


class InitializeTests(unittest.TestCase):
    def test_clean_download_can_initialize_log_in_and_construct_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            owner=initializer.initialize('My own admin','FixturePassword123',root)
            store=AuthStore(root/'data/accounts.sqlite3')
            self.assertTrue(owner['is_admin'])
            self.assertTrue((root/'output').is_dir())
            token,session=store.login('My own admin','FixturePassword123','127.0.0.1')
            self.assertEqual(session['user']['id'],owner['id'])
            server=make_server(0,Workbench(root/'output'),store)
            server.server_close()

    def test_reinitialization_does_not_replace_existing_password(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            owner=initializer.initialize('Owner','FixturePassword123',root)
            with self.assertRaises(ValueError):initializer.initialize('Other','NewFixturePassword123',root)
            store=AuthStore(root/'data/accounts.sqlite3')
            self.assertEqual(len(store.users()),1)
            self.assertTrue(store.verify(owner['id'],'FixturePassword123'))


if __name__=='__main__':unittest.main()
