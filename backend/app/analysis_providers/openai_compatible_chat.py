import base64
import json
import socket
import ssl
import threading
import time
from typing import Any, Optional, Union

import httpcore
import httpx
from httpcore._backends.sync import SyncStream

from ..analysis_input import ImageAnalysisInput, VideoAnalysisInput
from ..analysis_prompt import analysis_input_context
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status


MAX_PROVIDER_RESPONSE_BYTES = 256_000
TOTAL_PROVIDER_DEADLINE_SECONDS = 120.0
_CANCELLATION_REGISTRY_ATTRIBUTE = "_aivre_active_socket_registry"


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


def new_cancellable_client(timeout: float = 30.0) -> httpx.Client:
    """Create the production client with socket-level deadline cancellation."""

    registry = _ActiveSocketRegistry()
    client = httpx.Client(
        transport=_CancellableHTTPTransport(registry),
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
    )
    setattr(client, _CANCELLATION_REGISTRY_ATTRIBUTE, registry)
    return client


def image_data_url(value: Union[ImageAnalysisInput, VideoAnalysisInput]) -> str:
    if isinstance(value, ImageAnalysisInput):
        image_bytes = value.analysisProxyBytes
    else:
        image_bytes = value.contactSheetBytes
    mime_type = "image/png" if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return "data:%s;base64,%s" % (mime_type, encoded)


def build_chat_payload(request: ProviderRequest) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
    if request.analysisInput is not None:
        content.append({"type": "text", "text": analysis_input_context(request.analysisInput)})
        content.append({"type": "image_url", "image_url": {"url": image_data_url(request.analysisInput)}})
    return {
        "model": request.model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }


def post_chat_completion(
    client: httpx.Client,
    url: str,
    credential: Optional[str],
    request: ProviderRequest,
    *,
    credential_required: bool = True,
) -> ProviderResult:
    if credential_required and (credential is None or not credential.strip()):
        raise ProviderAnalysisError(ProviderFailure.for_code("provider_unconfigured"))
    headers = {"Content-Type": "application/json"}
    if credential is not None and credential.strip():
        headers["Authorization"] = "Bearer " + credential
    started_at = time.monotonic()
    deadline_expired = threading.Event()
    response_holder = [None]
    registry = getattr(client, _CANCELLATION_REGISTRY_ATTRIBUTE, None)

    def cancel_at_deadline() -> None:
        deadline_expired.set()
        if registry is not None:
            registry.cancel_active()
            return
        response = response_holder[0]
        if response is None:
            client.close()
        else:
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
            "POST", url, headers=headers, json=build_chat_payload(request), follow_redirects=False,
            timeout=httpx.Timeout(TOTAL_PROVIDER_DEADLINE_SECONDS),
        ) as response:
            response_holder[0] = response
            if deadline_expired.is_set():
                raise httpx.TimeoutException("total provider deadline exceeded")
            if time.monotonic() - started_at > TOTAL_PROVIDER_DEADLINE_SECONDS:
                raise httpx.TimeoutException("total provider deadline exceeded")
            if response.status_code >= 300:
                raise ProviderAnalysisError(failure_for_status(response.status_code))
            response_bytes = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() - started_at > TOTAL_PROVIDER_DEADLINE_SECONDS:
                    raise httpx.TimeoutException("total provider deadline exceeded")
                if len(response_bytes) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise ValueError("响应超过安全边界")
                response_bytes.extend(chunk)
            request_id = response.headers.get("x-request-id")
    except httpx.TimeoutException:
        raise ProviderAnalysisError(ProviderFailure.for_code("timeout")) from None
    except httpx.RequestError:
        if deadline_expired.is_set():
            raise ProviderAnalysisError(ProviderFailure.for_code("timeout")) from None
        raise ProviderAnalysisError(ProviderFailure.for_code("network_error")) from None
    except ProviderAnalysisError:
        raise
    except (TypeError, ValueError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None
    finally:
        deadline_timer.cancel()
        deadline_timer.join()
        if registry is not None:
            registry.disarm()
    try:
        payload = json.loads(response_bytes.decode("utf-8"))
        raw_text = payload["choices"][0]["message"]["content"]
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None
    if not isinstance(raw_text, str):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response"))
    return ProviderResult(rawText=raw_text, providerRequestId=request_id)


__all__ = ["build_chat_payload", "image_data_url", "post_chat_completion"]
