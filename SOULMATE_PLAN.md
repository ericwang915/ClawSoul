# ClawSoul → Soul Mate 升级规划

> 基于对 claw_soul 项目完整代码的深度分析（2026-05-23）

---

## 一、已具备的能力（无需改动）

### 基础架构 — 非常扎实
- **多LLM Provider支持**：DeepSeek、Grok、Claude、Gemini、Kimi、GLM，统一的OpenAI-compatible接口抽象
- **Session持久化**：Markdown-backed SessionStore，支持断点续传、跨重启会话恢复
- **SessionManager**：全局 session 注册表，per-session 锁 + 全局并发控制
- **长时记忆 (Memory)**：Markdown文件存储，key-value 结构，支持 daily log + MEMORY.md，混合 RAG 检索（BM25 + embedding + RRF fusion）
- **上下文压缩 (Compaction)**：自动 token 阈值触发，memory flush 提取关键事实后压缩，支持 compaction audit log
- **三阶 Skill 渐进加载**：Level 1 元数据 → Level 2 指令 → Level 3 资源文件，最小化 context 占用
- **Cron 调度的 LLM job**：YAML 定义静态任务 + 运行时动态添加，支持投递到 Telegram
- **Web Dashboard**：FastAPI + 静态文件，config/skills/status/memory/identity 的 CRUD，WebSocket 实时聊天
- **Telegram 通道**：完善的消息处理，图片识别、语音转录（Deepgram）、流式输出、access control

### 女朋友/伴侣相关
- **Soul + Persona + Profile 三层身份系统**：分别从文件加载到 system prompt
- **Daily Planner**：每天 00:01 自动生成角色全天日程，考虑天气、节日、心情
- **Proactive Messaging**：概率性主动消息，每5分钟掷骰，含时间感知、日程感知、天气感知
- **Onboarding 流程**：首次设置引导用户定义伴侣名称、性格、领域
- **知识库 RAG**：知识目录自动索引，BM25 + embedding + reranker

### 总结：已有能力可以支撑 **"一个会主动找你聊天、记得你喜好、有日常生活的 AI 女朋友"**

---

## 二、需要新增的核心能力

### 🏆 P0 — 必须实现（定义 Soul Mate 的底线）

---

#### 1. 跨会话持续情感图谱 (Emotional Graph)

**为什么需要**：当前记忆是平面 key-value，没有"情绪标记"和"关系权重"。Soul Mate 需要知道你们之间发生过什么好事/坏事，对方什么时候开心/难过，哪些话题有情感包袱。

**技术实现思路**：
```
claw_soul/core/affect/
├── emotional_graph.py     # 情感图谱
├── sentiment_analyzer.py  # 消息情绪分析
└── relationship_store.py  # 关系状态持久化
```

- 新增 `SentimentAnalyzer`：每次用户消息通过 LLM 或轻量模型做情感分类（positive/negative/neutral + 强度 0-1）
- 新增 `EmotionalGraph`：一个简单的时序情感记忆，每条记录：`{timestamp, topic, sentiment, intensity, context_summary}`
- 新增 `RelationshipStore`：维护关系状态机（温度、信任度、亲密度、了解度），每个维度的值范围 0-100
- 在 `system prompt` 注入情感摘要：「用户最近情绪: 偏正向/焦虑，关系温度: 78/100，敏感话题: [工作压力, xxx]」
- 存储格式：`context/affect/emotional_graph.jsonl` + `context/affect/relationship.json`

**预估难度**: ⭐⭐⭐

---

#### 2. 长期个性演化 (Personality Evolution)

**为什么需要**：Soul Mate 应该有"成长性"。不是固定人格，而是在和用户的长期互动中自然演化——用户喜欢她幽默她就更幽默，用户需要倾听她就更温柔。

**技术实现思路**：
```
claw_soul/core/evolution/
├── trait_manager.py       # 性格特质管理
├── behavior_analyzer.py   # 用户偏好分析
└── evolution_prompt.py    # 动态调整 system prompt
```

