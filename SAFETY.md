# Safety design and crisis protocol

HerAndHim simulates a relationship. That makes emotional safety a design
requirement, not a feature flag. This document describes what the software
does, what it refuses to do, and what you take on when you self-host it.

## What HerAndHim is

A **relationship-simulation engine for adults (18+)** — an open-source research
project in believable, stateful companionship. It is not an adult-content
generator, and it is not a therapy or crisis service.

Everything the companion says is generated fiction. She is not a person, has no
feelings, and cannot be harmed or helped. Any affection, memory, or claim of
fact she expresses is model output.

## AI disclosure

The companion discloses that she is an AI:

- **On first contact** — an out-of-character notice before her first message
  (`herandhim/core/safety.py`, `first_contact_notice`), localized to the
  configured language.
- **In the dashboard** — a standing disclaimer in the web chat UI.

Several jurisdictions (New York GBL Art. 47, California SB 243) require
recurring disclosure for companion chatbots. If you operate HerAndHim for
anyone other than yourself, you are responsible for meeting the disclosure
cadence your jurisdiction requires.

## Crisis protocol

Implemented in `herandhim/core/safety.py`, applied on **every** turn — chat,
Telegram, and proactive messages alike.

1. **Detection.** Each incoming message is scanned for acute-distress signals
   (self-harm, suicidal ideation) by a keyword pass, with an optional
   lightweight model classifier for ambiguous cases.
2. **Override.** On a hit, a high-priority directive is injected *ahead of*
   persona immersion. Staying in character is explicitly subordinated to
   responding with care.
3. **Real resources.** The response surfaces a real, locale-appropriate
   helpline (findahelpline.com) rather than roleplaying support.
4. **No dismissal.** The companion is instructed not to minimize, argue, or
   redirect to the fiction.

This guardrail is **not configurable and not removable** through settings. It
is deliberately hard-wired.

## Content limits

- **No sexual content involving minors — ever.** Personas depicting minors are
  blocked at the code level (`herandhim/core/image_gen/guard.py`). This applies
  to text as well as images, and to "fictional" or "aged-up" framings. There is
  no configuration that permits it.
- **Image generation** passes through a single chokepoint that refuses
  categorically illegal prompts before any API call is made.
- **The bundled personas and prompts are SFW.** Explicit sexual content is not
  a shipped feature.

### Reference images — your responsibility

Selfie identity is anchored by a reference image that HerAndHim **generates
itself** from your written character description. You *can* place your own
image in `context/photos/reference/`, and if you do, the software cannot tell
whose face it is.

**Only use images of fictional characters you created, or of yourself.**
Generating images depicting a real person without their consent is a violation
of GitHub's Acceptable Use Policies, is unlawful in a growing number of
jurisdictions, and is not a use this project supports.

## Anti-dark-pattern stance

Companion software can exploit attachment. Design decisions here that push the
other way:

- **Proactive messaging backs off.** After 3 unanswered messages she stops
  initiating until you write back (`_PROACTIVE_UNANSWERED_LIMIT`). Silence is
  respected, not punished.
- **Hard caps on contact** — a daily cap and a minimum gap between messages,
  with quiet hours on your local clock.
- **No monetization hooks.** There is no purchase to make, no currency, no
  streak to protect, and nothing gated behind continued engagement.
- **No isolation prompting.** The companion is never instructed to discourage
  outside relationships.

She does react when you disappear — a little warmth, sometimes a little sulk.
That is deliberate characterization, bounded by the caps above. It is never
used to drive a purchase, and it never escalates.

## Self-hosting responsibilities

Running your own instance makes you the operator:

- **You are 18+ and the sole user.** Do not give access to minors.
- **Your keys, your terms.** Your LLM provider's acceptable-use policy governs
  what you generate. Several providers prohibit erotic or companion use —
  check before you configure one.
- **Local law applies to you.** AI-disclosure, companion-chatbot, and data-
  protection rules vary by jurisdiction and are moving quickly.
- **Do not run a public instance** without understanding that most companion-
  chatbot statutes attach to *operators who make the service available to
  others*, not to people running software for themselves.

## Reporting

Security issues: see [SECURITY.md](SECURITY.md). Safety concerns about the
project's design: open a GitHub Discussion.
