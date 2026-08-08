<p align="center">
  <img src="assets/logo-300.png" alt="ClawSoul" width="160">
</p>

<h1 align="center">ClawSoul 🐾💕</h1>

<p align="center">
  <strong>A self-hosted AI companion with a life of her own.</strong>
</p>

<p align="center">
  She keeps a real daily schedule in a real city, remembers what matters to you,<br>
  texts like a person, and takes selfies that always look like the same person.<br>
  <b>Your keys · your data · your machine.</b> No account, no subscription, no one reading your chats.
</p>

<p align="center">
  <a href="https://github.com/ericwang915/ClawSoul/stargazers">
    <img src="https://img.shields.io/github/stars/ericwang915/ClawSoul?style=social" alt="GitHub stars">
  </a>
  <a href="https://github.com/ericwang915/ClawSoul/actions/workflows/ci.yml">
    <img src="https://github.com/ericwang915/ClawSoul/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0 License">
  </a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <a href="https://github.com/ericwang915/ClawSoul/pkgs/container/clawsoul">
    <img src="https://img.shields.io/badge/ghcr.io-clawsoul-2496ED?logo=docker&logoColor=white" alt="Docker image">
  </a>
</p>

<p align="center">
  <sub><a href="#-run-it-one-command">Run it</a> ·
  <a href="#-why-she-feels-real">Why it feels real</a> ·
  <a href="#-vs-the-hosted-apps">vs. Replika/Nomi</a> ·
  <a href="#%EF%B8%8F-safety--responsible-self-hosting">Safety</a></sub>
</p>

> ⭐ **Star the repo** to get release notifications — new personas, models, and
> features land often, and GitHub will tell you the moment they do.

---

## 🚀 Run it (one command)

```bash
docker run -e CLAW_DEEPSEEK_API_KEY=sk-... -p 7788:7788 -v clawsoul:/data ghcr.io/ericwang915/clawsoul
```

Open **http://localhost:7788**, design your companion in the wizard, and start
talking. The only key you need is a text-LLM key (a DeepSeek key is ~free to
start). Want her on your phone? Add a Telegram bot token — that's it.

<details>
<summary>Other ways to run (compose · pip · Fly)</summary>

```bash
# docker compose (edit deploy/local/.env first)
cp deploy/local/.env.example deploy/local/.env
docker compose -f deploy/local/docker-compose.yml up --build

# pip (Python 3.10+)
pip install -e .
claw_soul onboard    # pick a provider, paste your key, design your companion
claw_soul start      # dashboard at http://localhost:7788   (or: claw_soul chat)
```

Deploy your own instance to the cloud: see [deploy/docker/README.md](deploy/docker/README.md).
</details>

---

## 👀 What it actually looks like

Real screenshots from a live ClawSoul bot on Telegram (Chinese conversation,
translated below — she speaks whatever language you pick).

<table>
<tr>
<td width="33%"><img src="assets/demo/proactive-and-sass.jpg" alt="proactive good-morning, a selfie, and attitude"></td>
<td width="33%"><img src="assets/demo/same-face-selfies.jpg" alt="two selfies of the same person"></td>
<td width="33%"><img src="assets/demo/sees-your-photo.jpg" alt="she looks at a photo you sent"></td>
</tr>
<tr>
<td valign="top">

**She starts the conversation — then gives you attitude**

*"morning ☀️ just woke up, I was drawing till 3am… Sesame slept by my feet like a little pig 😂 how'd you sleep?"*

He replies with a flat 😑 — so she pushes back:
*"tsk, what's that face supposed to mean? judging my messy hair? I* just *woke up 😤"*

</td>
<td valign="top">

**The same person, every photo**

Two selfies minutes apart — same face, same apartment, different shirt and moment.

*"just made coffee, about to slack off ☕"*
*"heh, coffee before slacking. gotta have the ritual ☕"*

</td>
<td valign="top">

**She sees what you send — and knows where you both are**

He sends a photo of a park. She looks at it and answers in character:

*"pff, showing off huh 😒 …is the sun strong out there? Singapore weekends get hot. Enjoy your day off. **It's already evening on my side** — just pulled Sesame onto my lap, she's purring 😌"*

Vision + real timezones + the same pet, every time.

</td>
</tr>
</table>

