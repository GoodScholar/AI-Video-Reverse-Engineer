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


def _mount_identity_from_kernel(descriptor: int) -> tuple[str, int, int]:
    """Return the strongest mount identity the running kernel exposes."""
    info = os.fstat(descriptor)
    try:
        for line in Path(f"/proc/self/fdinfo/{descriptor}").read_text(encoding="utf-8").splitlines():
            if line.startswith("mnt_id:"):
                return ("linux-mount-id", int(line.split(":", 1)[1]), info.st_dev)
    except (OSError, ValueError):
        pass
    try:
        return ("filesystem-id", os.fstatvfs(descriptor).f_fsid, info.st_dev)
    except (AttributeError, OSError):
        return ("device-id", info.st_dev, info.st_dev)


def _entry_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_nlink)


def _same_entry(left: os.stat_result, right: os.stat_result, *, allow_owner_write: bool = False, allow_directory_link_count: bool = False) -> bool:
    left_identity = _entry_identity(left)
    right_identity = _entry_identity(right)
    if allow_directory_link_count and stat.S_ISDIR(left.st_mode) and stat.S_ISDIR(right.st_mode):
        left_identity = left_identity[:-1]
        right_identity = right_identity[:-1]
    if left_identity == right_identity:
        return True
    if not allow_owner_write:
        return False
    return left_identity[:4] + left_identity[5:] == right_identity[:4] + right_identity[5:] and stat.S_IMODE(right.st_mode) == (stat.S_IMODE(left.st_mode) | stat.S_IWUSR)


def _reject_symlink_ancestors(path: Path) -> None:
    if any(part == ".." for part in path.parts):
        raise ValueError("cleanup root must not contain parent traversal")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("cleanup root must not contain a symlink ancestor")


def _owned_root(root: str, expected_mode: int, expected_owner: int) -> tuple[Path, os.stat_result | None]:
    path = Path(root)
    if not path.is_absolute():
        raise ValueError("cleanup root must be absolute and non-symlink")
    _reject_symlink_ancestors(path)
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return path, None
    if not path.is_dir() or info.st_uid != expected_owner or stat.S_IMODE(info.st_mode) != expected_mode:
        raise ValueError("cleanup root is not an owned directory with the expected mode")
    resolved = path.resolve(strict=True)
    protected = (Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve())
    if any(resolved == item or resolved in item.parents for item in protected):
        raise ValueError("cleanup root is too broad")
    return path, info


