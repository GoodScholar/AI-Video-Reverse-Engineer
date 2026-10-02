"""Append-only registration of already-stored trusted evidence pointers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import EvidencePointer
from .registry import PolicyError, load_profiles, load_trusted_registry
from .storage import CatalogRepository, CatalogSnapshot, ContentAddressedStore, validate_pointer


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    pointer: EvidencePointer
    snapshot: CatalogSnapshot
    idempotent: bool


class EvidenceRegistrar:
    """Validate a static policy pair and append a pointer without promotion."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.store = ContentAddressedStore(self.root)
        self.catalog = CatalogRepository(self.root)

    def register(self, pointer: EvidencePointer) -> RegistrationResult:
        pointer = validate_pointer(pointer)
        registry = load_trusted_registry(self.root)
        profiles = load_profiles(self.root)
        profile = profiles.get(pointer.profile_id)
        if profile is None:
            raise PolicyError("PROFILE_NOT_FOUND", "pointer references an unknown profile")
        if pointer.subject_ref != profile.subject_ref:
            raise PolicyError("PROFILE_SCOPE_MISMATCH", "pointer subject is outside its profile")
        if pointer.verifier_id not in registry.verifiers:
            raise PolicyError("VERIFIER_NOT_REGISTERED", "pointer references an unknown verifier")
        permitted_verifiers = {
            verifier_id
            for verifier_ids in profile.required_verifier_ids.values()
            for verifier_id in verifier_ids
        }
        if pointer.verifier_id not in permitted_verifiers:
            raise PolicyError("PROFILE_SCOPE_MISMATCH", "profile does not permit this verifier")
        self.store.read(pointer.artifact_ref)
        self.catalog.migrate_empty_v1_for_write()
        appended = self.catalog.append(pointer)
        return RegistrationResult(pointer, appended.snapshot, appended.idempotent)
