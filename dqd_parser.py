"""懂球帝专用解析器，属于正文提取工具；从第一课提取，保留中文批注。"""
from html.parser import HTMLParser

class NewsParser(HTMLParser):
    """根据本次实际查看到的懂球帝网页结构提取文字。"""

    # 网页给不同区域起了 class 名称。这里把它们映射成我们自己的字段。
    FIELD_CLASSES = {
        "article-head__title": "title",
        "article-head__author-name": "author",
        "article-head__author-sub": "published_at",
        "article-body": "body",
    }
    # 这些标签没有结束标签，例如 <img>。它们不能压入等待关闭的栈。
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}
    BLOCK_TAGS = {"p", "div", "section", "h2", "h3", "li", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)  # 自动把 &amp; 等实体还原成文字
        self.parts = {field: [] for field in self.FIELD_CLASSES.values()}
        self.stack = []  # 记录当前标签位置及它所属的字段

    def handle_starttag(self, tag, attrs):
        """遇到开始标签时，HTMLParser 会自动调用这个方法。"""
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        # 子标签默认继承父标签的字段；script/style 中的文字不应进入正文。
        field = self.stack[-1][1] if self.stack else None
        for class_name in classes:
            if class_name in self.FIELD_CLASSES:
                field = self.FIELD_CLASSES[class_name]
        # 这篇文章正文里夹有方案推广链接；只跳过这个已确认的推广入口。
        # 普通新闻引用链接仍然保留，不能把所有链接文字都删掉。
        is_promotion = tag == "a" and attributes.get("href", "").startswith(
            "dongqiudi:///url/https://n.dongqiudi.com/webapp/plan.html"
        )
        if tag in {"script", "style"} or is_promotion:
            field = None
        if field and (tag in self.BLOCK_TAGS or tag == "br"):
            self.parts[field].append("\n")
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, field))

    def handle_endtag(self, tag):
        """遇到结束标签时，退出对应区域。"""
        # 从内向外寻找匹配标签，也能容忍部分没有规范闭合的 HTML。
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                field = self.stack[index][1]
                if field and tag in self.BLOCK_TAGS:
                    self.parts[field].append("\n")
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        """处理 <br /> 这样的自闭合标签。"""
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        """遇到普通文字时，保留目标区域里的内容。"""
        field = self.stack[-1][1] if self.stack else None
        if field:
            self.parts[field].append(data)

    def get_article(self):
        """把分散的文字片段拼好，并清除空白行。"""
        article = {}
        for field, pieces in self.parts.items():
            text = "".join(pieces)
            # strip() 清除两端空白，splitlines() 按行拆开，join() 再拼起来。
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            article[field] = "\n\n".join(lines)
        return article
