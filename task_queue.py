"""小型持久任务队列：五个工作人员，最多十三份未完成订单。

SQLite负责保存排队顺序，线程只负责执行；重启不会把已付费的任务再跑一次。
这里不存密钥，也不向浏览器返回其他人的问题。
"""
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from shared_io import InstanceLock

UNFINISHED = ('queued', 'running')


class TaskQueue:
    def __init__(self, path, execute, workers=5, waiting=8):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.execute = execute
        self.workers = workers
        self.waiting = waiting
        self.capacity = workers + waiting
        self.condition = threading.Condition(threading.RLock())
        self.stopping = False
        self.threads = []
        self.instance = InstanceLock(str(self.path)+'.lock')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_per_user ON jobs(user_id)
                WHERE status IN ('queued','running');
                CREATE INDEX IF NOT EXISTS user_history ON jobs(user_id, seq);''')
            for row in db.execute("SELECT id,state FROM jobs WHERE status='running'").fetchall():
                state = json.loads(row['state'])
                state.update(status='interrupted', error='服务上次停止时任务尚未完成，已保留现有报告；没有自动重跑付费请求。')
                db.execute('UPDATE jobs SET status=?,state=? WHERE id=?',
                           ('interrupted', json.dumps(state, ensure_ascii=False), row['id']))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self):
        with self.condition:
            if self.threads:
                return
            for index in range(self.workers):
                thread = threading.Thread(target=self._worker, name='news-worker-%d' % index, daemon=True)
                self.threads.append(thread)
                thread.start()

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        # 正在进行的外部请求不强行打断；服务重启会将残留running标为中断。
        for thread in self.threads:
            thread.join(timeout=.5)
        if not any(thread.is_alive() for thread in self.threads):
            self.instance.close()

    def enqueue(self, uid, kind, payload):
        with self.condition, self.connect() as db:
            if self.stopping:
                raise RuntimeError('服务正在停止，请稍后再试。')
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE user_id=? AND status IN ('queued','running')", (uid,)).fetchone():
                raise RuntimeError('你的账号已有排队或运行中的任务，请等待完成，或先取消排队。')
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.capacity:
                raise RuntimeError('当前已有%d个执行名额和%d个等待名额被占用，请稍后再试。' % (self.workers,self.waiting))
            job = dict(id=secrets.token_hex(16), status='queued', run_id=None, record=None,
                       messages=['任务已排队，轮到时才会调用搜索和模型。'], error=None)
            db.execute('INSERT INTO jobs(id,user_id,kind,payload,status,state,created) VALUES(?,?,?,?,?,?,?)',
                       (job['id'], uid, kind, json.dumps(payload, ensure_ascii=False), 'queued',
                        json.dumps(job, ensure_ascii=False), time.time()))
            # 提交事务后再叫醒工作人员，避免他们读取到尚未提交的订单。
        with self.condition:
            self.condition.notify_all()
        return job['id']

    def save(self, job):
        with self.condition, self.connect() as db:
            db.execute("UPDATE jobs SET status=?,state=? WHERE id=? AND status='running'",
                       (job['status'], json.dumps(job, ensure_ascii=False), job['id']))
            self.condition.notify_all()

    def status(self, uid):
        with self.condition, self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY seq DESC LIMIT 1', (uid,)).fetchone()
            if not row:
                return None
            job = json.loads(row['state'])
            if row['status'] == 'queued':
                job['ahead'] = db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued' AND seq<?", (row['seq'],)).fetchone()[0]
                job['messages'] = ['等待执行名额；前面还有%d个排队任务，轮到后自动开始。' % job['ahead']]
            return job

    def unfinished(self, uid):
        with self.condition, self.connect() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE user_id=? AND status IN ('queued','running')", (uid,)).fetchone())

    def cancel(self, uid, job_id):
        with self.condition, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE user_id=? AND id=?', (uid, job_id)).fetchone()
            if not row or row['status'] != 'queued':
                raise RuntimeError('只能取消自己的排队任务；任务可能已开始，请刷新查看。')
            job = json.loads(row['state'])
            job.update(status='cancelled', messages=['已取消排队，没有执行搜索或模型请求。'])
            db.execute("UPDATE jobs SET status='cancelled',state=? WHERE id=?", (json.dumps(job, ensure_ascii=False), job_id))
        return job

    def _claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if row is None:
                return None
            job = json.loads(row['state'])
            job.update(status='running', messages=['已轮到你的任务，正在准备新闻资料……'])
            db.execute("UPDATE jobs SET status='running',state=? WHERE id=?", (json.dumps(job, ensure_ascii=False), row['id']))
            return row['user_id'], row['kind'], json.loads(row['payload']), job

    def _worker(self):
        while True:
            with self.condition:
                if self.stopping:
                    return
                item = self._claim()
                if item is None:
                    self.condition.wait(timeout=2)
                    continue
            uid, kind, payload, job = item
            try:
                self.execute(uid, kind, payload, job, self.save)
            except Exception:
                # 不把异常原文、用户问题或密钥写到其他用户可见的响应。
                job.update(status='failed', error='后台任务未能完成，已有报告仍保留。')
                self.save(job)
