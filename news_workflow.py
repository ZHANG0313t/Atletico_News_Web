"""第十课：一次提交新闻链接，输出按事件整理的报告。

python news_workflow.py  → 逐行粘贴网址，空行结束。
固定流程负责把步骤走完；模型继续负责事件归属和内容比较。
不自动重试网络请求，不标记用户已读；归档结果会保存。
"""
import argparse
import copy
import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
import time
from app_support import safe_error
from llm_client import load_settings
from event_archive import classify_article
from event_updates import prepare_materials, compare_prepared, NoBaselineError
from fetch_news import fetch_news
from news_urls import normalize_news_url
from project_paths import OUTPUT_DIR
from app_support import now_text

MAX_LINKS = 5
from app_support import REPORT_LOCK


def text(value):
    """正文、标题可能带Markdown符号；转义，避免破坏本地报告排版。"""
    return str(value).replace('\\', '\\\\').replace('`', '\\`').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')


def render_report(record):
    """直接用已验证的结构化结果生成报告，不再花一次API做二次改写。"""
    rows = record['articles']
    lines = ['# 足球新闻阅读报告', '', '生成时间：' + record['started_at'],
             '任务状态：' + record['status'], '',
             '比较范围：目标与本次比较快照中同事件的其他归档文章。参照可能包含本批次文章，且不要求早于目标或已读。',
             '内容属于模型整理；多写的信息不等于新发生、独立确认或转会已推进。没有自动修改已读标记。', '']
    success = sum(row.get('comparison_status') == 'completed' for row in rows)
    lines += [f'本次条目（不含重复）：{len(rows)}；已完成比较：{success}；已忽略重复链接：{len(record["duplicates"])}。', '']
    # 优先核对的原因是具体的，不用模糊的“重要性评分”替用户决定兴趣。
    lines += ['## 可优先核对', '']
    attention = []
    for row in rows:
        comparison = row.get('comparison', {}).get('comparison', {})
        if row.get('error'):
            attention.append(f'- {text(row["url"])}：{text(row["error"])}')
        elif row.get('classification_status') == 'uncertain':
            attention.append(f'- {text(row.get("title",row["url"]))}：归属不确定，需要检查主事件。')
        elif comparison.get('differences'):
            attention.append(f'- {text(row["article_id"])}：有同一问题的表述差异，建议核对原文。')
        elif comparison.get('added_in_target'):
            attention.append(f'- {text(row["article_id"])}：模型列出了相对本次参照多写的信息。')
        elif row.get('comparison_status') == 'no_baseline':
            attention.append(f'- {text(row["article_id"])}：这个事件暂无其他参照，不能判定新增。')
        elif comparison.get('comparable') is False:
            attention.append(f'- {text(row["article_id"])}：正文不适合直接比较，建议检查归档。')
    lines += attention or ['本次未列出需要优先核对的新增、差异或失败项；不代表已穷尽全部细节。']
    # 按事件组织，读失败/归属不确定的条目另列，保留每篇独立状态。
    groups = {}
    for row in rows:
        groups.setdefault(row.get('event_id'), []).append(row)
    for event_id, members in groups.items():
        event = members[0].get('event', {})
        lines += ['', '## ' + text(event.get('title', '尚未进入事件档案'))]
        if event_id:
            lines += ['事件编号：' + text(event_id), '范围：' + text(event.get('question', ''))]
        for row in members:
            lines += ['', '### ' + text(row.get('title', row['url'])),
                      '链接：' + row['url'], '文章编号：' + text(row.get('article_id','未取得'))]
            if row.get('source_status'):
                lines += ['读取：' + ('复用本地缓存，本次没有联网更新' if row['source_status']=='local_cache' else '本次下载网页')]
            if row.get('warning'): lines += ['提取提示：' + text(row['warning'])]
            if row.get('classification'):
                c = row['classification']; assignment = c.get('assignment', {})
                lines += ['归档状态：' + text(c['status']) + '；归属来源：' + text(assignment.get('origin','未归档'))]
                if assignment.get('reason'): lines += ['归档理由：' + text(assignment['reason'])]
                if assignment.get('evidence'): lines += ['归档依据：' + text(assignment['evidence'])]
                if c.get('status')=='uncertain': lines += ['暂未归档：' + text(c.get('proposal',{}).get('reason',''))]
            if row.get('error'): lines += ['未完成阶段：' + text(row.get('failed_stage','')), '原因：' + text(row['error'])]
            status = row.get('comparison_status')
            if status=='no_baseline': lines += ['比较：该事件暂无其他归档文章，未调用比较模型。']
            elif status=='skipped': lines += ['比较：尚无确定归属，未强行比较。']
            elif status=='completed':
                result = row['comparison']; comparison = result['comparison']
                lines += ['参照编号：' + '、'.join(result['baseline_article_ids']),
                          '快照时间：' + result['snapshot_at'],
                          '能否比较：' + ('是' if comparison['comparable'] else '否'),
                          '比较理由：' + text(comparison['reason'])]
                for key, heading in [('overlap','重合的说法'),('added_in_target','相对这些参照多写的信息'),('differences','同一问题的表述差异')]:
                    lines += ['', '**' + heading + '**']
                    if not comparison[key]: lines += ['未列出。' if comparison['comparable'] else '不适用。']
                    for item in comparison[key]:
                        lines += ['- ' + text(item['statement']), '  - 目标摘录：' + text(item['evidence_target'])]
                        for evidence in item.get('evidence_baseline',[]):
                            lines += ['  - 参照 ' + text(evidence['article_id']) + '：' + text(evidence['quote'])]
                        if key=='differences':
                            lines += ['  - 模型是否标为矛盾：' + ('是，需人工核对' if item['is_contradiction'] else '否')]
    if record['duplicates']:
        lines += ['', '## 忽略的重复链接', ''] + ['- ' + text(url) for url in record['duplicates']]
    lines += ['', '## 本次用量与边界', '',
              f'归档请求：{record["requests"]["classification"]}；比较请求：{record["requests"]["comparison"]}。失败的已发送请求也可能计费。',
              '一次最多五条输入、五次归档请求和五次比较请求；不自动重试。已有归属复用，但重新运行通常会再次付费比较。',
              '归档和成功下载会保留，即使后续比较失败也不回滚。超出五篇参照或材料过大时明确失败，不偷偷删减参照。',
              '报告仅覆盖本次提交目标；事件档案可能还含其他文章，不是完整日报。']
    return '\n'.join(lines) + '\n'


