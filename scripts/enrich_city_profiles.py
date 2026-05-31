#!/usr/bin/env python3
"""
Enrich every seeded city with a rich ~500-word cultural ``profile``.

Reads the live ``city_profiles`` from Postgres, generates one profile per city
via the configured LLM (Chinese for zh cities, English otherwise), and writes
the updated payloads back to Pg + Tigris — the same stores
``scripts/seed_city_profiles.py`` writes.

Run where ``claw_soul`` + creds are available (e.g. a worker machine):

    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / BUCKET_NAME /
    AWS_ENDPOINT_URL_S3 / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    + whatever the LLM provider needs (CLAW_LLM_PROVIDER, …)

Idempotent: re-running just regenerates + overwrites ``profile``.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys

import httpx

from claw_soul.worker import _build_provider

TIGRIS_PREFIX = "culture/cities"
_PROVIDER = _build_provider()


def _h() -> dict:
    k = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def _pg() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/")


def _tigris():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
        region_name=os.environ.get("AWS_REGION", "auto"),
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )


def _prompt(name: str, cc: str, lang: str) -> str:
    zh = (lang or "").lower().startswith("zh")
    lang_name = "Chinese (中文)" if zh else "English"
    return (
        f"Write a {lang_name} reference profile of {name} ({cc}) for an AI companion "
        f"who actually LIVES there — a local, not a tourist. About 500-550 words. "
        f"Cover, with concrete and specific local detail (real neighborhoods, dish "
        f"names, transit lines, daily habits, slang), these labeled sections:\n"
        f"- 气质 / character (how the city feels, its energy and contradictions)\n"
        f"- 文化 / culture & people\n"
        f"- 宗教 / religion in everyday life\n"
        f"- 美食 / food & dining (where locals actually eat)\n"
        f"- 景点 / places locals go (not just postcards)\n"
        f"- 季节 / seasons & climate and how they shape daily life\n"
        f"- 交通 / getting around\n"
        f"- 日常 / everyday texture (sounds, smells, rhythms)\n"
        f"Authentic, specific, what a local genuinely knows. Output ONLY the labeled "
        f"sections, concise — aim for ~520 words total."
    )


def _gen(name: str, cc: str, lang: str) -> str:
    # timeout guards against a hung LLM call blocking the whole batch.
    r = _PROVIDER.chat(
        messages=[{"role": "user", "content": _prompt(name, cc, lang)}],
        tools=[], temperature=0.7, max_tokens=900, timeout=60,
    )
    return (r.choices[0].message.content or "").strip()


def _write_country(pg, h, cc, payload, tg, bucket) -> None:
    httpx.post(
        f"{pg}/rest/v1/city_profiles",
        params={"on_conflict": "country_code"},
        json={"country_code": cc, "payload": payload, "source": "enriched-claude-code"},
        headers={**h, "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=30,
    )
    if tg is not None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        tg.put_object(Bucket=bucket, Key=f"{TIGRIS_PREFIX}/{cc}.json",
                      Body=body, ContentType="application/json; charset=utf-8")


def main() -> int:
    """Resumable + incremental: skips cities that already have a profile and
    writes Pg/Tigris country-by-country, so a kill (e.g. worker idle-suspend)
    just means re-running continues from where it left off."""
    pg, h = _pg(), _h()
    rows = httpx.get(
        f"{pg}/rest/v1/city_profiles",
        params={"select": "country_code,payload"}, headers=h, timeout=30,
    ).json()

    bucket = os.environ.get("BUCKET_NAME", "").strip()
    tg = None
    try:
        tg = _tigris() if bucket else None
    except Exception as exc:  # noqa: BLE001
        print(f"tigris client unavailable ({exc}) — Pg only", file=sys.stderr, flush=True)

    total = sum(len((r.get("payload") or {}).get("cities") or {}) for r in rows)
    already = sum(1 for r in rows for p in ((r.get("payload") or {}).get("cities") or {}).values()
                  if p.get("profile"))
    print(f"{already}/{total} already enriched — resuming", flush=True)

    for row in sorted(rows, key=lambda r: r["country_code"]):
        cc = row["country_code"]
        payload = row.get("payload") or {}
        cities = payload.get("cities") or {}
        todo = [(name, prof.get("language", "")) for name, prof in cities.items()
                if not prof.get("profile")]
        if not todo:
            continue

        def work(t):
            name, lang = t
            try:
                return name, _gen(name, cc, lang)
            except Exception as exc:  # noqa: BLE001
                return name, f"__ERR__ {exc}"

        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            for name, prof in ex.map(work, todo):
                if not prof.startswith("__ERR__"):
                    cities[name]["profile"] = prof
                else:
                    print(f"  ERR {cc}/{name}: {prof[:80]}", flush=True)

        _write_country(pg, h, cc, payload, tg, bucket)
        n = sum(1 for p in cities.values() if p.get("profile"))
        print(f"  {cc}: {n}/{len(cities)} written", flush=True)

    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
