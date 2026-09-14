"""Run the same local connection-test endpoint used by the settings UI."""

import argparse
import json
from urllib import error, request
from urllib.parse import urlsplit


PROVIDERS = (
    "bailian", "local_openai_compatible", "openai", "doubao", "gemini", "grok", "claude",
)
_MAX_RESPONSE_BYTES = 64 * 1024


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _safe_loopback_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is None and parsed.netloc.endswith(":")
    ):
        raise ValueError
    return value.rstrip("/")


def _open_connection(call: request.Request, timeout: int):
    return request.build_opener(_NoRedirect()).open(call, timeout=timeout)


def _read_json(call: request.Request):
    with _open_connection(call, timeout=130) as response:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError
        return json.loads(body.decode("utf-8"))


def _provider_snapshot(payload, provider: str):
    if not isinstance(payload, list):
        return None
    for item in payload:
        if (
            isinstance(item, dict)
            and item.get("provider") == provider
            and isinstance(item.get("model"), str)
            and item["model"]
            and isinstance(item.get("configurationRevision"), str)
            and item["configurationRevision"]
            and isinstance(item.get("catalogVersion"), str)
            and item["catalogVersion"]
        ):
            return item
    return None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="测试本地已配置的分析供应商连接。")
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        base_url = _safe_loopback_base_url(args.base_url)
    except ValueError:
        print(f"{args.provider} connection test failed")
        return 1
    try:
        snapshot = _provider_snapshot(_read_json(request.Request(
            f"{base_url}/api/analysis-providers", method="GET",
        )), args.provider)
    except error.HTTPError as response_error:
        print(f"{args.provider} connection test failed (HTTP {response_error.code})")
        return 1
    except (OSError, UnicodeDecodeError, ValueError):
        print(f"{args.provider} connection test failed")
        return 1
    if snapshot is None:
        print(f"{args.provider} connection test failed")
        return 1
    endpoint = f"{base_url}/api/analysis-providers/{args.provider}/test-connection"
    call = request.Request(
        endpoint,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis"},
    )
    try:
        payload = _read_json(call)
    except error.HTTPError as response_error:
        print(f"{args.provider} connection test failed (HTTP {response_error.code})")
        return 1
    except (OSError, UnicodeDecodeError, ValueError):
        print(f"{args.provider} connection test failed")
        return 1
    if (
        not isinstance(payload, dict)
        or payload.get("provider") != args.provider
        or payload.get("model") != snapshot["model"]
        or payload.get("status") != "connected"
        or payload.get("verificationState") != "available"
        or payload.get("configurationRevision") != snapshot["configurationRevision"]
        or payload.get("catalogVersion") != snapshot["catalogVersion"]
    ):
        print(f"{args.provider} connection test failed")
        return 1
    print(f"{args.provider} {payload['model']} connected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
