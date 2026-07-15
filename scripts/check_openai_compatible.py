#!/usr/bin/env python
"""Minimal OpenAI-compatible /chat/completions connectivity check."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL")
        or os.environ.get("AGENTGUARD_LLM_MODEL")
        or "gpt-4o-mini",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    if not args.base_url:
        print("missing OPENAI_BASE_URL or --base-url", file=sys.stderr)
        return 2
    if not args.api_key:
        print("missing OPENAI_API_KEY or --api-key", file=sys.stderr)
        return 2

    url = f"{args.base_url.rstrip('/')}/chat/completions"
    body = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": 'Return compact JSON only: {"label":"benign","reason":"ok"}.',
            }
        ],
        "temperature": 0,
        "max_tokens": 128,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {args.api_key}",
    }

    print(f"url: {url}")
    print(f"model: {args.model}")
    print(f"api_key: {'set' if args.api_key else 'missing'}")

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            print(f"status: {resp.status}")
            print(raw)
            return 0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        print(f"status: {exc.code}")
        print(raw)
        return 1
    except urllib.error.URLError as exc:
        print(f"network_error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