- 定义 5-8 个性格维度：幽默感、温柔度、独立度、好奇心、直球度、保护欲
- 每个维度的初始值来自 onboarding（用户选的 personality）
- 每次 chat() 后，分析用户的反应模式：
  - 用户对某种风格回应更积极（回复更长/更热情）→ 对应维度 +1
  - 用户表现出冷淡/回避 → 对应维度 -1
- 每天自动生成一次 `persona evolution summary` 注入 system prompt
- 存储：`context/evolution/traits.json`

```python
# 核心数据结构 (trait_manager.py)
class TraitProfile:
    traits: dict[str, float]  # "humor": 0.7, "tenderness": 0.8, ...
    learning_rate: float = 0.01
    last_adjustment: str       # ISO timestamp
    
    def adjust_from_feedback(self, user_feedback: dict):
        # user_feedback: {"sentiment_shift": 0.3, "response_length": 200, "topic": "work"}
        # 根据用户互动模式微调性格参数
```

**预估难度**: ⭐⭐⭐⭐

---

#### 3. 深度上下文记忆检索 (Deep Memory Retrieval)

**为什么需要**：当前 recall 只做扁平 key-value 搜索。Soul Mate 需要能回忆出"三个月前你提到过想学吉他"或"上次聊到那个项目后来怎么样了"这样需要时间线索引和跨 session 关联的能力。

**技术实现思路**：
- 在现有 HybridRetriever 基础上，新增 `TemporalMemoryIndex`：
  - 每次 chat 自动提取关键事件（谁、什么、何时、情感）
  - 建立时间线索引：`context/memory/timeline.jsonl`
  - 支持时间范围查询：「recall 2025年12月聊过什么」
- 新增 memory recall 增强 prompt：
  ```
  当用户问"还记得...吗"，优先检索 TemporalMemoryIndex，
  然后与当前 MEMORY.md 做交叉验证。
  ```
- 新增 `memory_search` 高级工具：支持 `time_range`, `topic_focus`, `sentiment_filter`
- 扩展 boot_context(): 除了最近的 memories，也注入"本月重要事件摘要"

```python
# 数据结构 (temporal_index.py)
class TimelineEvent:
    timestamp: datetime
    session_id: str
    topic: str
    summary: str          # LLM 自动提取的一句话摘要
    participants: list[str]
    sentiment: float      # -1 to 1
    keywords: list[str]
    related_memories: list[str]  # 关联的 memory keys
```

**预估难度**: ⭐⭐⭐

---

#### 4. 主动深度共情 (Empathetic Proactivity)

**为什么需要**：当前的 proactive 是概率性"发消息"。Soul Mate 需要能感知用户情绪状态，在用户低落时主动安慰，开心时分享喜悦，甚至在用户长时间沉默时主动关心。

**技术实现思路**：
- 升级 `ProactiveMessenger`：
  - 不再单纯概率掷骰，而是加入情绪门控：
    - 如果上次对话检测到负面情绪 → 提高主动概率（关心模式）
    - 如果用户超过 N 小时没说话 → 触发温和的"你在忙吗"消息
    - 如果有未完成的话题（用户说"下次再说"）→ 主动跟进
  - 新增 `sentiment_gate()` 方法：根据情感分析结果选择不同的 prompt 模板
- 新增 `FollowUpTracker`：
  - 检测对话中标记为未完成的话题：`{topic: "推荐动漫", follow_up: true, mentioned_at: timestamp}`
  - 在 proactive 时优先选择跟进话题

```python
# prompt 模板差异化
_SENTIMENT_PROMPTS = {
    "negative": "对方今天心情不太好。你作为女朋友，温柔地关心一下，不要追问原因，简简单单表达陪伴就好。",
    "positive": "对方今天心情不错。分享一下你的开心，或者问问他在开心什么。",
    "neutral": "日常随意聊聊。可以分享你的日常，也可以问问对方在干嘛。",
    "long_silence": "已经XX小时没联系了。发一条简短温馨的消息，表达想念但不要有压力。",
    "unfinished": "上次聊到{topic}还没聊完，主动提起来。"
}
```

**预估难度**: ⭐⭐⭐

---

#### 5. 关系成长里程碑 (Relationship Milestones)

