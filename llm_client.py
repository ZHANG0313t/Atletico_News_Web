"""大脑连接器：读取本机 DeepSeek 配置，发送结构化整理请求。

不带单篇演示入口；各业务模块自己提供提示词。MCP 工具决策在 mcp_search.py。
"""
import configparser
import json
from urllib.request import Request, urlopen
from request_limits import limited_open
from project_paths import PROJECT_DIR

API_URL = 'https://api.deepseek.com/chat/completions'

def load_settings():
    """读取密钥与模型名；不把密钥打印或写进结果。"""
    config_path = PROJECT_DIR / "local_config.ini"
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path, encoding="utf-8-sig")
    api_key = config.get("deepseek", "api_key", fallback="").strip()
    model = config.get("deepseek", "model", fallback="deepseek-flash").strip()
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        raise ValueError(f"请先在 {config_path} 中填写 api_key，不用发到聊天里。")
    if not model:
        raise ValueError("配置中的 model 不能为空。")
    return api_key, model  # return 可以同时返回两个值

def call_deepseek(news_text, api_key, model, system_prompt, max_tokens=2048):
    """返回模型结果和用量。仅明确的HTTP 429最多补试两次，不重试超时。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请整理以下文章资料：\n\n" + news_text},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": max_tokens,  # 限制模型输出长度；token 不完全等同于汉字
        "stream": False,  # 一次取得完整结果，先不处理逐字输出
    }
    # Python字典 → JSON字符串 → UTF-8字节，才能通过网络发送。
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    with limited_open('deepseek', urlopen, request, timeout=90) as response:
        api_response = json.loads(response.read().decode("utf-8"))

    # HTTP成功不等于结果可用：还要检查是否被截断、是否为空、是否符合JSON格式。
    try:
        choice = api_response["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("模型未正常完成，可能输出被截断。本次结果不保存。")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("模型返回了空内容。本次结果不保存。")
        result = json.loads(content)
    except (KeyError, IndexError, TypeError):
        raise ValueError("接口返回结构与预期不符，请核对官方接口和模型配置。") from None
    return result, api_response.get("usage", {})
