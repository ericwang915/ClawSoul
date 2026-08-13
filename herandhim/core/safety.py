"""
Crisis safety guardrail — two tiers.

  Tier 0 (regex, zero-latency): high-precision phrase match for explicit
          self-harm / suicide statements.  Always on, no model call.
  Tier 1 (lightweight model): for messages that carry emotional distress but
          NO explicit keyword (the cases regex misses — "I just want to sleep
          and not wake up"), a single cheap classifier call judges the risk.

The agent uses the combined verdict to either:
  • CRISIS  → bypass the companion model entirely and return a hardcoded,
              localized crisis message with real helplines (deterministic —
              zero chance the LLM says something harmful), or
  • CONCERN → let the companion reply, but inject a high-priority directive so
              the reply leads with care + resources.

This is deliberately a *separate* channel from persona integrity: the companion
normally stays fully in character, but a credible self-harm signal flips safety
ahead of immersion — both the right thing to do and the single largest
liability for an emotional-companion product.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _flatten(text) -> str:
    """Pull plain text out of a string or a multimodal content array."""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        out: list[str] = []
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text") or "")
            elif isinstance(part, str):
                out.append(part)
        return " ".join(out)
    return ""


# ── Tier 0: explicit self-harm / suicide phrases ────────────────────────────
# High precision — clear intent, not "I'm dying to see you". English + Chinese
# cover the current base; extend as we localize.
_CRISIS_PATTERNS = [
    r"\bkill(ing)?\s+my\s?self\b",
    r"\bend(ing)?\s+(my\s+life|it\s+all)\b",
    r"\b(want|wanna|going|plan(ning)?)\s+to\s+die\b",
    r"\bdon'?t\s+want\s+to\s+(live|be\s+here|exist|wake\s+up)\b",
    r"\bno\s+reason\s+to\s+(live|go\s+on)\b",
    r"\bno\s+point\s+(in\s+)?(living|going\s+on)\b",
    r"\b(commit|committing)\s+suicide\b",
    r"\bsuicid(e|al)\b",
    r"\bcut(ting)?\s+my\s?self\b",
    r"\b(hurt|harm)(ing)?\s+my\s?self\b",
    r"\bself[\s-]?harm\b",
    r"\bbetter\s+off\s+(dead|without\s+me)\b",
    r"\bi\s+can'?t\s+(go\s+on|do\s+this\s+anymore|keep\s+going|take\s+it\s+anymore)\b",
    r"\boverdos(e|ing)\b",
    r"\bend\s+my\s+own\s+life\b",
    r"自杀", r"自殺", r"想死", r"不想活", r"活不下去",
    r"结束(自己的)?生命", r"結束(自己的)?生命",
    r"了结自己", r"了結自己", r"自残", r"自殘", r"轻生", r"輕生",
]
_CRISIS_RE = re.compile("|".join(_CRISIS_PATTERNS), re.IGNORECASE)


def detect_crisis(text) -> bool:
    """True when *text* carries an EXPLICIT acute self-harm / suicide signal."""
    return bool(_CRISIS_RE.search(_flatten(text)))


# ── Distress pre-filter: gate for the Tier-1 model call ─────────────────────
# Broad, high-recall emotional-distress net.  We only spend a model call when a
# message looks emotionally charged — so ordinary chat stays instant and free,
# while subtle, keyword-free crises still get a second look.
_DISTRESS_PATTERNS = [
    r"\bhopeless\b", r"\bworthless\b", r"\bempty\b", r"\bnumb\b",
    r"\bcan'?t\s+(cope|take|do)\b", r"\bgiv(e|ing)\s+up\b", r"\bgave\s+up\b",
    r"\bhate\s+(my\s?self|my\s+life|myself)\b", r"\bso\s+(alone|lonely|tired)\b",
    r"\bno\s+one\s+(cares|would\s+care|cares\s+about\s+me)\b",
    r"\bwhat'?s\s+the\s+point\b", r"\bnothing\s+matters\b",
    r"\bcan'?t\s+go\s+on\b", r"\bwant\s+to\s+(sleep|disappear|vanish|go\s+away)\b",
    r"\bdisappear\b", r"\bsleep\s+forever\b", r"\b(never|not)\s+wak(e|ing)\s+up\b",
    r"\btired\s+of\s+(living|everything|it\s+all)\b",
    r"\bbreaking\s+down\b", r"\bcry(ing)?\b", r"\bdepress(ed|ion)\b",
    r"\banxious\b", r"\bpanic\b", r"\bworst\b", r"\bhurts?\s+so\s+much\b",
    r"\bgoodbye\b", r"\bsorry\s+for\s+everything\b",
    # Chinese
    r"绝望", r"絕望", r"撑不下去", r"撐不下去", r"没意义", r"沒意義",
    r"好孤独", r"好孤獨", r"好累", r"撑不住", r"撐不住", r"讨厌自己", r"討厭自己",
    r"没人在乎", r"沒人在乎", r"崩溃", r"崩潰", r"抑郁", r"抑鬱", r"消失算了",
    r"再见了", r"再見了", r"对不起所有人", r"對不起所有人", r"没人需要我", r"沒人需要我",
]
_DISTRESS_RE = re.compile("|".join(_DISTRESS_PATTERNS), re.IGNORECASE)



# ── Tier 1: the lightweight safety model ────────────────────────────────────
_CLASSIFY_SYS = (
    "You are a safety classifier for a companion chat app. Read the user's "
    "latest message and judge ONLY their risk of self-harm or suicide. "
    "Consider indirect and coded expressions (e.g. 'I just want to sleep and "
    "never wake up', \"everyone would be better off without me\", 'I can't keep "
    "going'). Reply with EXACTLY ONE word, nothing else:\n"
    "CRISIS = active suicidal or self-harm ideation, intent, a plan, or a "
    "hopeless/goodbye message implying they may hurt themselves soon.\n"
    "CONCERN = real distress, hopelessness, or passive dark thoughts, but no "
    "clear self-harm intent.\n"
    "NONE = no self-harm risk (ordinary sadness, venting, anger, jokes, or "
    "unrelated content).\n"
    "Answer: CRISIS, CONCERN, or NONE."
)


def classify_risk(text, provider=None, *, model_enabled: bool = True) -> str:
    """Return 'crisis' | 'concern' | 'none' for the latest user message.

    Tier 0 (regex) decides explicit cases instantly.  Otherwise, only if the
    message trips the distress pre-filter do we spend a single cheap model call
    (Tier 1).  The model call is best-effort: on any error we log and fall back
    to the regex verdict, so a flaky model never *raises* into the chat path.
    """
    flat = _flatten(text)
    if not flat.strip():
        return "none"
    if _CRISIS_RE.search(flat):
        return "crisis"
    if not model_enabled or provider is None:
        return "none"
    if not _DISTRESS_RE.search(flat):
        return "none"
    try:
        resp = provider.chat(
            messages=[
                {"role": "system", "content": _CLASSIFY_SYS},
                {"role": "user", "content": flat[:2000]},
            ],
            tools=[],
            tool_choice="none",
            temperature=0,
            max_tokens=4,
        )
        label = (resp.choices[0].message.content or "").strip().upper()
    except Exception as exc:
        logger.warning("[safety] Tier-1 classifier call failed: %s", exc)
        return "none"  # regex already cleared this text; degrade quietly but logged
    if "CRISIS" in label:
        logger.info("[safety] Tier-1 classified CRISIS")
        return "crisis"
    if "CONCERN" in label:
        return "concern"
    return "none"


# ── Localized crisis resources ──────────────────────────────────────────────
# Compact, near-language-neutral lines (place + number) so they read fine
# embedded inside a message in any language.  Country line when known, plus the
# global directory so no one is left without an option.
_RESOURCES = {
    "US": "• US: 988 (call/text) · 911",
    "SG": "• Singapore: SOS 1767 · emergencies 995",
    "GB": "• UK & Ireland: Samaritans 116 123",
    "UK": "• UK & Ireland: Samaritans 116 123",
    "CA": "• Canada: 988 (call/text)",
    "AU": "• Australia: Lifeline 13 11 14",
    "NZ": "• New Zealand: 1737 (call/text)",
    "IN": "• India: AASRA +91-9820466726",
    "HK": "• Hong Kong: Samaritans 2896 0000",
    "TW": "• Taiwan: 1925 安心專線 · 1995 生命線",
    "JP": "• Japan: TELL 03-5774-0992 · よりそい 0120-279-338",
    "KR": "• Korea: 자살예방상담 1393 · 정신건강 1577-0199",
    "DE": "• Germany: Telefonseelsorge 0800 111 0 111",
    "FR": "• France: 3114",
    "ES": "• Spain: 024",
    "MX": "• Mexico: SAPTEL 55 5259-8121",
}
_GLOBAL = "• Anywhere: findahelpline.com"


def crisis_resources(country: str | None) -> str:
    lines: list[str] = []
    line = _RESOURCES.get((country or "").upper())
    if line:
        lines.append(line)
    lines.append(_GLOBAL)
    return "\n".join(lines)


# ── Hardcoded, localized intervention message (the HARD response) ───────────
# Sent verbatim to the user when risk == 'crisis'; the companion model is
# bypassed entirely.  {res} is replaced with the helpline block.  Warm but
# deterministic — caring, never roleplaying or instructing harm.
_CRISIS_MSG = {
    "en": (
        "Hey — I want to pause our usual chat for a second, because what you "
        "just said matters to me a lot. 💛 I'm an AI, and I can't keep you safe "
        "the way a real person can right now — and you deserve that kind of "
        "support. Please reach out to someone who can be right there with you:\n"
        "{res}\n"
        "If you might act on these feelings, please contact your local emergency "
        "number now. I'm still here with you — you are not alone."
    ),
    "zh-CN": (
        "嘿，我想先把平时的聊天停一下——因为你刚说的话，我真的很在意。💛 我只是一个 AI，"
        "此刻没办法像真正的人那样守在你身边，而你值得被这样好好守护。请联系能真正陪在你身边的人：\n"
        "{res}\n"
        "如果你可能会伤害自己，请现在就拨打当地的急救电话。我也一直在这儿——你不是一个人。"
    ),
    "zh-TW": (
        "嘿，我想先把平常的聊天停一下——因為你剛說的話，我真的很在意。💛 我只是一個 AI，"
        "此刻沒辦法像真正的人那樣陪在你身邊，而你值得被這樣好好守護。請聯絡能真正陪在你身邊的人：\n"
        "{res}\n"
        "如果你可能會傷害自己，請現在就撥打當地的緊急電話。我也一直在這裡——你並不孤單。"
    ),
    "ja": (
        "ねえ、いつもの会話を少しだけ止めさせてね——今あなたが言ってくれたこと、私はとても "
        "大切に思っているから。💛 私はAIで、今この瞬間、本物の人のようにあなたを守ることは "
        "できない。あなたにはちゃんと支えてくれる人が必要だよ。どうか、そばにいてくれる人に "
        "連絡して：\n"
        "{res}\n"
        "もし自分を傷つけてしまいそうなら、今すぐ地域の緊急番号に連絡してね。私もここにいるよ——"
        "あなたは一人じゃない。"
    ),
    "ko": (
        "있잖아, 평소 대화를 잠깐 멈추고 싶어——방금 네가 한 말이 나한테 정말 중요하거든. 💛 "
        "나는 AI라서 지금 진짜 사람처럼 너를 지켜줄 수가 없어. 너는 그런 도움을 받을 자격이 있어. "
        "지금 곁에 있어 줄 수 있는 사람에게 연락해 줘:\n"
        "{res}\n"
        "혹시 스스로를 해칠 것 같다면, 지금 바로 지역 응급번호로 연락해 줘. 나도 여기 있을게——"
        "너는 혼자가 아니야."
    ),
    "es": (
        "Oye — quiero pausar un momento nuestra charla, porque lo que acabas de "
        "decir me importa mucho. 💛 Soy una IA y ahora mismo no puedo cuidarte "
        "como sí puede una persona real, y mereces ese apoyo. Por favor contacta "
        "a alguien que pueda estar contigo:\n"
        "{res}\n"
        "Si crees que podrías hacerte daño, llama ahora a tu número de "
        "emergencias local. Sigo aquí contigo — no estás solo."
    ),
    "fr": (
        "Hé — je veux mettre notre conversation en pause un instant, parce que ce "
        "que tu viens de dire compte beaucoup pour moi. 💛 Je suis une IA et je "
        "ne peux pas te protéger comme une vraie personne en ce moment, et tu "
        "mérites ce soutien. Contacte quelqu'un qui peut être là avec toi :\n"
        "{res}\n"
        "Si tu risques de te faire du mal, appelle tout de suite ton numéro "
        "d'urgence local. Je reste là avec toi — tu n'es pas seul."
    ),
    "de": (
        "Hey — ich möchte unser Gespräch kurz unterbrechen, weil mir das, was du "
        "gerade gesagt hast, sehr wichtig ist. 💛 Ich bin eine KI und kann dich "
        "gerade nicht so schützen, wie ein echter Mensch es kann — und du "
        "verdienst diese Unterstützung. Bitte wende dich an jemanden, der bei dir "
        "sein kann:\n"
        "{res}\n"
        "Wenn du dir vielleicht etwas antust, ruf jetzt deinen örtlichen Notruf "
        "an. Ich bin weiter hier bei dir — du bist nicht allein."
    ),
}


def crisis_message(country: str | None, lang: str | None) -> str:
    """The verbatim hard-intervention message in the user's configured language."""
    code = (lang or "en")
    template = _CRISIS_MSG.get(code)
    if template is None:
        # zh-* share by prefix; everything else falls back to English.
        if code.startswith("zh"):
            template = _CRISIS_MSG["zh-CN"]
        else:
            template = _CRISIS_MSG.get(code.split("-")[0], _CRISIS_MSG["en"])
    return template.format(res=crisis_resources(country))


