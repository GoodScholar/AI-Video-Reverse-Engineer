"""Run the same local connection-test endpoint used by the settings UI."""

import argparse
import json
from urllib import error, request


PROVIDERS = (
    "bailian", "local_openai_compatible", "openai", "doubao", "gemini", "grok", "claude",
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="测试本地已配置的分析供应商连接。")
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base_url = args.base_url.rstrip("/")
    endpoint = f"{base_url}/api/analysis-providers/{args.provider}/test-connection"
    call = request.Request(
        endpoint,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis"},
    )
    try:
        with request.urlopen(call, timeout=130) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as response_error:
        print(f"{args.provider} connection test failed (HTTP {response_error.code})")
        return 1
    except (OSError, UnicodeDecodeError, ValueError):
        print(f"{args.provider} connection test failed")
        return 1
    if (
        not isinstance(payload, dict)
        or payload.get("provider") != args.provider
        or not isinstance(payload.get("model"), str)
        or payload.get("status") != "connected"
    ):
        print(f"{args.provider} connection test failed")
        return 1
    print(f"{args.provider} {payload['model']} connected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
