import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import httpcore
import httpx
from httpcore._backends.sync import SyncStream


MAX_PROVIDER_RESPONSE_BYTES = 256_000
TOTAL_PROVIDER_DEADLINE_SECONDS = 120.0
_CANCELLATION_REGISTRY_ATTRIBUTE = "_aivre_active_socket_registry"
_REQUEST_ID_HEADERS = ("x-request-id",)


@dataclass(frozen=True)
class ProviderHTTPResult:
    status_code: int
    json_body: Optional[Any]
    body_text: str
    request_id: Optional[str]


class ProviderHTTPTransportError(Exception):
    def __init__(self, failure_code: str):
        self.failure_code = failure_code
        super().__init__(failure_code)


class _ActiveSocketRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets = set()
        self._deadline = None

    def arm(self, deadline: float) -> None:
        with self._lock:
            self._deadline = deadline

    def disarm(self) -> None:
        with self._lock:
            self._deadline = None

    def remaining_timeout(self, requested_timeout: Optional[float]) -> Optional[float]:
        with self._lock:
            deadline = self._deadline
        if deadline is None:
            return requested_timeout
        remaining = max(0.0, deadline - time.monotonic())
        return remaining if requested_timeout is None else min(requested_timeout, remaining)

    def register(self, stream) -> object:
        network_socket = stream.get_extra_info("socket")
        if network_socket is not None:
            with self._lock:
                self._sockets.add(network_socket)
        return network_socket

    def discard(self, network_socket) -> None:
        if network_socket is not None:
            with self._lock:
                self._sockets.discard(network_socket)

    def replace(self, previous_socket, replacement_socket) -> None:
        with self._lock:
            if previous_socket is not None:
                self._sockets.discard(previous_socket)
            if replacement_socket is not None:
                self._sockets.add(replacement_socket)

    def cancel_active(self) -> None:
        with self._lock:
            active = tuple(self._sockets)
            self._sockets.clear()
        for network_socket in active:
            try:
                network_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                network_socket.close()
            except OSError:
                pass


class _RegisteredNetworkStream:
    def __init__(self, stream, registry: _ActiveSocketRegistry) -> None:
        self._stream = stream
        self._registry = registry
        self._socket = registry.register(stream)

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        return self._stream.read(max_bytes, self._registry.remaining_timeout(timeout))

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        self._stream.write(buffer, self._registry.remaining_timeout(timeout))

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            self._registry.discard(self._socket)

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        previous_socket = self._socket
        try:
            tls_socket = ssl_context.wrap_socket(
                previous_socket,
                server_hostname=server_hostname,
                do_handshake_on_connect=False,
            )
        except OSError as error:
            self.close()
            raise httpcore.ConnectError(str(error)) from error
        self._registry.replace(previous_socket, tls_socket)
        self._socket = tls_socket
        self._stream = SyncStream(tls_socket)
        try:
            tls_socket.settimeout(self._registry.remaining_timeout(timeout))
            tls_socket.do_handshake()
        except socket.timeout as error:
            self.close()
            raise httpcore.ConnectTimeout(str(error)) from error
        except OSError as error:
            self.close()
            raise httpcore.ConnectError(str(error)) from error
        return self

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _CancellableNetworkBackend:
    def __init__(self, registry: _ActiveSocketRegistry) -> None:
        self._backend = httpcore.SyncBackend()
        self._registry = registry

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return _RegisteredNetworkStream(
            self._backend.connect_tcp(
                host, port, self._registry.remaining_timeout(timeout), local_address, socket_options,
            ), self._registry,
        )

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return _RegisteredNetworkStream(
            self._backend.connect_unix_socket(
                path, self._registry.remaining_timeout(timeout), socket_options,
            ), self._registry,
        )

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class _CancellableHTTPTransport(httpx.HTTPTransport):
    def __init__(self, registry: _ActiveSocketRegistry) -> None:
        super().__init__(trust_env=False)
        original_pool = self._pool
        self._pool = httpcore.ConnectionPool(
            ssl_context=original_pool._ssl_context,
            max_connections=original_pool._max_connections,
            max_keepalive_connections=original_pool._max_keepalive_connections,
            keepalive_expiry=original_pool._keepalive_expiry,
            http1=original_pool._http1,
            http2=original_pool._http2,
            retries=original_pool._retries,
            local_address=original_pool._local_address,
            uds=original_pool._uds,
            network_backend=_CancellableNetworkBackend(registry),
            socket_options=original_pool._socket_options,
        )
        original_pool.close()