def _open_directory_path(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_owned(info: os.stat_result, expected_owner: int, *, expected_mode: int | None = None) -> None:
    if info.st_uid != expected_owner or (expected_mode is not None and stat.S_IMODE(info.st_mode) != expected_mode):
        raise ValueError("cleanup tree identity is not owned with the expected mode")


def _open_verified_entry(directory_fd: int, name: str, before: os.stat_result, mount_identity) -> tuple[int, tuple[str, int, int]]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        after = os.fstat(descriptor)
        if not _same_entry(before, after):
            raise ValueError("cleanup tree entry identity changed")
        return descriptor, mount_identity(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _recheck_entry(directory_fd: int, name: str, before: os.stat_result, expected_mount: tuple[str, int, int], mount_identity, *, allow_owner_write: bool = False, allow_directory_link_count: bool = False) -> None:
    current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not _same_entry(before, current, allow_owner_write=allow_owner_write, allow_directory_link_count=allow_directory_link_count):
        raise ValueError("cleanup tree entry identity changed")
    descriptor, actual_mount = _open_verified_entry(directory_fd, name, current, mount_identity)
    try:
        if actual_mount != expected_mount:
            raise ValueError("cleanup tree entry mount identity changed")
    finally:
        os.close(descriptor)


def _assert_directory_removed(descriptor: int, before: os.stat_result, message: str) -> None:
    after = os.fstat(descriptor)
    if sys.platform == "darwin":
        if after.st_ctime_ns != before.st_ctime_ns:
            raise ValueError(message)
    elif after.st_nlink != 0:
        raise ValueError(message)


def _reject_overlapping_cleanup_roots(roots: list[str], expected_mode: int, expected_owner: int) -> None:
    approved = [_owned_root(root, expected_mode, expected_owner)[0] for root in roots]
    for index, root in enumerate(approved):
        for other in approved[index + 1:]:
            if root == other or root in other.parents or other in root.parents:
                raise ValueError("cleanup roots must not overlap")


def _cleanup_owned_tree(root: str, expected_mode: int, *, mount_identity=_mount_identity_from_kernel, owner_id=os.getuid) -> None:
    expected_owner = owner_id()
    path, approved_root = _owned_root(root, expected_mode, expected_owner)
    if approved_root is None:
        return
    parent_fd = _open_directory_path(path.parent)
    try:
        root_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_entry(approved_root, root_stat):
            raise ValueError("cleanup root identity changed")
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError("cleanup root is not an owned directory with the expected mode")
        _assert_owned(root_stat, expected_owner, expected_mode=expected_mode)
        root_fd, root_mount = _open_verified_entry(parent_fd, path.name, root_stat, mount_identity)
        try:
            _remove_tree_at(root_fd, mount_identity, expected_owner, root_mount)
            if mount_identity(root_fd) != root_mount:
                raise ValueError("cleanup root mount identity changed")
            _recheck_entry(parent_fd, path.name, root_stat, root_mount, mount_identity, allow_directory_link_count=True)
            root_before_delete = os.fstat(root_fd)
            os.rmdir(path.name, dir_fd=parent_fd)
            _assert_directory_removed(root_fd, root_before_delete, "cleanup root identity changed")
        finally:
            os.close(root_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    if path.exists():
        raise ValueError("cleanup left residual entries")


def _remove_tree_at(directory_fd: int, mount_identity, expected_owner: int, expected_mount) -> None:
    if mount_identity(directory_fd) != expected_mount:
        raise ValueError("cleanup tree entry mount identity changed")
    open_directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for entry in os.scandir(directory_fd):
        before = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise ValueError("cleanup tree contains a symlink")
        if stat.S_ISDIR(before.st_mode):
            _assert_owned(before, expected_owner)
            child_fd, child_mount = _open_verified_entry(directory_fd, entry.name, before, mount_identity)
            try:
                if child_mount != expected_mount:
                    raise ValueError("cleanup tree entry mount identity changed")
                _remove_tree_at(child_fd, mount_identity, expected_owner, expected_mount)
                if mount_identity(child_fd) != child_mount:
                    raise ValueError("cleanup tree entry mount identity changed")
                _recheck_entry(directory_fd, entry.name, before, child_mount, mount_identity, allow_directory_link_count=True)
                child_before_delete = os.fstat(child_fd)
                os.rmdir(entry.name, dir_fd=directory_fd)
                _assert_directory_removed(child_fd, child_before_delete, "cleanup tree entry identity changed")
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(before.st_mode):
            _assert_owned(before, expected_owner)
            if before.st_nlink != 1:
                raise ValueError("cleanup tree contains a hardlink alias")
            entry_fd, entry_mount = _open_verified_entry(directory_fd, entry.name, before, mount_identity)
            os.chmod(entry.name, before.st_mode | stat.S_IWUSR, dir_fd=directory_fd, follow_symlinks=False)
            try:
                if entry_mount != expected_mount:
                    raise ValueError("cleanup tree entry mount identity changed")
                _recheck_entry(directory_fd, entry.name, before, entry_mount, mount_identity, allow_owner_write=True)
                os.unlink(entry.name, dir_fd=directory_fd)
                if os.fstat(entry_fd).st_nlink != 0:
                    raise ValueError("cleanup tree entry identity changed")
            finally:
                os.close(entry_fd)
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
    cleanup.add_argument("--root", action="append", required=True)
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
            failures = []
            _reject_overlapping_cleanup_roots(args.root, args.expected_mode, os.getuid())
            for root in args.root:
                try:
                    _cleanup_owned_tree(root, args.expected_mode)
                except (OSError, ValueError) as error:
                    failures.append(f"{root}: {error}")
            if failures:
                raise ValueError("; ".join(failures))
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
