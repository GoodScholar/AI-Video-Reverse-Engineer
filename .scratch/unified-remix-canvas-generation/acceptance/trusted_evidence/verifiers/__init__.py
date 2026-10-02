"""Concrete trusted-evidence verifiers."""

from .e1_git import GitLicenseVerifier, GitObjectReader

__all__ = ["GitLicenseVerifier", "GitObjectReader"]