def save_progress(record, folder):
    """每个阶段完成都存盘；临时文件写完后替换，尽量保留完整的旧进度。"""
    with REPORT_LOCK:
        for name, content in [('trace.json', json.dumps(record, ensure_ascii=False, indent=2)),
                              ('report.md', render_report(record))]:
            path = folder / name
            temporary = folder / (name + '.tmp')
            temporary.write_text(content, encoding='utf-8')
            for attempt in range(4):
                try:
                    os.replace(str(temporary), str(path))
                    break
                except PermissionError:
                    if attempt==3: raise
                    time.sleep(.04)  # A short file-operation retry, never an API retry.



def run_workflow(urls, api_key, model, output_dir=OUTPUT_DIR, progress=print, on_snapshot=None):
    """两阶段流程：先全部读取/归档，再全部比较。参数接口也能供以后界面使用。"""
    if not urls or len(urls) > MAX_LINKS:
        raise ValueError('请一次提交1到5条新闻链接。')
    folder = Path(output_dir) / ('workflow_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True, exist_ok=False)
    record = {'status':'running', 'started_at':now_text(), 'model':model,
              'articles':[], 'duplicates':[], 'requests':{'classification':0,'comparison':0}, 'usage':[]}
    seen = set()
    # 无效链接作为独立失败条目记录，不影响其他有效链接。
    for url in urls:
        row = {'url':url, 'stage':'pending'}
        try:
            canonical, article_id = normalize_news_url(url)
            if article_id in seen:
                record['duplicates'].append(url)
                continue
            seen.add(article_id)
            row.update(url=canonical, article_id=article_id)
        except ValueError as error:
            row.update(stage='failed', failed_stage='validate_url', error=safe_error(error))
        record['articles'].append(row)
    def save():
        save_progress(record, folder)
        # 网页只订阅进度，不改变读取、归档与比较逻辑；旧命令行不传回调即可。
        if on_snapshot is not None:
            on_snapshot(copy.deepcopy(record), folder)
    save()
    progress('本次报告目录：' + str(folder))
    try:
        # 阶段一：读到一篇即保存归属。某条出错，继续下一条，不自动重试。
        for index, row in enumerate(record['articles'],1):
            if row['stage']=='failed': continue
            stage='fetch'
            try:
                row['stage']=stage; save()
                progress(f'[{index}/{len(record["articles"])}] 读取：{row["url"]}')
                article=fetch_news(row['url'])
                from news_storage import format_article
                article_path=Path(output_dir)/(article['article_id']+'.txt')
                if not article_path.exists():article_path.write_text(format_article(article),encoding='utf-8')
                row.update(title=article['title'], article_id=article['article_id'],
                           source_status=article['source_status'], extraction_method=article.get('extraction_method'))
                if article.get('extraction_method')=='generic:heuristic-review-needed' or str(article.get('extraction_method','')).startswith('unknown'):
                    row['warning']='正文使用通用或未知提取方式，请核对完整性。'
                if article.get('reextracted_from_saved_html'):
                    row['warning']='已从本地HTML修正旧标题和正文范围，本次没有联网；旧TXT已备份。'
                stage='classify'; row['stage']=stage; save()
                progress('  正在确认主事件；已有归属则复用。')
                # 回调只在底层真正准备发起归档请求时触发；已有归属不计费。
                def counted_classify():
                    record['requests']['classification']+=1
                    save()
                classified=classify_article(row['article_id'],api_key,model,
                                            output_dir=output_dir,on_request=counted_classify)
                row['classification']=classified; row['classification_status']=classified['status']
                if classified.get('usage'): record['usage'].append({'kind':'classification','usage':classified['usage']})
                if classified['status']=='uncertain':
                    row.update(stage='needs_review', comparison_status='skipped')
                else:
                    row.update(stage='archived',event_id=classified['event_id'],event=classified['event'])
            except (ValueError,OSError) as error:
                row.update(stage='failed', failed_stage=stage,error=safe_error(error))
                progress('  此条未完成：' + row['error'])
            save()
        # 阶段二：全部候选已经有机会进入档案，才冻结每篇的比较材料。
        for row in record['articles']:
            if row['stage']!='archived': continue
            stage='prepare_comparison'
            try:
                row['stage']=stage; save()
                progress('比较：' + row['article_id'])
                materials=prepare_materials(row['article_id'],output_dir=output_dir)
                stage='compare'; row['stage']=stage
                record['requests']['comparison']+=1; save()
                result=compare_prepared(materials,api_key,model)
                row.update(stage='completed',comparison_status='completed',comparison=result)
                record['usage'].append({'kind':'comparison','usage':result.get('usage',{})})
            except NoBaselineError:
                row.update(stage='completed',comparison_status='no_baseline')
            except (ValueError,OSError) as error:
                row.update(stage='failed',failed_stage=stage,error=safe_error(error))
                progress('  比较未完成：' + row['error'])
            save()
        failed=any(row['stage']=='failed' for row in record['articles'])
        pending=any(row['stage']=='needs_review' for row in record['articles'])
        record['status']='partial' if failed or pending else 'completed'
        record['finished_at']=now_text(); save()
    except BaseException:
        # Ctrl+C也保留已下载/已归档和当前阶段；异常原样抛出，不当作完成。
        record['status']='interrupted'; save()
        raise
    return record, folder


def main():
    parser=argparse.ArgumentParser(description='读取多条足球新闻，归档并生成事件阅读报告（会保存归属、产生API用量）。')
    parser.add_argument('urls',nargs='*',help='1到5条具体新闻网址；不传参数则逐行输入')
    args=parser.parse_args()
    urls=args.urls
    if not urls:
        print('逐行粘贴新闻链接，空行开始处理；一次最多5条。',flush=True)
        while True:
            value=input('链接> ').strip()
            if not value: break
            urls.append(value)
            if len(urls)>MAX_LINKS: raise ValueError('一次最多5条链接，请减少输入。')
    if not urls or len(urls)>MAX_LINKS: raise ValueError('请提交1到5条链接。')
    api_key,model=load_settings()
    print('开始处理：新归档最多5次、比较最多5次API请求；不自动重试。',flush=True)
    record,folder=run_workflow(urls,api_key,model,progress=lambda message:print(message,flush=True))
    print('\n'+render_report(record),flush=True)
    print('报告：',folder/'report.md',flush=True)
    print('过程：',folder/'trace.json',flush=True)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace',line_buffering=True)
    try: main()
    except (ValueError,OSError,configparser.Error) as error:
        print('流程未完成：',safe_error(error),flush=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('已停止；已保存的文章、归属和进度保留在output/workflow_本次时间中。',flush=True)
        raise SystemExit(130)
