# Atletico新闻站

一个从零搭建的足球新闻 Agent 学习项目，使用 Python、DeepSeek API 和 Tavily MCP。
支持切换球队，搜集新闻、按类别与发布时间整理，并生成带来源链接的整体综述。

## 已实现功能

- 自然语言新闻搜索、正文提取、分类、新闻时间线与整体摘要。
- 独立账号与个人报告历史；管理员创建和管理账号。
- 最近赛果与未来赛程，球队队徽、赛季数据、当前阵容和球员照片。
- 拖拽自定义战术板，直线/曲线箭头、线条、区域、文字与JPG导出。
- 本机和局域网访问；5个新闻任务同时执行，8个等待名额。
- 每个账号最多1个未完成任务；等待可取消，队列状态持久保存。

## 环境

Python 3.9及以上。进入本仓库目录后安装依赖：

```powershell
python -m pip install -r requirements.txt
```

## 首次运行

仓库不包含作者的API密钥、账号数据库、历史报告或缓存。下载后需要自行配置。

1. 将 `local_config.example.ini` 复制为 `local_config.ini`，填写自己的DeepSeek API密钥和已确认可用的模型名称。不要提交真实配置。
2. 运行 `python mcp_setup.py`，配置自己的Tavily MCP服务；检查连接可用 `python mcp_setup.py --check`。搜索和模型调用可能消耗各自服务额度。
3. 运行下面的脚本，在终端创建自己的管理员账号。没有预设通用账号密码。

```powershell
python tools/initialize_account.py
```

4. 启动网站：

```powershell
python -B -u web_app.py
```

打开 http://127.0.0.1:8765/，使用刚创建的账号登录。

## 局域网模式

```powershell
python -B -u web_app.py --lan
```

其他设备使用终端打印的局域网地址。主机需要持续运行，网络需要允许设备互访，防火墙需要允许相应端口。换网络后需要重启服务以更新地址。校园网可能限制设备互访。

Windows `.cmd` 启动脚本会优先查找原开发机的 `E:\Anaconda3\python.exe`，否则使用PATH中的Python。防火墙脚本也针对原开发机的Python路径和WLAN网卡；其他电脑请先调整，或自行配置系统防火墙。通用启动方式是上面的Python命令。

## 代码结构

| 文件 | 职责 |
|---|---|
| `web_app.py`、`web/` | 网站接口和页面 |
| `news_briefing.py`、`news_overview.py` | 新闻整理与整体综述 |
| `mcp_search.py`、`mcp_client.py` | 搜索决策和MCP通信 |
| `llm_client.py` | DeepSeek结构化调用 |
| `fetch_news.py` | 文章下载与正文提取 |
| `user_accounts.py`、`account_web.py` | 账号、会话与个人数据隔离 |
| `task_queue.py`、`request_limits.py` | 任务排队与接口请求限速 |
| `team_fixtures.py`、`team_profile.py` | 赛程、球队和球员数据 |
| `tactics_board.py`、`web/tactics*` | 战术板、绘图与图片导出 |

更多实现说明见 [代码地图](docs/CODE_MAP.md)、[MCP说明](docs/MCP_SETUP.md)和 [历史学习记录](docs/history/PROJECT_PLAN.md)。历史文档用于回顾课程，旧命令以当前README为准。原开发机专属的备份、部署清单、网络检查记录没有放入仓库。

## 验证

```powershell
python -B -m unittest discover -s tests -p "test_*.py"
```

Python测试使用临时数据和模拟接口，不发起付费模型请求。浏览器回归脚本保留了原开发机的路径和运行环境配置，迁移到其他电脑前需调整；不属于运行网站所需依赖。

## 数据说明

新闻与统计由第三方网站提供，可能变更或暂时无法访问；界面保留来源与更新时间。新闻摘要由模型整理，需要结合原文判断。背景图与俱乐部/球员图像用于个人学习展示，第三方素材不因进入本仓库而变更权利归属。

上传源码不会自动部署Python网站。本项目当前面向本机及少量熟人的局域网使用。
