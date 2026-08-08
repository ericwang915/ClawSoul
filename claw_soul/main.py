"""
ClawSoul CLI — entry point.

Subcommands
-----------
  onboard   Interactive first-time setup wizard
  start     Start the agent daemon (web dashboard + Telegram)
  stop      Stop the running daemon
  status    Show daemon status
  chat      Interactive CLI chat (foreground)
"""

import argparse
import logging
import sys

from . import config
from .core.persistent_agent import PersistentAgent
from .core.session_store import SessionStore

logger = logging.getLogger(__name__)

# ── Provider builder ─────────────────────────────────────────────────────────

def _build_provider():
    """Instantiate the LLM provider, wrapping with a vision fallback if needed.

    If the configured primary can't see images AND a Gemini key is available,
    we transparently wrap it with a ``RoutingProvider`` that dispatches each
    chat turn to whichever model can handle it. Text → cheap primary;
    image → Gemini.
    """
    primary = _build_primary_provider()
    if getattr(primary, "supports_images", False):
        return primary

    # Vision fallback: only built if the user has a Gemini key. Failures
    # here are non-fatal — we keep the primary and accept "no vision".
    gemini_key = config.get_str("llm", "gemini", "apiKey", env="GEMINI_API_KEY")
    if not gemini_key:
        return primary

    try:
        from .core.llm.gemini_client import GeminiProvider
        from .core.llm.routing import RoutingProvider
        vision = GeminiProvider(
            api_key=gemini_key,
            model_name=config.get_str(
                "llm", "gemini", "model", default="gemini-2.5-flash",
            ),
        )
        logger.info(
            "[Provider] Routing %s (text) + %s (vision fallback)",
            getattr(primary, "model_name", "?"),
            getattr(vision, "model_name", "?"),
        )
        return RoutingProvider(primary, vision)
    except Exception as exc:
        logger.warning("[Provider] Vision fallback unavailable: %s", exc)
        return primary