---

## 💗 Why she feels real

Most AI companions answer you. ClawSoul lives a life and texts you like a person.

- **She has a day.** A real schedule in a real city (weather-aware outfits,
  meals, a commute) — ask "what are you up to?" and the answer is anchored to
  where her day actually is, not generic filler.
- **She texts like a human.** Short messages, sometimes 2–3 in a row with a
  typing pause between; reacts to your photo with a ❤️ before she replies;
  groggy at 3am her time; notices when you vanished all day — and gets a little
  sulky if you left her on read.
- **She remembers what matters.** Long-term memory + an emotional graph +
  relationship stages that change *how* she talks as you grow closer. A
  personal-date engine means she won't miss your birthday or that interview you
  mentioned last week. The photos you send become shared memories.
- **She looks like herself.** A canonical face reference keeps every selfie the
  same person across scenes, outfits, and months.
- **She's yours.** Runs entirely on your machine with your keys. No account, no
  subscription, no one reading your chats.

---

## 📸 A photo from their day, not a stock asset

Every selfie is generated from where her day actually is — the time, the mood,
the weather, what she's doing right now. Same face, every time.

<table>
<tr>
<td width="33%"><img src="assets/samples/morning.jpg" alt="cozy morning, coffee in hand"></td>
<td width="33%"><img src="assets/samples/lunch.jpg" alt="in the park at lunchtime"></td>
<td width="33%"><img src="assets/samples/cozy.jpg" alt="on the couch in the evening"></td>
</tr>
<tr>
<td align="center"><sub><b>08:30</b> · sleepy ☕<br>"morning…just made coffee. you up?"</sub></td>
<td align="center"><sub><b>12:15</b> · cheerful 🌿<br>"lunch in the park today, it's gorgeous out"</sub></td>
<td align="center"><sub><b>20:40</b> · cozy 🕯️<br>"reading on the couch. wish you were here."</sub></td>
</tr>
</table>

**Boyfriend, same system — anime or photoreal, your call:**

<table>
<tr>
<td width="33%"><img src="assets/samples/anime-male-rush.jpg" alt="running late with toast"></td>
<td width="33%"><img src="assets/samples/anime-male-ramen.jpg" alt="at a ramen counter"></td>
<td width="33%"><img src="assets/samples/anime-male-gaming.jpg" alt="late-night gaming"></td>
</tr>
<tr>
<td align="center"><sub><b>07:45</b> · running late 🍞<br>"toast in mouth, tie not done. running."</sub></td>
<td align="center"><sub><b>13:00</b> · ramen run 🍜<br>"snuck out for ramen. don't tell my boss."</sub></td>
<td align="center"><sub><b>23:20</b> · one more round 🎮<br>"one more round and I'm logging off. promise."</sub></td>
</tr>
</table>

<details>
<summary><b>Any look you want</b> — you describe them in the wizard, they stay that person</summary>

<table>
<tr>
<td width="16%"><img src="assets/samples/realistic-asian.jpg"></td>
<td width="16%"><img src="assets/samples/realistic-european.jpg"></td>
<td width="16%"><img src="assets/samples/realistic-black.jpg"></td>
<td width="16%"><img src="assets/samples/realistic-male-gym.jpg"></td>
<td width="16%"><img src="assets/samples/realistic-male-bike.jpg"></td>
<td width="16%"><img src="assets/samples/realistic-male-rooftop.jpg"></td>
</tr>
</table>

