"""账号与会话：SQLite、加盐密码哈希、可撤销会话；不保存明文密码。

用户名仅用于显示，用户文件夹使用随机编号，避免用户名变成文件路径。
此模块不调用模型，账号资料不会进入新闻提示词或报告。
"""
from contextlib import contextmanager
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import unicodedata

ITERATIONS=310000
SESSION_SECONDS=12*60*60


def username(value):
    if not isinstance(value,str):raise ValueError('请输入账号名称。')
    value=unicodedata.normalize('NFKC',value).strip()
    if not 1<=len(value)<=32 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('账号名称须为1至32字，不能包含控制字符。')
    return value


def password_record(password):
    if not isinstance(password,str) or not 8<=len(password)<=128:
        raise ValueError('密码长度须为8至128个字符。')
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS)
    return salt.hex(),digest.hex()


def public_user(row):
    return {'id':row['id'],'username':row['username'],'is_admin':bool(row['is_admin'])}


class AuthStore:
    def __init__(self,path):
        self.path=path;self.path.parent.mkdir(parents=True,exist_ok=True)
        self.attempts={};self.lock=threading.RLock()
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY, username TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL, digest TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf TEXT NOT NULL, expires REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=10);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    def owner(self):
        with self.connect() as db:row=db.execute('SELECT * FROM users WHERE is_admin=1 ORDER BY created_at LIMIT 1').fetchone()
        return public_user(row) if row else None

    def bootstrap_hash(self,name,salt,digest):
        """一次性导入主账号的哈希；部署工具不需要把明文密码写进源文件。"""
        name=username(name)
        if len(bytes.fromhex(salt))!=16 or len(bytes.fromhex(digest))!=32:raise ValueError('初始化哈希格式无效。')
        with self.connect() as db:
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:raise ValueError('账号库已有用户，未覆盖。')
            uid=secrets.token_hex(16)
            db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(uid,name,name.casefold(),salt,digest,1,time.time()))
        return uid

    def create(self,name,password):
        name=username(name);salt,digest=password_record(password);uid=secrets.token_hex(16)
        try:
            with self.connect() as db:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(uid,name,name.casefold(),salt,digest,0,time.time()))
        except sqlite3.IntegrityError:raise ValueError('这个账号名称已存在。') from None
        return {'id':uid,'username':name,'is_admin':False}

    def verify(self,uid,password):
        with self.connect() as db:row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return self._matches(row,password)

    def _matches(self,row,password):
        if not isinstance(password,str) or len(password)>128:return False
        salt=bytes.fromhex(row['salt']) if row else bytes(16)
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS).hex()
        return bool(row and hmac.compare_digest(actual,row['digest']))

    def login(self,name,password,address):
        name=username(name);key=(address,name.casefold());now=time.time()
        with self.lock:
            recent=[t for t in self.attempts.get(key,[]) if now-t<60]
            if len(recent)>=6:raise ValueError('尝试过于频繁，请一分钟后再试。')
            self.attempts[key]=recent+[now]
            if len(self.attempts)>1000:self.attempts={k:v for k,v in self.attempts.items() if v and now-v[-1]<60}
        with self.connect() as db:
            row=db.execute('SELECT * FROM users WHERE name_key=?',(name.casefold(),)).fetchone()
            if not self._matches(row,password):raise ValueError('账号或密码不正确。')
            token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?',(now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(self.token_hash(token),row['id'],csrf,now+SESSION_SECONDS))
        with self.lock:self.attempts.pop(key,None)
        return token,{'user':public_user(row),'csrf':csrf}

    @staticmethod
    def token_hash(token):return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def session(self,token):
        if not isinstance(token,str) or not 20<=len(token)<=100:return None
        with self.connect() as db:
            row=db.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?',
                           (self.token_hash(token),time.time())).fetchone()
        return {'user':public_user(row),'csrf':row['csrf']} if row else None

    def logout(self,token):
        if token:
            with self.connect() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(self.token_hash(token),))

    def users(self):
        with self.connect() as db:rows=db.execute('SELECT * FROM users ORDER BY is_admin DESC,created_at').fetchall()
        return [public_user(row) for row in rows]

    def change_password(self,uid,password):
        salt,digest=password_record(password)
        with self.connect() as db:
            cursor=db.execute('UPDATE users SET salt=?,digest=? WHERE id=?',(salt,digest,uid))
            if not cursor.rowcount:raise ValueError('账号不存在。')
            db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))

    def delete(self,uid):
        with self.connect() as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not row:raise ValueError('账号不存在。')
            if row['is_admin']:raise ValueError('主账号不能注销，可修改密码。')
            db.execute('DELETE FROM users WHERE id=?',(uid,))
