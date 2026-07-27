# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Utilities for code hosting platform URL parsing.

This module provides shared functionality for parsing URLs from code hosting
platforms like GitHub and GitLab.
"""

from typing import Optional
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from openviking_cli.utils.config import get_openviking_config


# Repo-page path segments that mark an http(s) URL as a browse page rather
# than a cloneable repository (github.com/org/repo/issues/123 etc.).
_NON_REPO_PATH_SEGMENTS = frozenset(
    {
        "blob",
        "commit",
        "commits",
        "issues",
        "merge_requests",
        "pull",
        "pulls",
        "raw",
        "releases",
        "wiki",
    }
)

# Top-level namespaces reserved by the hosting platforms themselves
# (GitHub / GitLab / Gitee); they can never be org names, so URLs starting
# with them are never repositories (github.com/topics/python,
# gitlab.com/groups/gitlab-org, ...).
_RESERVED_TOP_LEVEL_SEGMENTS = frozenset(
    {
        "-",
        "admin",
        "apps",
        "codespaces",
        "collections",
        "dashboard",
        "enterprise",
        "explore",
        "features",
        "groups",
        "help",
        "issues",
        "join",
        "login",
        "marketplace",
        "new",
        "notifications",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "settings",
        "sponsors",
        "topics",
        "trending",
        "users",
    }
)


def _looks_like_commit_sha(ref: str) -> bool:
    """Return True if ref looks like a git commit SHA (7-40 hex chars)."""
    return 7 <= len(ref) <= 40 and all(c in "0123456789abcdefABCDEF" for c in ref)


def _domain_matches(parsed: ParseResult, domains: list[str]) -> bool:
    """Return True when parsed URL host matches configured domains.

    ``urlparse().netloc`` includes optional userinfo and port values. Repository
    clone URLs commonly use forms like ``ssh://git@github.com/org/repo.git``,
    where the netloc is ``git@github.com`` but the actual host is
    ``github.com``.
    """
    hostname = parsed.hostname
    if not hostname:
        return False

    normalized_domains = {domain.lower() for domain in domains}
    host = hostname.lower()
    candidates = {host}

    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        candidates.add(f"{host}:{port}")

    return any(candidate in normalized_domains for candidate in candidates)


def _extract_host(url: str) -> str:
    """Extract normalized host for supported git/code-hosting URL forms."""
    if url.startswith("git@"):
        rest = url[4:]
        if ":" not in rest:
            return ""
        return rest.split(":", 1)[0].strip().lower()

    parsed = urlparse(url)
    return (parsed.hostname or parsed.netloc or "").strip().lower()


def _get_all_domains() -> list[str]:
    config = get_openviking_config()
    return list(
        set(
            config.code.github_domains
            + config.code.gitlab_domains
            + getattr(config.code, "azure_devops_domains", [])
            + config.code.code_hosting_domains
        )
    )


def _get_azure_devops_domains() -> set[str]:
    config = get_openviking_config()
    return set(getattr(config.code, "azure_devops_domains", []))


def _sanitize_segment(segment: str) -> str:
    decoded_segment = unquote(segment)
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in decoded_segment)


def _extract_azure_devops_repo_parts(path_parts: list[str]) -> Optional[list[str]]:
    """Return Azure DevOps repository path parts ending in repo name."""
    try:
        git_index = path_parts.index("_git")
    except ValueError:
        return None

    if git_index < 2 or git_index + 1 >= len(path_parts) or len(path_parts) != git_index + 2:
        return None

    repo_parts = path_parts[:git_index] + [path_parts[git_index + 1]]
    if not all(repo_parts):
        return None
    return repo_parts


def _extract_azure_devops_ssh_repo_parts(path_parts: list[str]) -> Optional[list[str]]:
    """Return Azure DevOps SSH repository path parts ending in repo name."""
    if len(path_parts) != 4 or path_parts[0] != "v3":
        return None

    repo_parts = path_parts[1:]
    if not all(repo_parts):
        return None
    return repo_parts


def _is_azure_devops_browse_url(query: str) -> bool:
    """Return True for Azure DevOps repo browsing URLs like ?path=/README.md."""
    return "path" in parse_qs(query, keep_blank_values=True)


def parse_code_hosting_url(url: str) -> Optional[str]:
    """Parse code hosting platform URL to get org/repo path.

    Args:
        url: Code hosting URL like https://github.com/volcengine/OpenViking
             or git@github.com:volcengine/OpenViking.git

    Returns:
        org/repo path like "volcengine/OpenViking" or None if not a valid
        code hosting URL
    """
    all_domains = _get_all_domains()
    # Handle git@ SSH URLs: git@host:org/repo.git
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return None
        host_part, path_part = url[4:].split(":", 1)
        if host_part not in all_domains:
            return None
        path_parts = [p for p in path_part.split("/") if p]
        if host_part in _get_azure_devops_domains():
            azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
            if azure_repo_parts:
                return "/".join(
                    _sanitize_segment(part.removesuffix(".git")) for part in azure_repo_parts
                )
        if len(path_parts) < 2:
            return None
        # A trailing .git marks a clone path: keep every segment so nested
        # groups (GitLab subgroups, GitCode/Gitee) resolve to the full path.
        if path_parts[-1].endswith(".git"):
            repo_parts = list(path_parts)
            repo_parts[-1] = repo_parts[-1][:-4]
        else:
            repo_parts = path_parts[:2]
        return "/".join(_sanitize_segment(part) for part in repo_parts)

    if not url.startswith(("http://", "https://", "git://", "ssh://")):
        return None

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    if _domain_matches(parsed, list(_get_azure_devops_domains())):
        azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
        if azure_repo_parts is None:
            azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
        if azure_repo_parts:
            return "/".join(
                _sanitize_segment(part.removesuffix(".git")) for part in azure_repo_parts
            )
        return None

    # GitLab "/-/" browse separator: everything before "-" is the full
    # (possibly nested) repository path.
    from_dash_separator = False
    if "-" in path_parts and path_parts.index("-") >= 2:
        path_parts = path_parts[: path_parts.index("-")]
        from_dash_separator = True

    # For code hosting URLs with org/repo structure
    if _domain_matches(parsed, all_domains) and len(path_parts) >= 2:
        has_git_suffix = path_parts[-1].endswith(".git")
        # Segments between repo and filename that mark a browse URL
        # (org/repo/blob/main/file.git must not be taken as a nested path).
        browse_markers = set(path_parts[2:-1]) & (_NON_REPO_PATH_SEGMENTS | {"tree"})
        if from_dash_separator or (has_git_suffix and not browse_markers):
            repo_parts = list(path_parts)
        else:
            # Plain browser URL: take first two parts (org/repo)
            repo_parts = path_parts[:2]
        if repo_parts[-1].endswith(".git"):
            repo_parts[-1] = repo_parts[-1][:-4]
        return "/".join(_sanitize_segment(part) for part in repo_parts)

    return None


def is_github_url(url: str) -> bool:
    """Check if a URL is a GitHub URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitHub URL
    """
    config = get_openviking_config()
    return _extract_host(url) in config.code.github_domains


def is_gitlab_url(url: str) -> bool:
    """Check if a URL is a GitLab URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitLab URL
    """
    config = get_openviking_config()
    return _extract_host(url) in config.code.gitlab_domains


def is_code_hosting_url(url: str) -> bool:
    """Check if a URL is a code hosting platform URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a code hosting platform URL
    """
    all_domains = _get_all_domains()

    # Handle git@ SSH URLs
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return False
        host_part = url[4:].split(":", 1)[0]
        return host_part in all_domains

    return _domain_matches(urlparse(url), all_domains)


def is_code_hosting_blob_url(url: str) -> bool:
    """Check whether a URL points to a single file on a code hosting platform.

    Recognizes GitHub/GitLab ``blob`` URLs and GitHub ``raw`` URLs, e.g.
    ``https://github.com/org/repo/blob/main/README.md`` or
    ``https://raw.githubusercontent.com/org/repo/main/README.md``. These
    historically go through HTTPAccessor (with optional blob->raw rewriting),
    not the recursive web crawler.
    """
    if not url.startswith(("http://", "https://")):
        return False

    config = get_openviking_config()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False

    raw_hosts = {"raw.githubusercontent.com"}
    if host in raw_hosts:
        return True

    code_hosts = set(config.code.github_domains) | set(config.code.gitlab_domains)
    if host not in code_hosts:
        return False

    path_parts = [p for p in parsed.path.split("/") if p]
    return "blob" in path_parts or "raw" in path_parts


def validate_git_ssh_uri(url: str) -> None:
    """Validate a git@ SSH URI format.

    Args:
        url: URL to validate (e.g. git@github.com:org/repo.git)

    Raises:
        ValueError: If the URL is not a valid git@ SSH URI
    """
    if not url.startswith("git@"):
        raise ValueError(f"Not a git@ SSH URI: {url}")
    rest = url[4:]
    if ":" not in rest or not rest.split(":", 1)[1]:
        raise ValueError(f"Invalid git@ SSH URI (missing colon or empty path): {url}")


def is_git_repo_url(url: str) -> bool:
    """Strict check for cloneable git repository URLs.

    Distinguishes repo URLs (github.com/org/repo) from non-repo URLs
    (github.com/org/repo/issues/123). The domain must always be whitelisted.

    Accepted http(s) shapes:
    - owner/repo, with or without a .git suffix
    - nested clone URLs ending in .git, e.g. GitLab subgroups or
      GitCode/Gitee nested groups (host/group/subgroup/repo.git)
    - browser tree URLs: owner/repo/tree/<ref> (the ref may contain '/')
      and GitLab-style group/.../repo/-/tree/<ref>
    - commit pins: owner/repo/commit/<sha> and group/.../repo/-/commit/<sha>
    - Azure DevOps org/project/_git/repo (browse URLs with ?path= excluded)

    Rejected: platform-reserved top-level pages (topics, orgs, groups, ...),
    repo browse pages (issues, pull, blob, ...), and Azure DevOps URLs
    without the _git form.

    Args:
        url: URL to check

    Returns:
        True if the URL points to a cloneable git repository
    """
    # git@/ssh://git:// protocols: domain must match and a repo path must exist
    if url.startswith(("git@", "ssh://", "git://")):
        if not is_code_hosting_url(url):
            return False
        if url.startswith("git@"):
            rest = url[4:]
            path = rest.split(":", 1)[1] if ":" in rest else ""
        else:
            path = urlparse(url).path
        return any(p for p in path.split("/") if p)

    if not url.startswith(("http://", "https://")):
        return False

    all_domains = _get_all_domains()
    parsed = urlparse(url)
    if not _domain_matches(parsed, all_domains):
        return False
    path_parts = [p for p in parsed.path.split("/") if p]

    # Strip the .git suffix from the last part but remember it: a trailing
    # .git is a strong clone-URL signal used below.
    has_git_suffix = bool(path_parts) and path_parts[-1].endswith(".git")
    if has_git_suffix:
        path_parts[-1] = path_parts[-1][:-4]
        if not path_parts[-1]:
            return False

    # Azure DevOps: only the org/project/_git/repo form is cloneable;
    # anything else on an Azure domain (e.g. the project page) is not a repo.
    if _domain_matches(parsed, list(_get_azure_devops_domains())):
        azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
        if not azure_repo_parts:
            return False
        return not _is_azure_devops_browse_url(parsed.query)

    # Platform-reserved top-level namespaces are never repositories.
    if path_parts and path_parts[0].lower() in _RESERVED_TOP_LEVEL_SEGMENTS:
        return False

    # GitLab "/-/" browse separator: group/.../repo/-/<page>/...
    # Everything before "-" is the (possibly nested) repo path; only the
    # tree and commit pages still identify a cloneable snapshot.
    dash_index = path_parts.index("-") if "-" in path_parts else -1
    if dash_index >= 2:
        browse = path_parts[dash_index + 1 :]
        if len(browse) >= 2 and browse[0] == "tree":
            return True
        if len(browse) == 2 and browse[0] == "commit" and _looks_like_commit_sha(browse[1]):
            return True
        return False

    # owner/repo/commit/<sha> pins the repo to an exact snapshot.
    if len(path_parts) == 4 and path_parts[2] == "commit" and _looks_like_commit_sha(path_parts[3]):
        return True

    # Repo browse pages -- unless the matching segment is itself the final
    # .git-suffixed repo name (a nested repo literally named e.g. "blob").
    if (
        len(path_parts) >= 3
        and path_parts[2] in _NON_REPO_PATH_SEGMENTS
        and not (has_git_suffix and len(path_parts) == 3)
    ):
        return False

    # Clone-style URL: the trailing .git accepts nested group paths
    # (GitLab subgroups, GitCode/Gitee nested groups).
    if has_git_suffix:
        return True

    # owner/repo
    if len(path_parts) == 2:
        return True
    # owner/repo/tree/<ref> (the ref may contain '/', e.g. feature branches)
    if len(path_parts) >= 4 and path_parts[2] == "tree":
        return True
    return False
