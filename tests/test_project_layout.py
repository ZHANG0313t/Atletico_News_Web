"""防止清理后遗留孤立业务模块或对已退休教学脚本的导入。"""
import ast
import unittest
from project_paths import PROJECT_DIR


class ProjectLayoutTests(unittest.TestCase):
    def test_every_business_module_is_reachable_from_current_entries(self):
        # 用户另外保存的简历生成工具不属于新闻站的业务模块，仍保留原文件。
        modules={p.stem:p for p in PROJECT_DIR.glob('*.py') if p.stem!='generate_resume_pdf'}
        reached=set()
        pending=['web_app','mcp_setup']
        retired={'agent','agent_tools','read_news','analyze_news','compare_news','reading_history'}
        while pending:
            name=pending.pop()
            if name in reached or name not in modules:
                continue
            reached.add(name)
            tree=ast.parse(modules[name].read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                imports=[]
                if isinstance(node,ast.ImportFrom) and node.module:
                    imports=[node.module.split('.')[0]]
                elif isinstance(node,ast.Import):
                    imports=[alias.name.split('.')[0] for alias in node.names]
                self.assertFalse(retired.intersection(imports),(name,imports))
                pending.extend(imports)
        self.assertEqual(set(modules),reached,'留下的业务模块应从当前入口实际可达')


if __name__=='__main__':
    unittest.main()
