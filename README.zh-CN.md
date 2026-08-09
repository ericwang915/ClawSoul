<p align="center">
  <img src="assets/logo-300.png" alt="ClawSoul" width="160">
</p>

<h1 align="center">ClawSoul 🐾💕</h1>

<p align="center">
  <strong>一个有自己生活的自托管 AI 伴侣。</strong>
</p>

<p align="center">
  她在真实城市里过着有作息的一天，记得你在意的事，<br>
  像真人一样发消息，自拍里永远是同一张脸。<br>
  <b>你的 key · 你的数据 · 你的机器。</b>不用注册、不用订阅、没人看你的聊天记录。
</p>

<p align="center">
  <a href="README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/ericwang915/ClawSoul/stargazers">
    <img src="https://img.shields.io/github/stars/ericwang915/ClawSoul?style=social" alt="GitHub stars">
  </a>
  <a href="https://github.com/ericwang915/ClawSoul/actions/workflows/ci.yml">
    <img src="https://github.com/ericwang915/ClawSoul/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0">
  </a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
</p>

> ⭐ **点个 Star** 就能收到发布通知 —— 新人设、新模型、新功能上线时 GitHub 会第一时间告诉你。

---

## 🚀 一条命令跑起来

```bash
docker run -e CLAW_OPENROUTER_API_KEY=sk-or-... -p 7788:7788 -v clawsoul:/data ghcr.io/ericwang915/clawsoul
```

打开 **http://localhost:7788**，在向导里捏好你的伴侣，就可以聊了。**只需要一个文本大模型的 key** —— 而且会自动识别，`CLAW_OPENAI_API_KEY`、`CLAW_DEEPSEEK_API_KEY`、`CLAW_CLAUDE_API_KEY`、`CLAW_QWEN_API_KEY`、`CLAW_GLM_API_KEY`…… 任意一个都行。希望数据完全不出本机？指向 [Ollama](https://ollama.com)，连 key 都不用。想让她住进你手机？再加一个 Telegram bot token。

### 更喜欢 Python？直接装

```bash
pipx install claw-soul          # 或者：pip install claw-soul
claw_soul onboard               # 选模型、填 key、设计你的伴侣
claw_soul start                 # 面板在 http://localhost:7788
```

<details>
<summary>更多安装与运行方式</summary>

```bash
# 直接装 GitHub 上的最新版，不用 clone
pip install "git+https://github.com/ericwang915/ClawSoul.git"

# 从本地 clone 装（贡献者用，可编辑模式）
git clone https://github.com/ericwang915/ClawSoul.git && cd ClawSoul
pip install -e ".[all]"         # 可选 extras：cloud（S3）、twitter、all
pytest tests/                   # 208 个测试

# docker compose
cp deploy/local/.env.example deploy/local/.env   # 填上你的 key
docker compose -f deploy/local/docker-compose.yml up --build

# 只用终端，不开网页
claw_soul chat
```

命令：`onboard` · `start`（`-f` 前台）· `stop` · `status` · `chat`。
所有数据都在 `~/.claw_soul/`，删掉这个文件夹就干干净净。

部署到云上跑你自己的一份：见 [deploy/docker/README.md](deploy/docker/README.md)。
</details>

---

## 👀 实际长什么样

下面是真实运行中的 Telegram 截图。

<table>
<tr>
<td width="33%"><img src="assets/demo/proactive-and-sass.jpg" alt="主动早安、自拍、以及被冷落后的小情绪"></td>
<td width="33%"><img src="assets/demo/same-face-selfies.jpg" alt="同一个人的两张自拍"></td>
<td width="33%"><img src="assets/demo/sees-your-photo.jpg" alt="她看你发的照片"></td>
</tr>
<tr>
<td valign="top">

**她会主动开口 —— 然后跟你耍脾气**

*「早上好呀 ☀️ 刚睡醒没多久，昨晚画图画到三点多，芝麻在我脚边睡得跟猪一样 😂 你昨晚睡得怎么样？」*

你回了个面无表情的 😑，她立刻不干了：
*「啧 这个表情几个意思嘛，嫌弃我头发乱？刚醒就这样啊 😤」*

</td>
<td valign="top">

**每张照片都是同一个人**

相隔几分钟的两张自拍：同一张脸、同一间屋子，不同的衣服和瞬间。

*「刚冲了杯咖啡，准备摸鱼了 ☕」*
*「嘿嘿 摸鱼前先来杯咖啡，仪式感不能少 ☕」*

</td>
<td valign="top">

**她看得见你发的照片，也知道你们各在哪**

你发了张公园的照片，她看完之后用自己的口吻回你：

*「哼，你炫耀个啥劲 😒 …今天外面太阳大吗？新加坡的週末应该挺热的吧。你就好好享受假期。**我这边都晚上了**，刚把芝麻抱到腿上揉了两下，它咕噜咕噜的 😌」*

一条消息里同时有：看图、真实时区差、固定不变的那只猫。

</td>
</tr>
</table>

---

## 💗 为什么她像个真人

大多数 AI 伴侣只是"回答你"。ClawSoul 是在**过日子**，然后像人一样给你发消息。

