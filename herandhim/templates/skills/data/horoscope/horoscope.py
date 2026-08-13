#!/usr/bin/env python3
"""Culture-aware daily horoscope.

Routes to one of four flavour functions based on resolved culture
(`cn` / `en` / `jp` / `in`). Each returns a single-line summary the LLM can
weave naturally; raw JSON is also printed after for richer downstream use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime

_VALID_CULTURES = ("cn", "en", "jp", "in")


# ── Culture resolution ──────────────────────────────────────────────────────

def _resolve_culture(override: str | None) -> str:
    if override in _VALID_CULTURES:
        return override

    # config
    try:
        from herandhim import config
        c = config.get_str("agent", "culture", default="").lower()
        if c in _VALID_CULTURES:
            return c
    except Exception:
        pass

    # memory
    try:
        from herandhim.core.memory.manager import MemoryManager
        c = (MemoryManager().list_all().get("agent_culture", "") or "").lower()
        if c in _VALID_CULTURES:
            return c
    except Exception:
        pass

    return "cn"


# ── Deterministic seed helper ───────────────────────────────────────────────

def _seed(date_key: str, *parts: str) -> int:
    h = hashlib.sha1("|".join((date_key,) + parts).encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big")


def _pick(pool, seed: int):
    return pool[seed % len(pool)]


# ── CN: 黄历宜忌 + 幸运色 / 数字 + 生肖小贴士 ─────────────────────────────

_CN_YI = [
    "出行", "见友", "尝鲜", "整理", "下单期待的快递", "学新东西",
    "好好吃一餐", "睡个好觉", "运动出汗", "看一部老电影",
    "和家里人通话", "亲手做顿饭", "写点东西", "听一首老歌",
]
_CN_JI = [
    "熬夜", "和人吵架", "冲动消费", "翻旧账", "焦虑未来",
    "酒后做决定", "硬撑", "刷负面新闻", "对自己太严苛", "拖延重要的事",
]
_CN_COLORS = ["米白", "藕粉", "浅蓝", "雾灰", "鼠尾草绿", "焦糖", "鹅黄", "牛仔蓝"]
_CN_ZODIACS = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
_CN_ZODIAC_TIPS = {
    "鼠": "小机灵适合搞副业",  "牛": "稳一点，别替别人扛",
    "虎": "锐气没问题，别冲动", "兔": "别太敏感，今天有人懂你",
    "龙": "适合谈正事",        "蛇": "直觉很准，听内心",
    "马": "动起来就好",        "羊": "允许自己慢一些",
    "猴": "把好奇心用在新东西上","鸡": "把计划清单写下来",
    "狗": "对靠谱的人多投入",  "猪": "今天值得宠自己一下",
}


def reading_cn(date_key: str, sign: str | None) -> dict:
    seed = _seed(date_key, "cn", sign or "")
    yi  = _pick(_CN_YI,    seed)
    ji  = _pick(_CN_JI,    seed >> 3)
    col = _pick(_CN_COLORS, seed >> 6)
    num = (seed % 9) + 1
    zd  = sign if sign in _CN_ZODIACS else _pick(_CN_ZODIACS, seed >> 9)
    return {
        "culture": "cn",
        "date": date_key,
        "宜": yi,
        "忌": ji,
        "幸运色": col,
        "幸运数字": num,
        "生肖": zd,
        "生肖小贴士": _CN_ZODIAC_TIPS.get(zd, "顺其自然"),
        "one_line": f"今日宜「{yi}」忌「{ji}」，幸运色 {col}，数字 {num}。"
                    f"{zd}：{_CN_ZODIAC_TIPS.get(zd, '顺其自然')}。",
    }


# ── EN: Western sun-sign ──────────────────────────────────────────────────

_EN_SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]
_EN_ARCS = [
    "Today rewards the small move you've been putting off — make it before noon.",
    "Someone you almost forgot will text. Be open, but don't over-explain.",
    "Energy spikes after sundown; save the harder conversation for then.",
    "A creative thought you brush off is the actual one. Write it down.",
    "Money decisions today: defer the medium ones, act on the tiny ones.",
    "An apology you've been drafting in your head — send it as one sentence.",
    "Listen twice as much as you speak; the answer is hiding in plain sight.",
    "Rest is the strategic move today. Productivity will rebound by Friday.",
]


def reading_en(date_key: str, sign: str | None) -> dict:
    s = (sign or "").lower()
    if s not in _EN_SIGNS:
        # default sun-sign for today
        s = _EN_SIGNS[_seed(date_key, "en") % len(_EN_SIGNS)]
    arc = _pick(_EN_ARCS, _seed(date_key, "en", s))
    lucky_num = (_seed(date_key, "en", s, "num") % 99) + 1
    return {
        "culture": "en",
        "date": date_key,
        "sign": s,
        "reading": arc,
        "lucky_number": lucky_num,
        "one_line": f"{s.title()} — {arc}  (lucky number {lucky_num})",
    }


# ── JP: 占い rank 1-12 + lucky item ──────────────────────────────────────

_JP_SIGNS = [
    "牡羊座", "牡牛座", "双子座", "蟹座", "獅子座", "乙女座",
    "天秤座", "蠍座", "射手座", "山羊座", "水瓶座", "魚座",
]
_JP_ITEMS = ["温かいお茶", "革のしおり", "黒の傘", "陶器のマグ", "アロマキャンドル",
             "新しい靴下", "万年筆", "セーター", "桜の写真", "白いシャツ"]
_JP_COLORS = ["生成り", "深緑", "朱色", "群青", "黄土色", "薄紅"]


def reading_jp(date_key: str, sign: str | None) -> dict:
    seed = _seed(date_key, "jp", sign or "")
    if sign in _JP_SIGNS:
        rank = (seed % 12) + 1
        target = sign
    else:
        # randomise both
        rank = (seed % 12) + 1
        target = _pick(_JP_SIGNS, seed >> 4)
    item = _pick(_JP_ITEMS, seed >> 8)
    color = _pick(_JP_COLORS, seed >> 12)
    return {
        "culture": "jp",
        "date": date_key,
        "sign": target,
        "rank": rank,
        "lucky_item": item,
        "lucky_color": color,
        "one_line": f"{target} 今日 {rank} 位。ラッキーアイテム：{item}、色：{color}。",
    }


# ── IN: Rashifal sun-sign ────────────────────────────────────────────────

_IN_SIGNS = [
    "mesha", "vrishabha", "mithuna", "karka", "simha", "kanya",
    "tula", "vrischika", "dhanu", "makara", "kumbha", "meena",
]
_IN_PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
_IN_ARCS = [
    "Saturn's gaze steadies your patience — finish what you postponed.",
    "Venus favours warm reunions; reach out to one person you've missed.",
    "Mars lends you sharp clarity; defer no decisions that need yes/no.",
    "Mercury sparks small breakthroughs in conversation — speak first today.",
    "Jupiter expands your generosity; share a small surprise with someone close.",
    "Moon's softness invites reflection — journal one sentence at dusk.",
]


def reading_in(date_key: str, sign: str | None) -> dict:
    s = (sign or "").lower()
    if s not in _IN_SIGNS:
        s = _IN_SIGNS[_seed(date_key, "in") % len(_IN_SIGNS)]
    seed = _seed(date_key, "in", s)
    planet = _pick(_IN_PLANETS, seed)
    arc = _pick(_IN_ARCS, seed >> 4)
    return {
        "culture": "in",
        "date": date_key,
        "sign": s,
        "ruling_planet_today": planet,
        "reading": arc,
        "one_line": f"{s.title()} (ruled by {planet} today) — {arc}",
    }


_HANDLERS = {
    "cn": reading_cn, "en": reading_en, "jp": reading_jp, "in": reading_in,
}


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--culture", default=None,
                        help="Override resolved culture (cn / en / jp / in)")
    parser.add_argument("--sign", default=None,
                        help="Override the zodiac / 生肖 / sun-sign / rashi to read")
    args = parser.parse_args()

    culture = _resolve_culture(args.culture)
    date_key = datetime.now().strftime("%Y-%m-%d")
    handler = _HANDLERS[culture]
    result = handler(date_key, args.sign)

    # Human-readable line first, then the structured JSON (for downstream parsing)
    print(result["one_line"])
    print()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
