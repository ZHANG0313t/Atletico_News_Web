"""报告短标题：优先复用规划模型生成的标题，旧报告用已有资料补齐，不额外调用API。"""
import re


def clean_title(value,limit=26):
    if not isinstance(value,str):return ''
    value=re.sub(r'\s+',' ',value).strip(' \"“”')
    return value if len(value)<=limit else value[:limit-1]+'…'


def report_title(record):
    plan=record.get('plan') or {}
    for value in (record.get('title'),plan.get('title')):
        if isinstance(value,str) and value.strip():return clean_title(value)
    question=record.get('question') or ''
    if record.get('type')=='briefing' or plan.get('team'):
        team=plan.get('team') or record.get('team') or '球队'
        if team=='马德里竞技':team='马竞'
        hours=plan.get('hours') or (record.get('window') or {}).get('hours') or record.get('requested_hours',24)
        period={24:'近一天',72:'近三天',168:'近一周'}.get(hours,'近期')
        topic='新闻'
        for word,label in [('转会','转会动态'),('伤病','伤病动态'),('训练','训练动态'),('采访','采访整理'),('赛后','赛后消息'),('比赛','比赛消息')]:
            if word in question:topic=label;break
        return clean_title(team+period+topic)
    if question:
        question=re.sub(r'^(请你|请|帮我|帮忙|麻烦)(看看|整理|汇总|分析|了解)?','',question).strip(' ：，。?？')
        return clean_title(question)
    rows=record.get('articles') or []
    for row in rows:
        value=(row.get('event') or {}).get('title') or row.get('title')
        if value:return clean_title(value)
    return '新闻链接导入'