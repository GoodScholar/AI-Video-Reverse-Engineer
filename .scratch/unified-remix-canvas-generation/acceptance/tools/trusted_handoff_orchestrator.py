#!/usr/bin/env python3
"""Authenticated, FD-keyed transport for trusted handoff closures."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from trusted_evidence.handoffs import (
    HandoffExportRequestV1,
    HandoffRegistryV1,
    HandoffTransferManifestV1,
    TransportJobV1,
    TransportReadyMarkerV1,
    TransportSealV1,
    TrustedHandoffStoreV1,
    VerifiedSigningDomainsV1,
    _ed_add,
    _ed25519_verify,
    _ed_mul,
    _ed_decode,
    _digest,
    _ED_D,
    _ED_L,
    _ED_Q,
    load_disjoint_signing_domains,
)


def _stage_path(stage_root: Path, name: str) -> Path:
    if not stage_root.is_absolute() or stage_root.is_symlink() or not stage_root.is_dir() or stat.S_IMODE(stage_root.stat().st_mode) != 0o700:
        raise ValueError("transport stage root must be an absolute 0700 non-symlink directory")
    return stage_root / name


def _stage_publish(stage_root: Path, name: str, payload: bytes) -> None:
    path = _stage_path(stage_root, name)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError("transport stage bytes differ")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=".transport-", dir=stage_root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != payload:
                raise ValueError("concurrent transport stage bytes differ")
        directory = os.open(stage_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _stage_read(stage_root: Path, name: str) -> bytes:
    path = _stage_path(stage_root, name)
    if path.is_symlink() or not path.is_file():
        raise ValueError("transport stage payload is missing")
    return path.read_bytes()


def _closure(source: TrustedHandoffStoreV1, export_request_ref: str, handoff_id: str, consumer_task: int):
    request_bytes = source.read_blob(export_request_ref)
    request = HandoffExportRequestV1.from_canonical_bytes(request_bytes)
    if request.handoff_id != handoff_id or consumer_task not in request.consumer_tasks or source.read_export_request_ref(handoff_id) != export_request_ref:
        raise ValueError("export request does not authorize this transport")
    record, pointer, record_bytes, pointer_bytes = HandoffRegistryV1(source)._closure(source, request.record_ref, handoff_id)
    if request.record_full_bytes_digest != _digest(record_bytes) or request.pointer_bytes_digest != _digest(pointer_bytes) or request.binding_set_digest != record.binding_set_digest:
        raise ValueError("export request closure digest mismatch")
    refs = tuple(sorted(ref for _, ref in record.bindings))
    if not refs or len(set(refs)) != len(refs):
        raise ValueError("transport closure blob refs are invalid")
    blobs = tuple(source.read_blob(ref) for ref in refs)
    return request, request_bytes, record, pointer, record_bytes, pointer_bytes, refs, blobs


def verify_export_request(source_store: TrustedHandoffStoreV1, export_request_ref: str, handoff_id: str, consumer_task: int) -> HandoffTransferManifestV1:
    request, request_bytes, _record, _pointer, record_bytes, pointer_bytes, refs, _blobs = _closure(source_store, export_request_ref, handoff_id, consumer_task)
    return HandoffTransferManifestV1(
        "trusted-handoff-transfer/v1", export_request_ref, _digest(request_bytes), "verification-only",
        handoff_id, "verification-only", consumer_task, request.record_ref, _digest(record_bytes), handoff_id,
        _digest(pointer_bytes), "verification-only", "verification-only", "verification-only", 0, None,
        None, None, refs,
    )


def export(job: TransportJobV1, source_store: TrustedHandoffStoreV1) -> HandoffTransferManifestV1:
    request, request_bytes, _record, _pointer, record_bytes, pointer_bytes, refs, _blobs = _closure(source_store, job.export_request_ref, job.handoff_id, job.consumer_task)
    if job.policy_activation_proof_ref is not None:
        raise ValueError("product transport must not carry a policy activation proof")
    manifest = HandoffTransferManifestV1(
        "trusted-handoff-transfer/v1", job.export_request_ref, _digest(request_bytes), job.store_namespace,
        job.handoff_id, job.transport_id, job.consumer_task, request.record_ref, _digest(record_bytes), job.handoff_id,
        _digest(pointer_bytes), job.source_executor_id, job.destination_executor_id, job.nonce, job.sequence,
        job.expires_at, job.policy_activation_proof_ref, job.policy_activation_proof_full_bytes_digest, refs,
    )
    _stage_publish(job.stage_root, "export-manifest.json", manifest.canonical_bytes)
    return manifest


def upload(job: TransportJobV1, source_store: TrustedHandoffStoreV1, target_store: TrustedHandoffStoreV1, manifest: HandoffTransferManifestV1) -> str:
    if HandoffTransferManifestV1.from_canonical_bytes(_stage_read(job.stage_root, "export-manifest.json")) != manifest:
        raise ValueError("upload manifest does not match exported bytes")
    request, request_bytes, _record, _pointer, record_bytes, pointer_bytes, refs, blobs = _closure(source_store, job.export_request_ref, job.handoff_id, job.consumer_task)
    if (manifest.export_request_ref, manifest.record_ref, manifest.record_full_bytes_digest, manifest.pointer_bytes_digest, manifest.blob_refs) != (job.export_request_ref, request.record_ref, _digest(record_bytes), _digest(pointer_bytes), refs):
        raise ValueError("upload manifest closure is mixed")
    for payload in (request_bytes, record_bytes, pointer_bytes, *blobs):
        target_store.put_blob(payload)
    target_store.publish_pointer(job.handoff_id, pointer_bytes)
    target_store.publish_export_request_ref(job.handoff_id, job.export_request_ref)
    receipt = {"manifestDigest": manifest.digest, "schema": "trusted-handoff-upload-receipt/v1"}
    receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _stage_publish(job.stage_root, "upload-receipt.json", receipt_bytes)
    return manifest.digest


def _base_point() -> tuple[int, int]:
    y = (4 * pow(5, _ED_Q - 2, _ED_Q)) % _ED_Q
    x = pow(((y * y - 1) * pow((_ED_D * y * y + 1) % _ED_Q, _ED_Q - 2, _ED_Q)) % _ED_Q, (_ED_Q + 3) // 8, _ED_Q)
    if x & 1:
        x = _ED_Q - x
    return x, y


def _encode(point: tuple[int, int]) -> bytes:
    return (point[1] | ((point[0] & 1) << 255)).to_bytes(32, "little")


def _signing_seed(fd: int) -> bytes:
    info = os.fstat(fd)
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    if not stat.S_ISREG(info.st_mode) or (flags & os.O_ACCMODE) != os.O_RDONLY or info.st_size != 32:
        raise ValueError("transport signing key FD must be a read-only 32-byte regular file")
    seed = os.pread(fd, 32, 0)
    if len(seed) != 32 or os.pread(fd, 1, 32):
        raise ValueError("transport signing key FD bytes are invalid")
    return seed


def _ed25519_sign_from_fd(fd: int, message: bytes) -> tuple[bytes, str]:
    digest = hashlib.sha512(_signing_seed(fd)).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    public_key = _encode(_ed_mul(_base_point(), scalar))
    nonce = int.from_bytes(hashlib.sha512(digest[32:] + message).digest(), "little") % _ED_L
    encoded_r = _encode(_ed_mul(_base_point(), nonce))
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % _ED_L
    signature = encoded_r + ((nonce + challenge * scalar) % _ED_L).to_bytes(32, "little")
    return public_key, signature.hex()


def sign_seal(job: TransportJobV1, manifest: HandoffTransferManifestV1, uploaded_manifest_digest: str, signing_domains: VerifiedSigningDomainsV1) -> TransportSealV1:
    receipt = json.loads(_stage_read(job.stage_root, "upload-receipt.json"))
    if receipt != {"manifestDigest": manifest.digest, "schema": "trusted-handoff-upload-receipt/v1"} or uploaded_manifest_digest != manifest.digest:
        raise ValueError("seal requires the exact uploaded manifest receipt")
    registration = next((item for item in signing_domains.transport_keys if item.key_id == job.key_id), None)
    if registration is None or registration.purpose != job.signing_key_purpose or registration.signature_domain != job.signature_domain:
        raise ValueError("transport signing key is not registered for this purpose")
    public_key, _signature = _ed25519_sign_from_fd(job.signing_key_fd, b"probe")
    if public_key != registration.resolved_public_key:
        raise ValueError("transport signing FD does not match the registered key")
    unsigned = TransportSealV1(
        "trusted-handoff-transport-seal/v1", manifest.store_namespace, manifest.handoff_id, manifest.transport_id,
        manifest.consumer_task, manifest.record_ref, manifest.record_full_bytes_digest, manifest.pointer_name,
        manifest.pointer_bytes_digest, manifest.digest, manifest.source_executor_id, manifest.destination_executor_id,
        manifest.nonce, manifest.sequence, manifest.expires_at, manifest.policy_activation_proof_ref,
        manifest.policy_activation_proof_full_bytes_digest, job.signing_key_purpose, job.signature_domain,
        "ssh-ed25519", job.key_id, "0" * 128,
    )
    _public, signature = _ed25519_sign_from_fd(job.signing_key_fd, job.signature_domain.encode("utf-8") + b"\0" + unsigned.unsigned_canonical_bytes)
    seal = TransportSealV1(
        unsigned.schema, unsigned.store_namespace, unsigned.handoff_id, unsigned.transport_id, unsigned.consumer_task,
        unsigned.record_ref, unsigned.record_full_bytes_digest, unsigned.pointer_name, unsigned.pointer_bytes_digest,
        unsigned.transfer_manifest_digest, unsigned.source_executor_id, unsigned.destination_executor_id, unsigned.nonce,
        unsigned.sequence, unsigned.expires_at, unsigned.policy_activation_proof_ref,
        unsigned.policy_activation_proof_full_bytes_digest, unsigned.signing_key_purpose, unsigned.signature_domain,
        unsigned.signature_algorithm, unsigned.key_id, signature,
    )
    _stage_publish(job.stage_root, "transport-seal.json", seal.canonical_bytes)
    return seal


def _verify_seal(manifest: HandoffTransferManifestV1, seal: TransportSealV1, domains: VerifiedSigningDomainsV1) -> None:
    if seal.transfer_manifest_digest != manifest.digest:
        raise ValueError("transport seal does not bind manifest bytes")
    for attribute in ("store_namespace", "handoff_id", "transport_id", "consumer_task", "record_ref", "record_full_bytes_digest", "pointer_name", "pointer_bytes_digest", "source_executor_id", "destination_executor_id", "nonce", "sequence", "expires_at", "policy_activation_proof_ref", "policy_activation_proof_full_bytes_digest"):
        if getattr(seal, attribute) != getattr(manifest, attribute):
            raise ValueError("transport seal bindings are mixed")
    registration = next((item for item in domains.transport_keys if item.key_id == seal.key_id), None)
    if registration is None or registration.purpose != seal.signing_key_purpose or registration.signature_domain != seal.signature_domain:
        raise ValueError("transport seal key is not registered")
    message = seal.signature_domain.encode("utf-8") + b"\0" + seal.unsigned_canonical_bytes
    if not _ed25519_verify(registration.resolved_public_key, message, bytes.fromhex(seal.signature)):
        raise ValueError("transport seal signature is invalid")
    if manifest.expires_at is not None and datetime.fromisoformat(manifest.expires_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ValueError("transport has expired")


def install_transport(job: TransportJobV1, target_store: TrustedHandoffStoreV1, manifest: HandoffTransferManifestV1, seal: TransportSealV1, signing_domains: VerifiedSigningDomainsV1) -> TransportReadyMarkerV1:
    if HandoffTransferManifestV1.from_canonical_bytes(_stage_read(job.stage_root, "export-manifest.json")) != manifest or TransportSealV1.from_canonical_bytes(_stage_read(job.stage_root, "transport-seal.json")) != seal:
        raise ValueError("install requires exact staged manifest and seal")
    _verify_seal(manifest, seal, signing_domains)
    _closure(target_store, manifest.export_request_ref, manifest.handoff_id, manifest.consumer_task)
    ready = TransportReadyMarkerV1("trusted-handoff-transport-ready/v1", manifest.store_namespace, manifest.handoff_id, manifest.transport_id, manifest.consumer_task, manifest.source_executor_id, manifest.destination_executor_id, manifest.nonce, manifest.sequence, manifest.digest, _digest(seal.canonical_bytes), manifest.policy_activation_proof_ref, manifest.policy_activation_proof_full_bytes_digest)
    # Ready is deliberately last: any prior no-replace conflict leaves this transport unreadable.
    target_store.publish_transport_replay(manifest)
    target_store.publish_transport_file(manifest.handoff_id, manifest.transport_id, "manifest.json", manifest.canonical_bytes)
    target_store.publish_transport_file(manifest.handoff_id, manifest.transport_id, "seal.json", seal.canonical_bytes)
    target_store.publish_transport_file(manifest.handoff_id, manifest.transport_id, "ready.json", ready.canonical_bytes)
    return ready


def run_transport(*, source_store: TrustedHandoffStoreV1, target_store: TrustedHandoffStoreV1, stage_root: Path, export_request_ref: str, handoff_id: str, transport_id: str, consumer_task: int, store_namespace: str, source_executor_id: str, destination_executor_id: str, nonce: str, sequence: int, expires_at: str | None, signing_key_fd: int, key_id: str, signing_domains: VerifiedSigningDomainsV1) -> TransportReadyMarkerV1:
    job = TransportJobV1(source_store.root, target_store.root, stage_root, export_request_ref, handoff_id, transport_id, consumer_task, store_namespace, source_executor_id, destination_executor_id, nonce, sequence, expires_at, None, None, signing_key_fd, key_id)
    _closure(source_store, export_request_ref, handoff_id, consumer_task)
    manifest = export(job, source_store)
    receipt = upload(job, source_store, target_store, manifest)
    seal = sign_seal(job, manifest, receipt, signing_domains)
    return install_transport(job, target_store, manifest, seal, signing_domains)


def _domains_from_args(args: argparse.Namespace) -> VerifiedSigningDomainsV1:
    return load_disjoint_signing_domains(*(Path(getattr(args, name)).read_bytes() for name in ("transport_registry", "activation_registry", "broker_auth_registry", "broker_auth_revocations", "broker_auth_allowed_signers")))


def _add_domains_arguments(parser: argparse.ArgumentParser) -> None:
    for name in ("transport-registry", "activation-registry", "broker-auth-registry", "broker-auth-revocations", "broker-auth-allowed-signers"):
        parser.add_argument(f"--{name}", required=True)


def _add_job_arguments(parser: argparse.ArgumentParser, *, target: bool, signing: bool) -> None:
    parser.add_argument("--source-store-root", required=True)
    if target:
        parser.add_argument("--target-store-root", required=True)
    parser.add_argument("--stage-root", required=True)
    parser.add_argument("--export-request-ref", required=True)
    parser.add_argument("--handoff-id", required=True)
    parser.add_argument("--transport-id", required=True)
    parser.add_argument("--consumer-task", type=int, required=True)
    parser.add_argument("--store-namespace", required=True)
    parser.add_argument("--source-executor-id", required=True)
    parser.add_argument("--destination-executor-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--expires-at")
    if signing:
        parser.add_argument("--signing-key-fd", type=int, required=True)
        parser.add_argument("--key-id", required=True)


def _job_from_args(args: argparse.Namespace) -> TransportJobV1:
    fd = getattr(args, "signing_key_fd", None)
    key_id = getattr(args, "key_id", None)
    if fd is None or key_id is None:
        raise ValueError("transport signing key FD and key ID are required")
    target = Path(getattr(args, "target_store_root", args.source_store_root))
    return TransportJobV1(Path(args.source_store_root), target, Path(args.stage_root), args.export_request_ref, args.handoff_id, args.transport_id, args.consumer_task, args.store_namespace, args.source_executor_id, args.destination_executor_id, args.nonce, args.sequence, args.expires_at, None, None, fd, key_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    domains = commands.add_parser("verify-signing-domains")
    _add_domains_arguments(domains)
    request = commands.add_parser("verify-export-request")
    request.add_argument("--source-store-root", required=True); request.add_argument("--export-request-ref", required=True); request.add_argument("--handoff-id", required=True); request.add_argument("--consumer-task", type=int, action="append", required=True)
    export_parser = commands.add_parser("export")
    _add_job_arguments(export_parser, target=False, signing=True)
    upload_parser = commands.add_parser("upload")
    _add_job_arguments(upload_parser, target=True, signing=True)
    seal_parser = commands.add_parser("sign-seal")
    _add_job_arguments(seal_parser, target=True, signing=True); _add_domains_arguments(seal_parser)
    install_parser = commands.add_parser("install-transport")
    _add_job_arguments(install_parser, target=True, signing=True); _add_domains_arguments(install_parser)
    run = commands.add_parser("run-transport")
    _add_job_arguments(run, target=True, signing=True); _add_domains_arguments(run)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-signing-domains":
            _domains_from_args(args)
        elif args.command == "verify-export-request":
            source = TrustedHandoffStoreV1(Path(args.source_store_root))
            for consumer_task in args.consumer_task:
                verify_export_request(source, args.export_request_ref, args.handoff_id, consumer_task)
        elif args.command == "export":
            job = _job_from_args(args)
            export(job, TrustedHandoffStoreV1(job.source_store_root))
        elif args.command == "upload":
            job = _job_from_args(args)
            manifest = HandoffTransferManifestV1.from_canonical_bytes(_stage_read(job.stage_root, "export-manifest.json"))
            upload(job, TrustedHandoffStoreV1(job.source_store_root), TrustedHandoffStoreV1(job.target_store_root), manifest)
        elif args.command == "sign-seal":
            job = _job_from_args(args)
            manifest = HandoffTransferManifestV1.from_canonical_bytes(_stage_read(job.stage_root, "export-manifest.json"))
            sign_seal(job, manifest, manifest.digest, _domains_from_args(args))
        elif args.command == "install-transport":
            job = _job_from_args(args)
            manifest = HandoffTransferManifestV1.from_canonical_bytes(_stage_read(job.stage_root, "export-manifest.json"))
            seal = TransportSealV1.from_canonical_bytes(_stage_read(job.stage_root, "transport-seal.json"))
            install_transport(job, TrustedHandoffStoreV1(job.target_store_root), manifest, seal, _domains_from_args(args))
        elif args.command == "run-transport":
            job = _job_from_args(args)
            run_transport(source_store=TrustedHandoffStoreV1(job.source_store_root), target_store=TrustedHandoffStoreV1(job.target_store_root), stage_root=job.stage_root, export_request_ref=job.export_request_ref, handoff_id=job.handoff_id, transport_id=job.transport_id, consumer_task=job.consumer_task, store_namespace=job.store_namespace, source_executor_id=job.source_executor_id, destination_executor_id=job.destination_executor_id, nonce=job.nonce, sequence=job.sequence, expires_at=job.expires_at, signing_key_fd=job.signing_key_fd, key_id=job.key_id, signing_domains=_domains_from_args(args))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