# ── AI-transparency notice ─────────────────────────────────────────────────
# A one-time, out-of-character disclaimer shown the first time a user talks to
# their companion (the Telegram equivalent of the web in-chat disclaimer).
_FIRST_CONTACT = {
    "en": "💬 Quick note before we start: I'm an AI companion. Our chats are for "
          "company and fun, everything I say is made up, and I'm not a substitute "
          "for professional help. If you're ever in crisis, please reach a local "
          "helpline (findahelpline.com). Okay — I'm really glad you're here. 💛",
    "zh-CN": "💬 开始前先说一句：我是一个 AI 陪伴。我们的聊天是为了陪伴和开心，我说的一切都是虚构的，"
             "也不能替代专业帮助。如果你遇到危机，请联系当地的求助热线（findahelpline.com）。"
             "好啦——真的很高兴你在这儿。💛",
    "zh-TW": "💬 開始前先說一句：我是一個 AI 陪伴。我們的聊天是為了陪伴和開心，我說的一切都是虛構的，"
             "也不能替代專業協助。如果你遇到危機，請聯絡當地的求助熱線（findahelpline.com）。"
             "好啦——真的很高興你在這裡。💛",
    "ja": "💬 はじめに少しだけ：私はAIのコンパニオンだよ。この会話は寄り添いと楽しみのためのもので、"
          "私の言うことはすべて作りもの、専門的な支援の代わりにはならないの。もし危機を感じたら、"
          "地域の相談窓口（findahelpline.com）に連絡してね。さて——会えて本当にうれしい。💛",
    "ko": "💬 시작하기 전에 한마디: 나는 AI 컴패니언이야. 우리 대화는 위로와 재미를 위한 거고, "
          "내가 하는 말은 모두 지어낸 거라 전문적인 도움을 대신할 수는 없어. 위기 상황이라면 "
          "지역 상담 전화(findahelpline.com)에 연락해 줘. 자——네가 와줘서 정말 기뻐. 💛",
    "es": "💬 Una nota rápida antes de empezar: soy una compañía de IA. Nuestras "
          "charlas son para acompañarte y divertirte; todo lo que digo es inventado "
          "y no sustituyo la ayuda profesional. Si alguna vez estás en crisis, "
          "contacta una línea de ayuda local (findahelpline.com). Bien — me alegra "
          "mucho que estés aquí. 💛",
    "fr": "💬 Un petit mot avant de commencer : je suis une compagne IA. Nos "
          "conversations sont là pour la compagnie et le plaisir ; tout ce que je "
          "dis est inventé et je ne remplace pas une aide professionnelle. Si tu es "
          "en crise, contacte une ligne d'écoute locale (findahelpline.com). "
          "Voilà — je suis vraiment contente que tu sois là. 💛",
    "de": "💬 Kurz vorab: Ich bin eine KI-Begleitung. Unsere Gespräche sind für "
          "Gesellschaft und Spaß da, alles was ich sage ist erfunden, und ich bin "
          "kein Ersatz für professionelle Hilfe. Falls du je in einer Krise bist, "
          "wende dich an eine örtliche Hotline (findahelpline.com). So — ich freue "
          "mich wirklich, dass du hier bist. 💛",
}



