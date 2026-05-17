#!/usr/bin/env python3
"""Read and update claw_soul.json configuration values."""

import argparse
import json
import os
import re
import sys


def _config_file() -> str:
    home = os.path.expanduser("~/.claw_soul")
    for p in [os.path.join(home, "claw_soul.json"), "claw_soul.json"]:
        if os.path.exists(p):
            return p
    return os.path.join(home, "claw_soul.json")


SENSITIVE_PATTERNS = re.compile(
    r"(apikey|api_key|token|password|secret)", re.IGNORECASE
)


def _load_config() -> dict:
    cfg_file = _config_file()
    if not os.path.exists(cfg_file):
        print(f"Error: {cfg_file} not found", file=sys.stderr)
        sys.exit(1)
    with open(cfg_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(cfg: dict) -> None:
    with open(_config_file(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _mask_value(key: str, value) -> str:
    if isinstance(value, str) and SENSITIVE_PATTERNS.search(key) and value:
        if len(value) <= 8:
            return "****"
        return value[:4] + "****" + value[-4:]
    return value


def _mask_dict(d: dict, parent_key: str = "") -> dict:
    masked = {}
    for k, v in d.items():
        full_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            masked[k] = _mask_dict(v, full_key)
        else:
            masked[k] = _mask_value(full_key, v)
    return masked


def _get_nested(d: dict, key_path: str):
    keys = key_path.split(".")
    current = d
    for k in keys:
        if not isinstance(current, dict) or k not in current:
            return None
        current = current[k]
    return current


def _set_nested(d: dict, key_path: str, value) -> None:
    keys = key_path.split(".")
    current = d
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]

    old_value = current.get(keys[-1])
    if isinstance(old_value, int):
        try:
            value = int(value)
        except (ValueError, TypeError):
            pass
    elif isinstance(old_value, bool) or (isinstance(value, str) and value.lower() in ("true", "false")):
        value = value.lower() in ("true", "1", "yes") if isinstance(value, str) else value
    elif isinstance(old_value, list) and isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [v.strip() for v in value.split(",") if v.strip()]

    current[keys[-1]] = value


def show_config():
    cfg = _load_config()
    masked = _mask_dict(cfg)
    print(json.dumps(masked, indent=2, ensure_ascii=False))


def get_value(key_path: str):
    cfg = _load_config()
    val = _get_nested(cfg, key_path)
    if val is None:
        print(f"Key '{key_path}' not found")
        sys.exit(1)
    masked = _mask_value(key_path, val) if isinstance(val, str) else val
    if isinstance(masked, dict):
        masked = _mask_dict(masked, key_path)
    print(json.dumps(masked, indent=2, ensure_ascii=False) if isinstance(masked, (dict, list)) else masked)


def set_value(key_path: str, value: str):
    cfg = _load_config()
    _set_nested(cfg, key_path, value)
    _save_config(cfg)
    display_val = _mask_value(key_path, value)
    print(f"Updated {key_path} = {display_val}")


def main():
    parser = argparse.ArgumentParser(description="Manage claw_soul.json")
    parser.add_argument("--show", action="store_true", help="Show all config (masked)")
    parser.add_argument("--get", metavar="KEY", help="Get a config value by dot-path")
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a config value")
    args = parser.parse_args()

    if args.show:
        show_config()
    elif args.get:
        get_value(args.get)
    elif args.set:
        set_value(args.set[0], args.set[1])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
