<p align="center">
  <img src="assets/logo-300.png" alt="ClawSoul" width="160">
</p>

<h1 align="center">ClawSoul 🐾💕</h1>

<p align="center">
  <strong>你的虚拟 AI 女友 — 基于 Telegram，拥有记忆、RAG、技能系统</strong>
</p>

<p align="center">
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/ericwang915/ClawSoul" alt="MIT License">
  </a>
  <img src="https://img.shields.io/pypi/pyversions/claw_soul" alt="Python">
</p>

---

## ✨ 特点

| | Feature | Details |
|---|---------|---------|
| 💕 | **虚拟女友人设** | 温柔体贴、俏皮可爱的 AI 女友 |
| 🧠 | **多模型支持** | DeepSeek, Grok, Claude, Gemini, Kimi, GLM |
| 💾 | **持久记忆** | Markdown 长期记忆 + 每日日志 |
| 🔍 | **混合 RAG** | BM25 + 向量检索 + RRF 融合 + LLM 重排 |
| 🌐 | **Web 仪表盘** | 浏览器 UI 可聊天、配置、管理技能 |
| 📱 | **Telegram** | Telegram Bot 接入，随时聊天 |
| ⏰ | **定时任务** | 定时问候、提醒、主动关心 |
| 🔄 | **后台守护** | PID 管理，`start` / `stop` / `status` |

---

## 🚀 快速开始

```bash
pip install -e .

# 首次配置 — 选择 LLM 和输入 API Key
claw_soul onboard

# 启动守护进程（Web 仪表盘 http://localhost:7788）
claw_soul start

# CLI 交互聊天
claw_soul chat

# 停止
claw_soul stop
```

---

## 📋 CLI 命令

| 命令 | 说明 |
|------|------|
| `claw_soul onboard` | 交互式配置向导 |
| `claw_soul start` | 后台启动（Web + Telegram） |
| `claw_soul start -f` | 前台启动 |
| `claw_soul stop` | 停止守护进程 |
| `claw_soul status` | 查看运行状态 |
| `claw_soul chat` | CLI 交互聊天 |

---

## ⚙️ 配置

配置文件 `claw_soul.json`（由 `claw_soul onboard` 自动创建）：

```jsonc
{
  "llm": {
    "provider": "deepseek",
    "deepseek": { "apiKey": "...", "model": "deepseek-chat" }
  },
  "channels": {
    "telegram": { "token": "your-bot-token", "allowedUsers": [] }
  },
  "web": { "host": "0.0.0.0", "port": 7788 }
}
```

详见 [`claw_soul.example.json`](claw_soul.example.json)。

---

## 🧠 支持的 LLM

| Provider | 默认模型 |
|----------|----------|
| **DeepSeek** | `deepseek-chat` (V4) |
| **Grok (xAI)** | `grok-3` |
| **Claude (Anthropic)** | `claude-sonnet-4-20250514` |
| **Gemini (Google)** | `gemini-2.0-flash` |
| **Kimi (Moonshot)** | `moonshot-v1-128k` |
| **GLM (Zhipu)** | `glm-4-flash` |

---

## 📁 项目结构

```
ClawSoul/
├── claw_soul/
│   ├── main.py                # CLI 入口
│   ├── onboard.py             # 配置向导
│   ├── daemon.py              # 守护进程管理
│   ├── server.py              # Telegram 启动
│   ├── core/
│   │   ├── agent.py           # 核心推理循环
│   │   ├── tools.py           # 工具调用
│   │   ├── skill_loader.py    # 三级技能加载
│   │   ├── compaction.py      # 上下文压缩
│   │   ├── llm/               # LLM 适配器
│   │   ├── memory/            # Markdown 记忆
│   │   └── retrieval/         # BM25 + 向量 + 融合
│   ├── channels/
│   │   └── telegram_bot.py    # Telegram Bot
│   ├── scheduler/             # 定时任务
│   ├── web/                   # Web 仪表盘
│   └── templates/             # 内置技能模板
├── claw_soul.json             # 配置文件
├── pyproject.toml
└── LICENSE
```

---

## 🛠️ 开发

```bash
git clone https://github.com/ericwang915/ClawSoul.git
cd ClawSoul
python -m venv .venv && source .venv/bin/activate
pip install -e .
pytest tests/ -v
```

---

## 📄 License

[MIT](LICENSE)

---

<p align="center">
  <sub>Made with 💕 by ClawSoul</sub>
</p>