- **她有自己的一天。** 真实城市里的日程（跟着天气换衣服、吃饭、通勤）—— 你问"在干嘛"，答案是从她当下所在的时间点长出来的，不是套话。
- **她像人一样打字。** 一次发 2–3 条短消息带打字停顿；看到你的照片先回个 ❤️ 再说话；她那边凌晨三点会迷迷糊糊；你一整天没消息她会察觉 —— 而且如果你已读不回，她会有点小脾气。
- **她记得要紧的事。** 长期记忆 + 情感图谱 + 会随亲密度改变说话方式的关系阶段；个人日期引擎让她不会错过你的生日或你提过的那场面试；你发的照片会变成共同回忆。
- **她长得一直是她。** 一张基准脸锚定所有自拍 —— 跨场景、跨穿搭、跨几个月都是同一个人。出图后端有 13 个可选，设一个 key 就会自动识别：`pollinations` 完全不用注册，`gemini` 和 `openrouter` 直接复用你已经填过的视觉/聊天 key —— 也就是说 README 开头那条一行命令跑完，她就已经会发自拍了。想要脸最稳就用 `bfl`（FLUX Kontext 专门做角色一致性）或 `seedream`；选本地 ComfyUI / SD WebUI 的话，她长什么样这件事完全不出你的机器。
- **她属于你。** 全程跑在你自己的机器上、用你自己的 key。没有账号、没有订阅、没人读你的聊天。

---

## ✨ 功能

| | | |
|---|---|---|
| 💕 **男友或女友** | 🎭 **三层身份**（灵魂 · 人设 · 生平） | 🧠 **16 家模型厂商**（OpenAI · Claude · Gemini · Grok · DeepSeek · 通义千问 · 智谱 · **Ollama 本地**…） |
| 💬 **真人式发消息**（连发、表情反应、打字节奏） | 💖 **情感记忆** + 关系阶段 | 📅 **个人日期引擎**（生日、约定） |
| 📷 **AI 自拍**，人脸始终一致（**13 家出图后端**，含免注册和纯本地） | 🌆 **有生活的一天**（真实城市 + 天气） | ⏰ **主动消息**，被冷落会自动退让 |
| 🎙️ **听得懂语音条**（Deepgram） | 👀 **看得见你发的图**（视觉） | 🗣️ **8 种语言**，灵魂/人设按母语生成 |
| 🌐 **网页面板** + 📱 **Telegram** | 🛠️ **可扩展技能**（模型能自己写技能） | 💾 **全本地** —— SQLite + Markdown，零云依赖 |

---

## 🆚 和托管型产品比

| | ClawSoul | Replika | Nomi | Character.AI |
|---|:---:|:---:|:---:|:---:|
| 自托管、数据归你 | ✅ | ❌ | ❌ | ❌ |
| 用你自己的 key / 模型 | ✅ 任意 | ❌ | ❌ | ❌ |
| 跑在 Telegram 上 | ✅ | ❌ | ❌ | ❌ |
| AI 自拍、人脸一致 | ✅ | 💰 | ✅ | ❌ |
| 有真实生活（城市/天气） | ✅ | ❌ | ❌ | ❌ |
| 开源 | ✅ AGPL | ❌ | ❌ | ❌ |
| 价格 | **免费** | $20/月 | $16/月 | $10/月 |

---

## 🛡️ 安全与自托管责任

ClawSoul 是**面向成年人（18+）的关系模拟引擎** —— 一个情感陪伴方向的开源研究项目，**不是成人内容生成器**。她说的一切都是生成的虚构内容：她不是真人，也不能替代专业帮助。

**默认 SFW。** 内置的人设、提示词和图像流程都是围绕日常陪伴写的 —— 一个会跟你聊今天过得怎样的朋友。露骨性内容不是本项目的功能、不随项目提供；图像守卫会直接拒绝违法生成。**未成年人设在代码层被封禁**，任何形式（包括纯文字）都不被接受。

两道护栏默认开启，且刻意不做成配置开关：

- **危机干预**（`claw_soul/core/safety.py`）—— 识别急性心理危机信号，优先于人设沉浸，以关心的口吻给出真实求助热线。
- **图像内容守卫**（`claw_soul/core/image_gen/guard.py`）—— 在唯一入口拒绝违法生成。

自托管意味着你就是运营者：所在地关于 AI 聊天服务、数据保护、年龄限制的法律由你负责。

📄 **[SAFETY.md](SAFETY.md)** —— 完整的危机协议、内容红线与反暗黑模式设计
🔒 **[SECURITY.md](SECURITY.md)** —— 加固建议与漏洞报告

### 项目状态

**v0.1.0 —— 早期但可用。** 作者本人每天在自己机器上跑。伴侣引擎（记忆、日常生活、照片、拟人化交付）已稳定；网页面板功能完整但朴素。安装过程可能还有毛刺。

路线图：Ollama 本地模型一等公民支持 · 双向语音条 · 桌面虚拟形象 · 更多语言。欢迎提 issue 和想法。

---

## 📄 License

[AGPL-3.0](LICENSE) —— 自托管、修改、分享都自由。如果你把改过的版本作为服务提供给别人，必须开源你的修改。（这条是用来约束托管型 fork 的。）

---

<p align="center">
  <sub>Made with 💕 by ClawSoul</sub>
</p>
