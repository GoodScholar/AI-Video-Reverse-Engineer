#!/usr/bin/env python3
"""Fail-closed local entrypoint for the portable handoff store."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import json

from trusted_evidence.handoffs import HandoffExportRequestV1, HandoffPointerV1, HandoffRegistryV1, TrustedHandoffRecordV1, TrustedHandoffStoreV1, load_disjoint_signing_domains, verify_policy_activation_proof, verify_policy_transport_source_closure, verify_transport_closure


def _store(root: str | None) -> TrustedHandoffStoreV1:
    if root is None:
        return TrustedHandoffStoreV1.open_from_environment()
    if os.environ.get("TRUSTED_HANDOFF_STORE_ROOT") != root:
        raise ValueError("explicit store root must match the leased handoff store root")
    return TrustedHandoffStoreV1.open_from_environment()


def _cache(root: str, *, require_empty: bool, expected_mode: int) -> TrustedHandoffStoreV1:
    store = TrustedHandoffStoreV1(Path(root))
    store.verify_root(expected_mode=expected_mode, require_empty=require_empty)
    return store


def _owned_root(root: str, expected_mode: int) -> Path:
    path = Path(root)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("cleanup root must be absolute and non-symlink")
    if not path.exists():
        return path
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != expected_mode:
        raise ValueError("cleanup root is not an owned directory with the expected mode")
    resolved = path.resolve(strict=True)
    protected = (Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve())
    if any(resolved == item or resolved in item.parents for item in protected):
        raise ValueError("cleanup root is too broad")
    return resolved


def _cleanup_owned_tree(root: str, expected_mode: int) -> None:
    path = _owned_root(root, expected_mode)
    if not path.exists():
        return
    parent = path.parent
    open_directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(parent, open_directory)
    try:
        root_stat = path.stat()
        root_fd = os.open(path.name, open_directory, dir_fd=parent_fd)
        try:
            if (root_stat.st_dev, root_stat.st_ino) != (os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino):
                raise ValueError("cleanup root identity changed")
            _remove_tree_at(root_fd)
        finally:
            os.close(root_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (root_stat.st_dev, root_stat.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("cleanup root identity changed")
        os.rmdir(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    if path.exists():
        raise ValueError("cleanup left residual entries")


def _remove_tree_at(directory_fd: int) -> None:
    open_directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for entry in os.scandir(directory_fd):
        before = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise ValueError("cleanup tree contains a symlink")
        if stat.S_ISDIR(before.st_mode):
            child_fd = os.open(entry.name, open_directory, dir_fd=directory_fd)
            try:
                after = os.fstat(child_fd)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise ValueError("cleanup tree identity changed")
                _remove_tree_at(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(entry.name, dir_fd=directory_fd)
        elif stat.S_ISREG(before.st_mode):
            os.chmod(entry.name, before.st_mode | stat.S_IWUSR, dir_fd=directory_fd, follow_symlinks=False)
            os.unlink(entry.name, dir_fd=directory_fd)
        else:
            raise ValueError("cleanup tree contains an unsupported entry")


def _arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-root")
    parser.add_argument("--store-root")
    parser.add_argument("--handoff-id")
    parser.add_argument("--transport-id")
    parser.add_argument("--consumer-task", type=int)
    parser.add_argument("--expected-destination-executor-id")
    parser.add_argument("--expected-kind")
    parser.add_argument("--expected-producer-task", type=int)


def _domain_arguments(parser: argparse.ArgumentParser) -> None:
    for name in ("transport-registry", "activation-registry", "broker-auth-registry", "broker-auth-revocations", "broker-auth-allowed-signers"):
        parser.add_argument(f"--{name}")


def _domains(args: argparse.Namespace):
    values = (args.transport_registry, args.activation_registry, args.broker_auth_registry, args.broker_auth_revocations, args.broker_auth_allowed_signers)
    if any(value is None for value in values):
        raise ValueError("authenticated transport requires all signing-domain inputs")
    return load_disjoint_signing_domains(*(Path(value).read_bytes() for value in values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    verify_root = commands.add_parser("verify-root")
    verify_root.add_argument("--store-root")
    publish = commands.add_parser("publish-local")
    publish.add_argument("--producer-task", type=int, required=True)
    publish.add_argument("--record", type=Path)
    publish.add_argument("--pointer", type=Path)
    publish.add_argument("--blob", type=Path, action="append", default=[])
    export_request = commands.add_parser("publish-export-request")
    export_request.add_argument("--producer-task", type=int, required=True)
    export_request.add_argument("--request", type=Path, required=True)
    verify_cache = commands.add_parser("verify-cache")
    verify_cache.add_argument("--cache-root", required=True)
    verify_cache.add_argument("--require-empty", action="store_true")
    verify_cache.add_argument("--expected-mode", type=lambda value: int(value, 8), required=True)
    fetch = commands.add_parser("fetch")
    _arguments(fetch)
    fetch.add_argument("--export-request-ref")
    _domain_arguments(fetch)
    import_command = commands.add_parser("import")
    _arguments(import_command)
    _domain_arguments(import_command)
    finalize = commands.add_parser("finalize")
    _arguments(finalize)
    _domain_arguments(finalize)
    read = commands.add_parser("read")
    _arguments(read)
    _domain_arguments(read)
    read.add_argument("--field", default="producerCommit.hex")
    read.add_argument("legacy_handoff_id", nargs="?")
    subject = commands.add_parser("read-product-subject")
    _arguments(subject)
    _domain_arguments(subject)
    subject.add_argument("--field", choices=("hex", "canonicalSubject"), default="hex")
    subject.add_argument("legacy_handoff_id", nargs="?")
    cleanup = commands.add_parser("cleanup-owned-tree")
    cleanup.add_argument("--root", required=True)
    cleanup.add_argument("--expected-mode", type=lambda value: int(value, 8), required=True)
    activation = commands.add_parser("verify-policy-activation")
    _arguments(activation)
    _domain_arguments(activation)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-root":
            _store(args.store_root)
        elif args.command == "verify-cache":
            _cache(args.cache_root, require_empty=args.require_empty, expected_mode=args.expected_mode)
        elif args.command == "cleanup-owned-tree":
            _cleanup_owned_tree(args.root, args.expected_mode)
        elif args.command == "publish-local":
            if args.producer_task in {22, 27}:
                raise ValueError("reserved producer")
            if args.record is None or args.pointer is None:
                raise ValueError("record and pointer are required")
            registry = HandoffRegistryV1(_store(None))
            record = TrustedHandoffRecordV1.from_canonical_bytes(args.record.read_bytes())
            pointer = HandoffPointerV1.from_canonical_bytes(args.pointer.read_bytes())
            if record.producer_task != args.producer_task:
                raise ValueError("producer task does not match record")
            registry.publish_local(record, pointer, tuple(item.read_bytes() for item in args.blob))
        elif args.command == "publish-export-request":
            if args.producer_task in {22, 27}:
                raise ValueError("reserved producer")
            request = HandoffExportRequestV1.from_canonical_bytes(args.request.read_bytes())
            print(HandoffRegistryV1(_store(None)).publish_export_request(request))
        elif args.command == "fetch":
            if not args.cache_root or not args.store_root or not args.handoff_id or not args.consumer_task:
                raise ValueError("fetch identity is required")
            source = _store(args.store_root)
            request_ref = args.export_request_ref or source.read_export_request_ref(args.handoff_id)
            HandoffRegistryV1(_cache(args.cache_root, require_empty=False, expected_mode=0o700)).fetch(source, request_ref, args.handoff_id, consumer_task=args.consumer_task, transport_id=args.transport_id, expected_destination_executor_id=args.expected_destination_executor_id, signing_domains=_domains(args))
        elif args.command == "import":
            if not args.cache_root or not args.handoff_id or not args.consumer_task:
                raise ValueError("import identity is required")
            HandoffRegistryV1(_cache(args.cache_root, require_empty=False, expected_mode=0o700)).import_(args.handoff_id, consumer_task=args.consumer_task, expected_destination_executor_id=args.expected_destination_executor_id, signing_domains=_domains(args))
        elif args.command in {"finalize", "read", "read-product-subject"}:
            handoff_id = args.handoff_id or getattr(args, "legacy_handoff_id", None)
            if not args.cache_root or not handoff_id or not args.consumer_task:
                raise ValueError("not finalized: lifecycle identity is required")
            registry = HandoffRegistryV1(_cache(args.cache_root, require_empty=False, expected_mode=0o700))
            if args.command == "finalize":
                registry.finalize(handoff_id, expected_kind=args.expected_kind, expected_producer_task=args.expected_producer_task, consumer_task=args.consumer_task, expected_destination_executor_id=args.expected_destination_executor_id, signing_domains=_domains(args))
            elif args.command == "read":
                print(registry.read(handoff_id, consumer_task=args.consumer_task, field=args.field, expected_destination_executor_id=args.expected_destination_executor_id, signing_domains=_domains(args)))
            else:
                product = registry.read_product_subject(handoff_id, consumer_task=args.consumer_task, expected_destination_executor_id=args.expected_destination_executor_id, signing_domains=_domains(args))
                print(product.hex if args.field == "hex" else f"git://product@{product.hex}")
        elif args.command == "verify-policy-activation":
            if not args.cache_root or args.handoff_id != "ticket-23-policy-v1" or not args.consumer_task or not args.expected_destination_executor_id:
                raise ValueError("policy activation lifecycle identity is required")
            store = _cache(args.cache_root, require_empty=False, expected_mode=0o700)
            domains = _domains(args)
            registry = HandoffRegistryV1(store)
            fetched = registry._read_fetch_manifest(args.handoff_id, args.consumer_task)
            transport_id = fetched["transportId"]
            if not isinstance(transport_id, str) or fetched["destinationExecutorId"] != args.expected_destination_executor_id:
                raise ValueError("policy activation fetch does not bind the requested destination")
            transport, _seal, _ready = verify_transport_closure(
                store, args.handoff_id, transport_id, args.consumer_task,
                args.expected_destination_executor_id, domains,
            )
            request_ref = store.read_policy_export_request_ref(args.handoff_id)
            if transport.export_request_ref != request_ref or fetched["requestRef"] != request_ref:
                raise ValueError("policy activation fetch does not bind the policy source closure")
            activated, _handoff, proof, _record, _pointer, _core, _refs = verify_policy_transport_source_closure(
                store, request_ref, args.handoff_id, args.consumer_task,
                args.expected_destination_executor_id, domains,
            )
            verified = verify_policy_activation_proof(proof.canonical_bytes, domains, {
                "consumerTask": args.consumer_task,
                "destinationExecutorId": args.expected_destination_executor_id,
                "policyActivationProofRef": activated.policy_activation_proof_ref,
                "policyActivationProofFullBytesDigest": activated.policy_activation_proof_full_bytes_digest,
            })
            print(json.dumps({
                "consumerTask": args.consumer_task,
                "destinationExecutorId": args.expected_destination_executor_id,
                "handoffId": args.handoff_id,
                "operationId": verified.operation_id,
                "policyCommit": verified.policy_commit.to_wire(),
                "proofFullBytesDigest": verified.proof_full_bytes_digest,
                "proofRef": verified.proof_ref,
                "schema": "trusted-policy-activation-verification/v1",
            }, sort_keys=True, separators=(",", ":")))
        else:
            raise ValueError("unsupported command")
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
