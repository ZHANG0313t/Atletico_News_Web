"""队列并发、容量、归属、重启恢复与失败隔离；全部使用模拟任务。"""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from task_queue import TaskQueue


def until(predicate, timeout=4):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('等待模拟后台任务超时')


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'jobs.sqlite3'

    def test_two_running_eight_waiting_cancel_fifo_and_one_per_user(self):
        release=threading.Event();started=[];lock=threading.Lock()
        def execute(uid,kind,payload,job,save):
            with lock:started.append(uid)
            release.wait(5)
            job.update(status='completed');save(job)
        q=TaskQueue(self.path,execute,workers=2);q.start()
        try:
            for i in range(2):q.enqueue('u%d'%i,'import',['url'])
            until(lambda:len(started)==2)
            ids=[q.enqueue('u%d'%i,'briefing',{'question':'private %d'%i}) for i in range(2,10)]
            self.assertEqual(len(started),2)
            self.assertEqual(q.status('u9')['ahead'],7)
            with self.assertRaises(RuntimeError):q.enqueue('u10','import',[])
            with self.assertRaises(RuntimeError):q.enqueue('u2','import',[])
            with self.assertRaises(RuntimeError):q.cancel('u3',ids[0])
            with self.assertRaises(RuntimeError):q.cancel('u0',q.status('u0')['id'])
            self.assertEqual(q.cancel('u2',ids[0])['status'],'cancelled')
            self.assertFalse(q.unfinished('u2'))
            q.enqueue('u10','import',[])
            self.assertNotIn('question',q.status('u9'))
            release.set()
            until(lambda:len(started)==10 and not q.unfinished('u10'))
            self.assertNotIn('u2',started)
            self.assertEqual(set(started[:2]),{'u0','u1'})
            # 两个线程开始回调的顺序可交错；SQLite认领的顺序必须是FIFO。
            with q.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE status='completed'").fetchone()[0],10)
        finally:
            release.set();q.close()

    def test_default_five_running_eight_waiting(self):
        release=threading.Event();started=[];lock=threading.Lock()
        def execute(uid,kind,payload,job,save):
            with lock:started.append(uid)
            release.wait(5)
            job.update(status='completed');save(job)
        q=TaskQueue(self.path,execute)
        self.assertEqual(q.workers,5);self.assertEqual(q.capacity,13)
        q.start()
        try:
            for i in range(5):q.enqueue('u%d'%i,'import',[])
            until(lambda:len(started)==5)
            for i in range(5,13):q.enqueue('u%d'%i,'import',[])
            self.assertEqual(len(started),5)
            self.assertEqual(q.status('u12')['ahead'],7)
            with self.assertRaisesRegex(RuntimeError,'5个执行名额和8个等待名额'):
                q.enqueue('u13','import',[])
            with self.assertRaises(RuntimeError):q.enqueue('u0','import',[])
            q.cancel('u5',q.status('u5')['id'])
            q.enqueue('u13','import',[])
            release.set()
            until(lambda:len(started)==13 and not q.unfinished('u13'))
            self.assertNotIn('u5',started)
        finally:
            release.set();q.close()

    def test_fifo_with_one_worker(self):
        seen=[]
        def execute(uid,kind,payload,job,save):
            seen.append(uid);job.update(status='completed');save(job)
        q=TaskQueue(self.path,execute,workers=1)
        try:
            for i in range(5):q.enqueue(str(i),'import',[])
            q.start();until(lambda:len(seen)==5 and not q.unfinished('4'))
            self.assertEqual(seen,list('01234'))
        finally:q.close()

    def test_restart_keeps_waiting_but_does_not_repeat_interrupted_paid_task(self):
        q=TaskQueue(self.path,lambda *args:None)
        a=q.enqueue('a','import',['old']);q.enqueue('b','import',['queued'])
        uid,kind,payload,job=q._claim()
        job['run_id']='saved-partial-report';q.save(job);q.close()
        seen=[]
        def execute(uid,kind,payload,job,save):
            seen.append(uid);job.update(status='completed');save(job)
        other=TaskQueue(self.path,execute)
        try:
            self.assertEqual(other.status('a')['status'],'interrupted')
            self.assertEqual(other.status('a')['run_id'],'saved-partial-report')
            self.assertEqual(other.status('b')['status'],'queued')
            other.start();until(lambda:seen==['b'] and not other.unfinished('b'))
        finally:other.close()

    def test_second_service_rejected_without_corrupting_running_status(self):
        q=TaskQueue(self.path,lambda *args:None)
        try:
            q.enqueue('a','import',[]);q._claim()
            with self.assertRaises(OSError):TaskQueue(self.path,lambda *args:None)
            self.assertEqual(q.status('a')['status'],'running')
        finally:q.close()

    def test_failure_does_not_block_next_job_or_leak_exception(self):
        def execute(uid,kind,payload,job,save):
            if uid=='a':raise RuntimeError('fake-private-key')
            job.update(status='completed');save(job)
        q=TaskQueue(self.path,execute,workers=1)
        try:
            q.enqueue('a','import',[]);q.enqueue('b','import',[]);q.start()
            until(lambda:q.status('b')['status']=='completed')
            self.assertEqual(q.status('a')['status'],'failed')
            self.assertNotIn('fake-private-key',str(q.status('a')))
        finally:q.close()


if __name__=='__main__':unittest.main()