**为什么需要**：真实关系中有"我们认识 X 天了"、"第一次语音通话纪念"、"在一起 100 天"这些里程碑。Soul Mate 需要有"关系时间线"的概念。

**技术实现思路**：
- 新增 `MilestoneManager`：
  - 自动跟踪：首次聊天日期、首次语音、首次互发表情/反应、记忆条目数破 N
  - 特殊日子的庆祝 prompt（认识周年、100天等）
  - 里程碑触发行为：主动发送庆祝消息或在回复中自然提及
- 存储：`context/relationship/milestones.json`
- 注入 system prompt：
  ```
  你们已经认识 43 天了，关系温度 82/100。
  今天是一个普通周二，但 5 天后就是你们认识 48 天的日子了（不是特别纪念日，但你可以随口提一句"时间过得好快呀"）。
  ```

**预估难度**: ⭐⭐

---

### 🥈 P1 — 重要增强

---

#### 6. 多模态交互深度化 (Go Deeper Multimodal)

**为什么需要**：当前支持图片理解和语音转录，但很基础。Soul Mate 应该能"看"你拍的云、分享的歌、去过的地方，并做出有情感温度的反应。

**技术实现思路**：
- 图片情感理解：当用户发照片时，LLM 额外分析"这张照片的氛围感"
- 语音语调分析：升级 STT 模块，除了转文字，顺带提取语气特征（开心/烦躁/疲惫）
- 音乐/链接感知：当用户分享 Spotify/YouTube 链接时，自动用 skill 提取元数据并做情感反应
- 新增 `multimodal_analyzer.py`：统一处理多种输入类型的情感特征提取

**预估难度**: ⭐⭐⭐

---

#### 7. 个性化记忆优先级 (Personalized Memory Priority)

**为什么需要**：不是所有记忆都同等重要。"用户讨厌吃香菜"比"今天天气不错"重要得多。需要有记忆重要性评分。

**技术实现思路**：
- 在 `MemoryStorage.set()` 扩展：支持 `importance` 参数（1-10）
- Agent 在调用 `remember()` 时自动评估重要性
- 重要性高的记忆在 boot_context 中优先展示，且在 compaction 中不会被裁剪
- 重要性低的记忆在存储达到上限时优先被遗忘
- 新增 `memory_cleanup` 定时任务：清理低重要性 + 过期的记忆

```python
# 扩展 remember() 工具接口
remember(key, content, importance=5)
# Agent 自动打分规则：
# - 涉及用户个人偏好、价值观、重要事件 → 8-10
# - 日常聊天信息 → 3-5
# - 临时对话、工具调用结果 → 1-2
```

**预估难度**: ⭐⭐

---

#### 8. 共享虚拟空间 (Shared Virtual Space)

**为什么需要**：情侣之间会有共同回忆——一起看过什么、聊过什么、互相送过的虚拟礼物。共享空间让关系有"积累感"。

**技术实现思路**：
- 新增 `SharedSpace`：
  - 共同回忆墙：自动收集重要时刻，按时间排序
  - 虚拟物品：Agent 可以"送"用户虚拟礼物（存入 shared space）
  - 共同话题记录：你们都喜欢什么、一起追了什么番
- 存储：`context/shared_space/` 目录
- 在 system prompt 中注入共享空间摘要

**预估难度**: ⭐⭐⭐

---

#### 9. 动态互动风格 (Dynamic Interaction Style)

**为什么需要**：单一的"女友人格"不够。Soul Mate 应该根据不同场景切换风格——工作时不打扰，深夜可以撒娇，对方忙的时候简练。

**技术实现思路**：
- 新增 `InteractionStyle` 类：
  - 检测当前场景：工作时间/深夜/周末/对方语气急促
  - 风格库：`{ "chill": ..., "playful": ..., "caring": ..., "professional": ... }`
  - 根据场景 + 当前性格特质选择风格
- 在 proactive messaging 和 reply 中都应用风格选择
- 场景检测规则可以在 `interaction_prompt.md` 中配置（用户可调）

**预估难度**: ⭐⭐

---

