"""公共保障：安全错误说明、北京时间、报告读写锁，不负责模型决策。"""
import configparser
from datetime import datetime, timedelta, timezone
import threading
from urllib.error import HTTPError, URLError

# 网页读报告与后台写报告共享同一把锁，避免读到写了一半的文件。
REPORT_LOCK = threading.RLock()

def safe_error(error):
    """只保留简短错误，不打印网络请求头、密钥或原始接口响应。"""
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}，请检查密钥、余额、模型参数或接口状态。"
    if isinstance(error, URLError):
        return "网络连接失败，请检查网络、代理和证书。"
    if isinstance(error, configparser.Error):
        return "本地配置格式错误。"
    if isinstance(error, OSError):
        return "文件读写失败或网络超时，请检查本地文件及网络。"
    return str(error)

def now_text():
    """记录明确带北京时间时区的时间，和文章发布时间分开。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