def new_provider_http_client(timeout: float = 30.0) -> httpx.Client:
    """Create a provider client that ignores environment proxies and redirects."""

    registry = _ActiveSocketRegistry()
    client = httpx.Client(
        transport=_CancellableHTTPTransport(registry),
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
    )
    setattr(client, _CANCELLATION_REGISTRY_ATTRIBUTE, registry)
    return client


def _request_id(headers: httpx.Headers) -> Optional[str]:
    for name in _REQUEST_ID_HEADERS:
        value = headers.get(name)
        if value:
            return value
    return None


def post_provider_json(
    client: httpx.Client,
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
) -> ProviderHTTPResult:
    """Send one JSON request within the common hardened provider boundary."""

    started_at = time.monotonic()
    deadline_expired = threading.Event()
    response_holder = [None]
    response_bytes = bytearray()
    registry = getattr(client, _CANCELLATION_REGISTRY_ATTRIBUTE, None)

    def cancel_at_deadline() -> None:
        deadline_expired.set()
        if registry is not None:
            registry.cancel_active()
            return
        response = response_holder[0]
        if response is None:
            client.close()
            return
        stream = response.extensions.get("network_stream")
        close_socket = getattr(stream, "get_extra_info", None)
        if close_socket is not None:
            try:
                network_socket = close_socket("socket")
                if network_socket is not None:
                    network_socket.shutdown(socket.SHUT_RDWR)
                    network_socket.close()
            except OSError:
                pass
        response.close()

    deadline_timer = threading.Timer(TOTAL_PROVIDER_DEADLINE_SECONDS, cancel_at_deadline)
    deadline_timer.daemon = True
    if registry is not None:
        registry.arm(started_at + TOTAL_PROVIDER_DEADLINE_SECONDS)
    deadline_timer.start()
    try:
        with client.stream(
            "POST", url, headers=dict(headers), json=dict(payload), follow_redirects=False,
            timeout=httpx.Timeout(TOTAL_PROVIDER_DEADLINE_SECONDS),
        ) as response:
            response_holder[0] = response
            if deadline_expired.is_set() or time.monotonic() - started_at > TOTAL_PROVIDER_DEADLINE_SECONDS:
                raise httpx.TimeoutException("total provider deadline exceeded")
            for chunk in response.iter_bytes():
                if time.monotonic() - started_at > TOTAL_PROVIDER_DEADLINE_SECONDS:
                    raise httpx.TimeoutException("total provider deadline exceeded")
                if len(response_bytes) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise ValueError("provider response exceeds boundary")
                response_bytes.extend(chunk)
            status_code = response.status_code
            request_id = _request_id(response.headers)
    except httpx.TimeoutException:
        raise ProviderHTTPTransportError("timeout") from None
    except httpx.RequestError:
        raise ProviderHTTPTransportError("timeout" if deadline_expired.is_set() else "network_error") from None
    except (TypeError, ValueError):
        raise ProviderHTTPTransportError("invalid_analysis_response") from None
    finally:
        deadline_timer.cancel()
        deadline_timer.join()
        if registry is not None:
            registry.disarm()

    response_body = bytes(response_bytes)
    body_text = response_body.decode("utf-8", errors="replace")
    try:
        json_body = json.loads(response_body.decode("utf-8"))
    except (TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
        json_body = None
    return ProviderHTTPResult(
        status_code=status_code,
        json_body=json_body,
        body_text=body_text,
        request_id=request_id,
    )


__all__ = [
    "MAX_PROVIDER_RESPONSE_BYTES",
    "TOTAL_PROVIDER_DEADLINE_SECONDS",
    "ProviderHTTPResult",
    "ProviderHTTPTransportError",
    "new_provider_http_client",
    "post_provider_json",
]
