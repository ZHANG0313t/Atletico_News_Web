"""给原文片段编号，让模型选择编号，由Python取回真正的原句。

不做模糊匹配，不把改写的句子当原文。保留原来的逐字摘录兼容旧测试和结果。
"""
import copy
import re


def segment_article(article, prefix):
    result=copy.deepcopy(article)
    body=result.pop('body')
    pieces=[]
    # 按句末/换行切开；很长的句子再切成不超过140字的连续片段。
    for paragraph in body.splitlines():
        for sentence in re.findall(r'[^。！？!?]+[。！？!?]?|[。！？!?]',paragraph):
            for start in range(0,len(sentence),140):
                piece=sentence[start:start+140].strip()
                if piece: pieces.append(piece)
    catalog={f'{prefix}{i:04d}':piece for i,piece in enumerate(pieces,1)}
    result['body_segments']=[{'id':key,'text':value} for key,value in catalog.items()]
    return result,catalog


def resolve_quote(value,catalog):
    if isinstance(value,str) and value in catalog: return catalog[value]
    # 未知编号或改写文本原样交给已有严格检查，绝不“找个近似句”替换。
    return value


def require_text(value, label):
    """把重复的文字检查写成小函数，方便复用。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"模型返回的{label}为空或类型不正确。")

def check_evidence(quote, body, label):
    """证据必须在正确的那篇正文中出现。A的摘录不能拿B的句子冒充。"""
    require_text(quote, label)
    if len(quote) > 220 or "".join(quote.split()) not in "".join(body.split()):
        raise ValueError(f"{label}过长或无法在对应正文中找到，本次不保存结果。")