# OpenAI-compatible providers — the vast majority. Each entry is
#   key: (aliases, env var, default base URL, default model, needs_key)
# Adding a vendor is one line here; nothing else in the codebase changes.
_OPENAI_COMPATIBLE: dict[str, dict] = {
    "openai": {
        "aliases": (),
        "env": "OPENAI_API_KEY",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "openrouter": {
        "aliases": (),
        "env": "OPENROUTER_API_KEY",
        "base": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
    },
    "ollama": {
        # Local models: no key, no upstream provider, no content policy.
        "aliases": ("local",),
        "env": "OLLAMA_API_KEY",
        "base": "http://localhost:11434/v1",
        "model": "llama3.1",
        "needs_key": False,
    },
    "deepseek": {
        "aliases": (),
        "env": "DEEPSEEK_API_KEY",
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "grok": {
        "aliases": ("xai",),
        "env": "GROK_API_KEY",
        "base": "https://api.x.ai/v1",
        "model": "grok-3",
    },
    "kimi": {
        "aliases": ("moonshot",),
        "env": "KIMI_API_KEY",
        "base": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-128k",
    },
    "glm": {
        "aliases": ("zhipu", "chatglm"),
        "env": "GLM_API_KEY",
        "base": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4-flash",
    },
    "qwen": {
        "aliases": ("dashscope", "tongyi"),
        "env": "QWEN_API_KEY",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "mistral": {
        "aliases": (),
        "env": "MISTRAL_API_KEY",
        "base": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
    },
    "groq": {
        "aliases": (),
        "env": "GROQ_API_KEY",
        "base": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
    },
    "together": {
        "aliases": (),
        "env": "TOGETHER_API_KEY",
        "base": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
    "siliconflow": {
        "aliases": ("silicon",),
        "env": "SILICONFLOW_API_KEY",
        "base": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
    },
    "lmstudio": {
        "aliases": (),
        "env": "LMSTUDIO_API_KEY",
        "base": "http://localhost:1234/v1",
        "model": "local-model",
        "needs_key": False,
    },
    "custom": {
        # Any other OpenAI-compatible endpoint: set baseUrl + model yourself.
        "aliases": (),
        "env": "CUSTOM_API_KEY",
        "base": "",
        "model": "",
        "needs_key": False,
    },
}

# Alias → canonical key, resolved once.
_PROVIDER_ALIASES = {
    alias: key
    for key, spec in _OPENAI_COMPATIBLE.items()
    for alias in spec["aliases"]
}


def _build_primary_provider():
    """Instantiate the LLM provider selected by config.

    Anything with an OpenAI-compatible API is table-driven (see
    ``_OPENAI_COMPATIBLE``); Anthropic and Gemini have native SDKs and are
    handled separately.
    """
    provider_name = config.get_str(
        "llm", "provider", env="LLM_PROVIDER", default="deepseek"
    ).lower()
    provider_name = _PROVIDER_ALIASES.get(provider_name, provider_name)

    if provider_name in ("claude", "anthropic"):
        from .core.llm.anthropic_client import AnthropicProvider
        api_key = config.get_str("llm", "claude", "apiKey", env="ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set (env or claw_soul.json)")
        return AnthropicProvider(
            api_key=api_key,
            model_name=config.get_str(
                "llm", "claude", "model", default="claude-sonnet-4-20250514",
            ),
        )

    if provider_name == "gemini":
        from .core.llm.gemini_client import GeminiProvider
        api_key = config.get_str("llm", "gemini", "apiKey", env="GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set (env or claw_soul.json)")
        return GeminiProvider(api_key=api_key)

    spec = _OPENAI_COMPATIBLE.get(provider_name)
    if spec is None:
        known = ", ".join(sorted(_OPENAI_COMPATIBLE) + ["claude", "gemini"])
        raise ValueError(f"Unknown LLM_PROVIDER: '{provider_name}'. Known: {known}")

    from .core.llm.openai_compatible import OpenAICompatibleProvider
    api_key = config.get_str("llm", provider_name, "apiKey", env=spec["env"])
    if not api_key:
        if spec.get("needs_key", True):
            raise ValueError(f"{spec['env']} not set (env or claw_soul.json)")
        # Local servers accept any non-empty token.
        api_key = "local"

    base_url = config.get_str("llm", provider_name, "baseUrl", default=spec["base"])
    model = config.get_str("llm", provider_name, "model", default=spec["model"])
    if not base_url or not model:
        raise ValueError(
            f"Provider '{provider_name}' needs llm.{provider_name}.baseUrl and "
            f"llm.{provider_name}.model set in claw_soul.json"
        )
    return OpenAICompatibleProvider(
        api_key=api_key, base_url=base_url, model_name=model,
    )


# ── Ensure config is ready (auto-onboard if needed) ─────────────────────────

def _ensure_configured(config_path: str | None = None) -> None:
    """Run the onboard wizard when no LLM key is configured — but ONLY with a
    real terminal. In a container / piped context (no TTY) the wizard's input()
    would EOFError-crash and the container would crash-loop; instead we let the
    web dashboard boot in a "needs setup" state so the user can add their key in
    the browser (or via env / the config API)."""
    from .onboard import needs_onboard, run_onboard

    if not needs_onboard(config_path):
        return
    if sys.stdin.isatty():
        print("[ClawSoul] No LLM provider configured. Starting setup wizard...\n")
        run_onboard(config_path)
    else:
        print("[ClawSoul] No LLM key yet — starting the dashboard in setup mode. "
              "Open http://localhost:7788 to finish setup.")


# ── Subcommand handlers ─────────────────────────────────────────────────────

def _cmd_onboard(args) -> None:
    from .onboard import run_onboard
    run_onboard(args.config)


def _cmd_start(args) -> None:
    _ensure_configured(args.config)

    if args.foreground:
        _run_foreground(args)
    else:
        from .daemon import start_daemon
        start_daemon(config_path=args.config)


def _run_foreground(args) -> None:
    """Run the web server + Telegram bot in the foreground."""
    provider = None
    try:
        provider = _build_provider()
    except Exception as exc:
        print(f"[ClawSoul] Warning: LLM provider not configured ({exc})")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        print("Error: Web mode requires 'fastapi' and 'uvicorn'.")
        print("Install with: pip install claw_soul")
        return

    from .web.app import create_app

    host = config.get_str("web", "host", default="0.0.0.0")
    port = config.get_int("web", "port", default=7788)

    app = create_app(provider, build_provider_fn=_build_provider)

    # Auto-start the Telegram bot when a token is configured. Without one, the
    # web dashboard runs on its own (you can add a token later in the browser).
    tg_token = config.get_str("channels", "telegram", "token", default="")
    if tg_token:
        from .server import start_telegram
        from .web import app as web_app_module
        print("[ClawSoul] Starting Telegram bot…")

        @app.on_event("startup")
        async def _start_telegram():
            bots = await start_telegram(provider, fastapi_app=app)
            web_app_module._active_bots.extend(bots)

    print(f"[ClawSoul] Web dashboard: http://localhost:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def _cmd_stop(args) -> None:
    from .daemon import stop_daemon
    stop_daemon()


def _cmd_status(args) -> None:
    from .daemon import print_status
    print_status()


def _cmd_storage(args) -> None:
    """Inspect / prune the unified storage DB."""
    from .core.storage import StorageManager

    sm = StorageManager.instance()
    sub = getattr(args, "storage_cmd", None) or "status"

    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    if sub == "status":
        s = sm.status()
        print()
        print(f"  DB:        {s['path']}")
        print(f"  Disk use:  {_fmt_size(s['size_bytes'])}")
        print()
        for name in ("events", "turns"):
            sec = s[name]
            print(f"  {name.upper():<8}  count={sec['count']:,}  "
                  f"retention={sec['retention_days']}d")
            if sec["count"]:
                print(f"            oldest={sec['oldest']}  newest={sec['newest']}")
        print()
        return

    if sub == "prune":
        res = sm.prune(dry_run=bool(getattr(args, "dry_run", False)))
        print()
        tag = "[DRY-RUN] would delete" if res["dry_run"] else "Deleted"
        print(f"  {tag}: events={res['events_deleted']:,}  turns={res['turns_deleted']:,}")
        if res["vacuumed"]:
            print("  VACUUM completed.")
        print()
        return

    print("  Usage: claw_soul storage {status|prune [--dry-run]}")


def _cmd_chat(args) -> None:
    _ensure_configured(args.config)

    try:
        provider = _build_provider()
    except Exception as exc:
        print(f"Error: {exc}")
        return

    provider_name = config.get_str("llm", "provider", env="LLM_PROVIDER", default="deepseek")
    verbose = config.get("agent", "verbose", default=True)

    store = SessionStore()
    session_id = "cli"

    print(f"Initializing ClawSoul with Provider: {provider_name.upper()}...")
    agent = PersistentAgent(
        provider=provider,
        verbose=bool(verbose),
        store=store,
        session_id=session_id,
    )
    print(f"Loaded {len(agent.loaded_skill_names)} active skills.")

    restored = len(agent.messages) - 1
    if restored > 0:
        print(f"Restored {restored} messages from previous session.")

    cfg_path = config.config_path()
    cfg_source = f" (config: {cfg_path})" if cfg_path else ""
    print("\n--- ClawSoul ---")
    print(f"Provider: {provider_name}{cfg_source}")
    print(f"Session: {store._path(session_id)}")
    print("Commands: 'exit' to quit | '/compact [hint]' | '/status' | '/clear'")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break

            if user_input.startswith("/compact"):
                hint = user_input[len("/compact"):].strip() or None
                result = agent.compact(instruction=hint)
                print(f"Bot: {result}")
                continue

            if user_input == "/status":
                memory_count = len(agent.memory.list_all())
                print(
                    f"Bot: Session Status\n"
                    f"  Provider     : {type(agent.provider).__name__}\n"
                    f"  Skills       : {len(agent.loaded_skill_names)} loaded\n"
                    f"  Memories     : {memory_count} entries\n"
                    f"  History      : {len(agent.messages)} messages\n"
                    f"  Compactions  : {agent.compaction_count}\n"
                    f"  Session File : {store._path(session_id)}"
                )
                continue

            if user_input == "/clear":
                store.delete(session_id)
                agent.clear_history()
                print("Bot: Chat history cleared. Agent is still active with all skills and memory intact.")
                continue

            response = agent.chat(user_input)
            print(f"Bot: {response}")
        except KeyboardInterrupt:
            print("\nExiting...")
            break


# ── Argument parser ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claw_soul",
        description="ClawSoul — Your Virtual AI Partner (Boyfriend or Girlfriend) on Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  claw_soul onboard       Set up your LLM provider\n"
            "  claw_soul start         Start the agent daemon\n"
            "  claw_soul chat          Interactive CLI chat\n"
            "\n"
            "Docs: https://github.com/ericwang915/ClawSoul"
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to claw_soul.json config file.",
    )

    sub = parser.add_subparsers(dest="command")

    # onboard
    sub.add_parser("onboard", help="Interactive first-time setup wizard")

    # start
    sp_start = sub.add_parser("start", help="Start the agent daemon")
    sp_start.add_argument(
        "--foreground", "-f", action="store_true",
        help="Run in foreground (don't daemonize)",
    )

    # stop
    sub.add_parser("stop", help="Stop the running daemon")

    # status
    sub.add_parser("status", help="Show daemon status")

    # chat
    sub.add_parser("chat", help="Interactive CLI chat (foreground)")

    # storage status / prune
    sp_storage = sub.add_parser(
        "storage", help="Inspect or maintain the unified event/turn database.",
    )
    sp_storage_sub = sp_storage.add_subparsers(dest="storage_cmd")
    sp_storage_sub.add_parser(
        "status", help="Show size, row counts, oldest/newest entries, retention.",
    )
    sp_prune = sp_storage_sub.add_parser(
        "prune", help="Delete rows past retention; VACUUM the DB.",
    )
    sp_prune.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be deleted without actually deleting.",
    )

    return parser


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    config.load()

    parser = _build_parser()
    args = parser.parse_args()

    if args.config:
        config.load(args.config, force=True)

    dispatch = {
        "onboard": _cmd_onboard,
        "start": _cmd_start,
        "stop": _cmd_stop,
        "status": _cmd_status,
        "chat": _cmd_chat,
        "storage": _cmd_storage,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
