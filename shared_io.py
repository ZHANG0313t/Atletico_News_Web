"""进程内按资源加锁；临时文件写好后再原子替换正式缓存。"""
from contextlib import contextmanager
from pathlib import Path
import os
import tempfile
import threading
import time

# 固定数量的锁，避免任意链接让锁字典无限增长。RLock允许同一资源嵌套调用。
_LOCKS = [threading.RLock() for _ in range(257)]


class InstanceLock:
    """数据库旁边的进程锁，防止双击两次启动两个队列。"""
    def __init__(self, path):
        self.handle = open(path, 'a+b')
        try:
            if os.fstat(self.handle.fileno()).st_size==0:
                self.handle.write(b'0'); self.handle.flush()
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise OSError('新闻站服务已经运行，请使用已有窗口或先停止原服务。') from None

    def close(self):
        self.handle.close()


@contextmanager
def resource_lock(key):
    with _LOCKS[hash(str(key)) % len(_LOCKS)]:
        yield


def atomic_write(path, value, encoding='utf-8'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else value.encode(encoding)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(path.parent), prefix=path.name+'.', suffix='.tmp', delete=False) as handle:
            temp = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows中另一线程短暂读取文件时可能禁止替换；只重试本地改名。
        for attempt in range(10):
            try:
                os.replace(str(temp), str(path))
                break
            except PermissionError:
                if attempt==9:raise
                time.sleep(.01*(attempt+1))
    finally:
        if temp and temp.exists():
            temp.unlink()