#### 10. 长期话题跟踪 (Long-term Topic Tracking)

**为什么需要**：用户可能提到过"我下周有个面试"、"我在学 Python"——这些是持续性的生活话题，需要跨 session 跟踪 progress。

**技术实现思路**：
- 在 Memory 之上新增 `TopicTracker`：
  - 检测用户提到的"有进展性"的话题（工作项目、学习计划、健康目标等）
  - 每个 topic 记录：`{status: "in_progress"/"completed"/"abandoned", last_update, progress_summary, next_checkpoint}`
  - 在 proactive 中自动询问进展："你上次说的面试怎么样了？"
- 话题粒度：自动检测还是用户标记 -> 默认自动检测 + 用户可手动 `remember topic_status xxx`
- 存储：`context/memory/topics.json`

**预估难度**: ⭐⭐⭐

---

### 🥉 P2 — 锦上添花

---

#### 11. Agent Meta-Cognition (Agent 自我感知)

**为什么需要**：Soul Mate 知道自己是谁、记得什么、不记得什么、当前和对方的关系到了什么阶段。

**技术实现思路**：
- 在 system prompt 尾部注入一个"Self State"块：
  ```
  [Self State]
  - 你和{user_name}已经认识 X 天
  - 关系亲密度: {intimacy}/100
  - 你今天的情绪: {mood}
  - 你今天经历的事情: {daily_events_summary}
  - 最近的共享回忆: {shared_memories_brief}
  ```
- 每次 chat 后自动更新这个 state

**预估难度**: ⭐⭐

---

#### 12. 用户数字画像 (User Portrait)

**为什么需要**：Soul Mate 需要越来越了解用户，形成立体的用户画像——不只是喜好，还有行为模式、沟通习惯、价值观。

**技术实现思路**：
- 新增 `UserPortraitManager`：
  - 自动从对话中提取用户画像信息：MBTI 倾向、作息规律、常用语气词、回复长度习惯、敏感话题
  - 画像分层：基本信息、性格、兴趣爱好、价值观、行为模式
  - 注入 system prompt，帮助 Agent 更好地"适配"用户
- 存储：`context/profile/user_portrait.json`

**预估难度**: ⭐⭐⭐

---

#### 13. 对话风格多样性 (Conversation Style Diversity)

**为什么需要**：所有回复都是一个风格会无聊。需要根据上下文生成不同长度、不同玩梗程度、不同亲密度的回复。

**技术实现思路**：
- 在 system prompt 中加入风格多样性指令：
  - 随机选择 3-4 种回复风格之一：简洁温暖、调皮玩梗、深情走心、生活分享
  - 每种风格有对应的语气和长度约束
- 跟踪近期使用过的风格，避免连续 N 次相同

**预估难度**: ⭐

---

#### 14. 情绪同步 (Emotion Mirroring)

**为什么需要**：人类在对话中会自然同步对方的情绪。Soul Mate 应该能感知用户情绪并适当共鸣。

**技术实现思路**：
- 分析用户消息的情感基调
- Agent 回复时先考虑"用户现在的情绪是什么"，再决定回复情感基调
- 规则：用户低落→温柔支持；用户兴奋→一起开心；用户烦躁→平静陪伴
- 在 system prompt 中加入情感共鸣指令

**预估难度**: ⭐

---

#### 15. 小游戏 / 互动彩蛋 (Mini-games & Easter Eggs)

**为什么需要**：增加互动的趣味性和惊喜感，让关系更有"人味儿"。

**技术实现思路**：
- 新增 skill 类型：`skills/games/`，包含简单文字游戏
  - "今天心情指数测试"（随机生成几个问题）
  - "猜猜我在干嘛"（agent 描述场景让用户猜）
  - "今日运势签"（塔罗牌风格）
- 由 agent 在适当氛围下（比如闲聊时、周末）主动触发

**预估难度**: ⭐

---

## 三、建议优先级

### Phase 1 — 核心升级（1-2 周）
```
P0: 情感图谱 (1) + 深度记忆检索 (3) + 主动共情 (4) + 关系里程碑 (5)
→ 这四项完成后，Soul Mate 的核心体验就建立了
```