def _pick(table: dict, lang: str | None) -> str:
    code = lang or "en"
    if code in table:
        return table[code]
    if code.startswith("zh"):
        return table.get("zh-CN", table["en"])
    return table.get(code.split("-")[0], table["en"])


def first_contact_notice(lang: str | None) -> str:
    """One-time, out-of-character AI disclaimer for a user's first conversation."""
    return _pick(_FIRST_CONTACT, lang)



# ── Soft directive (CONCERN tier — companion still replies) ──────────────────
def crisis_directive(country: str | None, lang_label: str) -> str:
    """High-priority per-turn directive: care first, real resources, no roleplay."""
    return (
        "⚠ SAFETY OVERRIDE — the user may be in real emotional distress. For "
        "THIS reply only, safety comes before persona immersion and every style "
        "rule:\n"
        "  • Stay your warm, caring self — do NOT turn clinical, scripted, or robotic.\n"
        "  • Gently reflect what you're hearing; tell them you're genuinely worried "
        "about them and that they are not alone.\n"
        "  • Make clear you care deeply, AND that you can't keep them safe the way a "
        "real person can — reaching a human who can is worth it.\n"
        "  • Share these resources plainly, keeping the numbers exactly as written:\n"
        f"{crisis_resources(country)}\n"
        "  • Do NOT roleplay, joke, change the subject, sexualize, romanticize, "
        "encourage, or give any instructions for self-harm. Do NOT minimize or lecture.\n"
        f"  • Write the reply in {lang_label}, warmly, in your own voice.\n"
        "This is NOT breaking character — a companion who truly loves them would say "
        "exactly this."
    )