Photos are generated with [Seedream](https://www.byteplus.com/en/product/modelark)
(~$0.035 each) and are **optional** — skip the key and everything else still works.
</details>

---

## ✨ Features

| | | |
|---|---|---|
| 💕 **Boyfriend or girlfriend** | 🎭 **Three-layer identity** (soul · persona · profile) | 🧠 **Any LLM** (DeepSeek · Claude · Grok · Gemini · Kimi · GLM) |
| 💬 **Human texting** (bursts, reactions, typing rhythm) | 💖 **Emotional memory** + relationship stages | 📅 **Personal-date engine** (birthdays, plans) |
| 📷 **AI selfies** with a consistent face | 🌆 **Daily life** grounded in a real city + weather | ⏰ **Proactive messages** that back off when ignored |
| 🎙️ **Understands voice notes** (Deepgram) | 👀 **Sees your photos** (vision) | 🗣️ **8 languages**, native soul/persona |
| 🌐 **Web dashboard** + 📱 **Telegram** | 🛠️ **Extensible skills** (LLM writes its own) | 💾 **All local** — SQLite + Markdown, zero cloud |

---

## 📋 CLI

| Command | Description |
|---------|-------------|
| `claw_soul onboard` | Interactive setup wizard |
| `claw_soul start` | Start the daemon (web + Telegram) |
| `claw_soul chat` | Interactive terminal chat |
| `claw_soul status` / `stop` | Daemon lifecycle |

---

## 🆚 vs. the hosted apps

| | ClawSoul | Replika | Nomi | Character.AI |
|---|:---:|:---:|:---:|:---:|
| Self-hosted, your data | ✅ | ❌ | ❌ | ❌ |
| Your own API keys / model | ✅ any | ❌ | ❌ | ❌ |
| Runs on Telegram | ✅ | ❌ | ❌ | ❌ |
| AI selfies, consistent face | ✅ | 💰 | ✅ | ❌ |
| Lives a daily life (city/weather) | ✅ | ❌ | ❌ | ❌ |
| Open source | ✅ AGPL | ❌ | ❌ | ❌ |
| Price | **free** | $20/mo | $16/mo | $10/mo |

---

## ⚙️ Configuration

All runtime data lives under `~/.claw_soul/`:

```
~/.claw_soul/
├── claw_soul.json           # config
├── claw_soul.pid            # daemon PID
├── daemon.log               # daemon log
└── context/
    ├── soul/SOUL.md         # core personality
    ├── persona/             # active persona + appearance.md (selfie look)
    ├── profile/PROFILE.md   # life background
    ├── calendar/today_plan.md   # today's 24-hour schedule
    ├── memory/              # long-term memory (Markdown)
    ├── knowledge/           # knowledge base (RAG)
    ├── photos/              # selfie album + reference/ portraits
    ├── skills/              # user-defined skills
    └── logs/                # per-day conversation logs
```

`claw_soul.json` is created by `claw_soul onboard`. See [`claw_soul.example.json`](claw_soul.example.json) for the full schema:

```jsonc
{
  "llm": {
    "provider": "deepseek",
    "deepseek": { "apiKey": "...", "model": "deepseek-chat" }
  },
  "channels": {
    "telegram": { "token": "your-bot-token", "allowedUsers": [12345678] }
  },
  "skills": {
    "seedream": {                          // AI selfies
      "apiKey": "<ARK_API_KEY>",
      "model": "seedream-5-0-lite-260128"
    }
  },
  "selfie": {
    "enabled": true,
    "schedule": ["10:00", "16:00", "20:00"],
    "chatId": 12345678,
    "maxDaily": 3,
    "proactiveProbability": 0.15           // chance of attaching a selfie to a proactive msg
  },
  "proactive": {
    "enabled": true,
    "chatId": 12345678,
    "maxDaily": 6,
    "quietStart": 0, "quietEnd": 8
  },
  "deepgram": { "apiKey": "" },            // voice input (optional)
  "tavily":   { "apiKey": "" },            // web search (optional)
  "web": { "host": "0.0.0.0", "port": 7788 }
}
```

---

## 🧠 Supported LLMs

| Provider | Default model |
|----------|---------------|
| **DeepSeek** | `deepseek-chat` (V4) |
| **Grok (xAI)** | `grok-3` |
| **Claude (Anthropic)** | `claude-sonnet-4-20250514` |
| **Gemini (Google)** | `gemini-2.0-flash` |
| **Kimi (Moonshot)** | `moonshot-v1-128k` |
| **GLM (Zhipu)** | `glm-4-flash` |

---

## 📷 AI selfies (Seedream)

Powered by ByteDance / Volcano Engine's Seedream model. **Three trigger paths:**

- **Scheduled** — fires at the times in `selfie.schedule` (default 10:00 / 16:00 / 20:00)
- **Proactive** — attached to a proactive message with `proactiveProbability` chance
- **On demand** — when the user says something like "send me a selfie", the LLM invokes the `selfie` skill

**Scene-driven.** Each selfie's content is derived from the activity scheduled for the
current time in `today_plan.md`. If the plan says *"10:00 coffee on the balcony"*, the
10:00 selfie will be exactly that.

**Visual consistency.**
- Edit `~/.claw_soul/context/persona/appearance.md` to lock the character's look
- Drop reference portraits into `~/.claw_soul/context/photos/reference/` for face anchoring
- A stable seed derived from the appearance description keeps the face consistent across shots

Photos are stored under `~/.claw_soul/context/photos/` and pruned automatically after 30 days.

---

## 📁 Project layout

```
ClawSoul/
├── claw_soul/
│   ├── main.py                  # CLI entry point
│   ├── onboard.py               # setup wizard
│   ├── daemon.py                # daemon process manager
│   ├── server.py                # Telegram + scheduler bootstrap
│   ├── core/
│   │   ├── agent.py             # core reasoning loop
│   │   ├── persistent_agent.py  # session persistence
│   │   ├── tools.py             # tool dispatch
│   │   ├── skill_loader.py      # three-tier progressive skill loading
│   │   ├── compaction.py        # context compaction
│   │   ├── stt.py               # speech-to-text (Deepgram)
│   │   ├── llm/                 # provider adapters (6)
│   │   ├── memory/              # Markdown memory + emotional graph + milestones + temporal index
│   │   ├── retrieval/           # BM25 + dense + RRF + LLM reranker
│   │   ├── knowledge/           # knowledge-base RAG
│   │   └── image_gen/           # Seedream selfie pipeline
│   ├── channels/
│   │   └── telegram_bot.py      # Telegram bot (streaming / voice / images)
│   ├── scheduler/
│   │   ├── cron.py              # generic cron jobs
│   │   ├── planner.py           # daily 24-hour plan generator
│   │   ├── proactive.py         # sentiment-aware proactive messages
│   │   ├── selfie_task.py       # scheduled selfies
│   │   └── heartbeat.py         # heartbeat monitor
│   ├── web/                     # FastAPI dashboard + WebSocket chat
│   └── templates/               # built-in persona / soul / skills
├── tests/                       # 208 tests
├── pyproject.toml
└── LICENSE
```

---

## 🛠️ Development

```bash
git clone https://github.com/ericwang915/ClawSoul.git
cd ClawSoul
python -m venv .venv && source .venv/bin/activate
pip install -e .
pytest tests/ -v
ruff check claw_soul tests
```

---

## 🛡️ Safety & responsible self-hosting

ClawSoul is a **relationship-simulation engine for adults (18+)** — an
emotional-companionship research project, not an adult-content generator.
Everything the companion says is generated fiction: it is not a person, and not
a substitute for professional help.

**It ships SFW.** The bundled personas, prompts, and image pipeline are written
for everyday companionship — a friend who texts you about her day. Explicit
sexual content is not a feature, is not included, and the image guard refuses
categorically illegal generation outright. Personas depicting minors are blocked
at the code level and are never acceptable, in any form, including text.

Two guardrails ship enabled and are deliberately not configuration flags:

- **Crisis safety** (`claw_soul/core/safety.py`) — detects acute distress and
  responds with care and real helpline resources ahead of persona immersion.
- **Image content guard** (`claw_soul/core/image_gen/guard.py`) — blocks
  categorically illegal image generation at the single chokepoint.

If you self-host, you are the operator: local laws on AI chat services, data
protection, and age restrictions are your responsibility.

📄 **[SAFETY.md](SAFETY.md)** — the full crisis protocol, content limits, and
anti-dark-pattern design decisions.
🔒 **[SECURITY.md](SECURITY.md)** — hardening notes and vulnerability reporting.

### Status

**v0.1.0 — early but real.** Runs daily on the maintainer's own machine. The
companion engine (memory, daily life, photos, humanized delivery) is stable;
the web dashboard is functional but plain. Expect rough edges in setup.

Roadmap: local-model (Ollama) first-class support · voice notes both directions ·
a desktop avatar mode · more languages. Ideas and issues welcome.

---

## 📄 License

[AGPL-3.0](LICENSE) — free to self-host, modify, and share. If you run a
modified version as a service for others, you must open-source your
modifications. (This keeps hosted forks honest.)

---

<p align="center">
  <sub>Made with 💕 by ClawSoul</sub>
</p>