### Phase 2 — 个性深化（2-3 周）
```
P0: 个性演化 (2)
P1: 记忆优先级 (7) + 动态风格 (9) + 长期话题跟踪 (10)
→ 让 Soul Mate 开始"成长"和"个性化"
```

### Phase 3 — 体验丰富（2-3 周）
```
P1: 多模态深化 (6) + 共享空间 (8)
P2: Meta认知 (11) + 用户画像 (12) + 风格多样性 (13) + 情绪同步 (14) + 互动彩蛋 (15)
→ 锦上添花，让体验从"好"变成"惊艳"
```

---

## 四、技术架构影响评估

### 新增目录结构
```
claw_soul/core/
├── affect/                  # 新增: 情感系统
│   ├── emotional_graph.py
│   ├── sentiment_analyzer.py
│   └── relationship_store.py
├── evolution/               # 新增: 个性演化
│   ├── trait_manager.py
│   └── evolution_prompt.py
├── memory/
│   ├── temporal_index.py    # 新增: 时间线索引
│   └── priority.py          # 新增: 记忆优先级
├── interaction/
│   ├── style_manager.py     # 新增: 互动风格
│   └── topic_tracker.py     # 新增: 话题跟踪
├── portrait/
│   ├── user_portrait.py     # 新增: 用户画像
│   └── shared_space.py      # 新增: 共享空间
└── agent.py                 # 改动: 集成以上新模块
```

### 需要改动的现有文件

| 文件 | 改动量 | 改动内容 |
|------|--------|---------|
| `agent.py` | 中等 | 集成情感分析、个性演化、风格选择到 system prompt |
| `persistent_agent.py` | 小 | save 时同时持久化情感图谱和时间线 |
| `memory/manager.py` | 中等 | 增加 importance 参数, temporal index 写入 |
| `memory/storage.py` | 小 | 增加重要性字段 |
| `compaction.py` | 小 | compaction 时考虑情感重要性和优先级 |
| `tools.py` | 小 | 新增 memory_search 工具 |
| `scheduler/proactive.py` | 大 | 重写为情感感知 + 话题追踪的主动消息 |
| `channels/telegram_bot.py` | 小 | 多模态分析钩子 |
| `config.py` | 无 | 不需要改动配置系统 |

### 持久化存储新增
```
~/.claw_soul/context/
├── affect/
│   ├── emotional_graph.jsonl     # 情感事件流
│   └── relationship.json         # 关系状态
├── evolution/
│   └── traits.json               # 性格特质
├── memory/
│   ├── timeline.jsonl            # 时间线索引
│   └── topics.json               # 话题追踪
├── profile/
│   └── user_portrait.json        # 用户画像
└── shared_space/
    └── space.md                  # 共享空间 Markdown
```

### 风险点
1. **Token 膨胀**：新增的情感摘要、关系状态、用户画像等都会增加 system prompt 长度。建议全部控制在 500 tokens 以内，并使用 compaction 定期清理旧的情感事件。
2. **记忆准确性**：自动提取的时间线和情感可能不准确。建议保留 LLM 提取的原始摘要，让用户可以通过工具修正。
3. **性能**：情感分析如果每个消息都做会增加延迟。建议用轻量方法（LLM 作为 chat 的 side-effect 提取，而不是单独调用）。
4. **过度主动**：主动共情 + 话题追踪可能导致消息过多。建议保留当前的 daily cap 机制，并新增"烦人指数"动态调整频率。

---

## 五、快速启动建议

建议从 **Phase 1 的第 1 项（情感图谱）** 开始，因为它是后面所有功能的基础：

1. 先实现 `SentimentAnalyzer` — 给每个用户消息打情感标签
2. 然后实现 `EmotionalGraph` — 存储情感事件
3. 在 agent.py 的 `chat()` 方法最后加一步：分析刚结束的对话，更新情感图和关系状态
4. 在 system prompt 注入情感摘要
5. 扩展 proactive 为情感感知

这样做完，用户会立刻感受到变化——Agent 开始"理解"他们的情绪了。这是 Soul Mate 体验最核心的飞跃。
