"""Deliberate handling of credentials in configuration data.

The application never needs to show a secret to do its job, so secret values
are masked in the UI, diffs, logs, reports, screenshots and preset exports.
Names that merely *reference* a secret (``key_env: OPENAI_API_KEY``) are not
secrets and stay visible.
"""

from __future__ import annotations

import re
from typing import Any

from .paths import MISSING, PathT

MASK = "••••••"

# Key names that hold credentials.  Matched against the last path component.
_SECRET_KEY = re.compile(
    r"(^|[_\-.])(api[_\-]?key|apikey|access[_\-]?key|secret|secret[_\-]?key|client[_\-]?secret|"
    r"password|passwd|pass|token|access[_\-]?token|refresh[_\-]?token|bot[_\-]?token|auth[_\-]?token|"
    r"bearer|cookie|cookies|credential|credentials|private[_\-]?key|session[_\-]?key|signing[_\-]?key|"
    r"webhook[_\-]?secret|app[_\-]?secret|encryption[_\-]?key|pat)($|[_\-.])",
    re.IGNORECASE,
)
# Suffixes that make a key a *reference* or a setting about secrets, not a secret.
_REFERENCE_SUFFIX = re.compile(
    r"(_env|_env_var|_var|_file|_path|_dir|_mode|_url|_uri|_type|_enabled|_required|_timeout|"
    r"_ttl|_header|_name|_limit|_len|_length|_count|_policy|_source|_command|_cmd|_id_env)$",
    re.IGNORECASE,
)
_NOT_SECRET = {
    "key_env", "api_mode", "auth_mode", "token_budget", "max_tokens", "tokens", "token_limit",
    "tokenizer", "redact_secrets", "secrets", "password_min_length", "password_store", "pass_through", "passthrough",
    "env_passthrough", "keys", "key", "tokens_per_minute", "max_output_tokens", "min_tokens",
}

# Value shapes that look like credentials wherever they appear.
_SECRET_VALUE = re.compile(
    r"^(sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{16,}|xai-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}|"
    r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_\-]{16,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|ya29\.[0-9A-Za-z_\-]{20,}|xox[abpors]-[0-9A-Za-z\-]{10,}|"
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}|"
    r"\d{8,12}:[A-Za-z0-9_\-]{30,}|"             # Telegram bot token
    r"[MN][A-Za-z\d]{23,25}\.[\w\-]{6}\.[\w\-]{27,}|"  # Discord bot token
    r"Bearer\s+\S{16,})$"
)
_INLINE_TOKEN = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{16,}|xai-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}|"
    r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|"
    r"ya29\.[0-9A-Za-z_\-]{20,}|xox[abpors]-[0-9A-Za-z\-]{10,}|"
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}|Bearer\s+\S{16,})"
)
# ``key: value`` lines in YAML/log text where the key is secret-like.
_YAML_SECRET_LINE = re.compile(r"^(?P<lead>\s*(?:-\s+)?)(?P<key>[\"']?[\w\-.]+[\"']?)(?P<sep>\s*:\s+)(?P<val>\S.*?)\s*$")
# URLs with embedded credentials: scheme://user:pass@host
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@/\s]+)@")


def is_secret_key(key: Any) -> bool:
    """True if a mapping key names a credential value."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered in _NOT_SECRET or _REFERENCE_SUFFIX.search(lowered):
        return False
    return bool(_SECRET_KEY.search(lowered))


def is_secret_path(path: PathT) -> bool:
    return any(is_secret_key(k) for k in path[-1:])


def looks_like_secret_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 16 or text.startswith("${") or " " in text and not text.startswith("Bearer "):
        return False
    return bool(_SECRET_VALUE.match(text))


def is_secret(path: PathT, value: Any) -> bool:
    """True if the value at ``path`` must be masked.

    Empty values and environment-variable references (``${VAR}``) are not
    secrets: showing that a field is empty or where it comes from is useful.
    """
    if value is None or value == "" or isinstance(value, (bool, int, float, dict, list)):
        return False
    if isinstance(value, str) and value.strip().startswith("${") and value.strip().endswith("}"):
        return False
    return is_secret_path(path) or looks_like_secret_value(value)


def _mask_inline(text: str) -> str:
    """Mask token-shaped substrings and the password of ``scheme://user:pass@host`` inside a string."""
    text = _INLINE_TOKEN.sub(MASK, text)
    return _URL_CREDENTIALS.sub(lambda m: f"{m.group('scheme')}{m.group('user')}:{MASK}@", text)


def mask_value(path: PathT, value: Any) -> Any:
    if is_secret(path, value):
        return MASK
    if isinstance(value, str) and len(value) >= 16:
        return _mask_inline(value)
    return value


def redact(data: Any, prefix: PathT = ()) -> Any:
    """Deep copy of ``data`` with secret values replaced by :data:`MASK`."""
    if isinstance(data, dict):
        return {k: redact(v, prefix + (k,)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, prefix + (i,)) for i, v in enumerate(data)]
    return mask_value(prefix, data)


def contains_mask(value: Any) -> bool:
    """True if any string inside ``value`` still carries :data:`MASK`."""
    if isinstance(value, dict):
        return any(contains_mask(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_mask(v) for v in value)
    return isinstance(value, str) and MASK in value


def _matches_masked(new: Any, original: Any) -> bool:
    """``new`` is exactly what masking ``original`` produces (i.e. left untouched)."""
    if original is MISSING:
        return False
    if isinstance(new, dict) and isinstance(original, dict):
        return new.keys() == original.keys() and all(_matches_masked(new[k], original[k]) for k in new)
    if isinstance(new, list) and isinstance(original, list):
        return len(new) == len(original) and all(_matches_masked(a, b) for a, b in zip(new, original))
    if isinstance(new, str) and MASK in new:
        if new == MASK:                       # a whole masked value stands for a scalar, never a block
            return not isinstance(original, (dict, list))
        return isinstance(original, str) and (mask_text(original) == new or _mask_inline(original) == new)
    return type(new) is type(original) and new == original


def restore_masks(new: Any, original: Any) -> Any:
    """Put back credential values the user left masked.

    A masked string is restored only when it equals the masked form of the
    original value at the same place (whole-value masks, partially masked URLs
    and tokens alike).  List items are matched by content, never just by
    position, so moved or inserted entries cannot swap credentials.  Anything
    that cannot be matched keeps its mask; callers refuse such values
    (:func:`contains_mask`).
    """
    if original is MISSING:
        return new
    if isinstance(new, dict) and isinstance(original, dict):
        return {k: restore_masks(v, original.get(k, MISSING)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(original, list):
        reordered = any(
            not _matches_masked(n, original[i]) and any(_matches_masked(n, o) for j, o in enumerate(original) if j != i)
            for i, n in enumerate(new) if i < len(original)
        )
        if len(new) == len(original) and not reordered:
            return [restore_masks(n, o) for n, o in zip(new, original)]
        used: set[int] = set()
        out = []
        for n in new:
            candidates = [j for j, o in enumerate(original) if j not in used and _matches_masked(n, o)]
            distinct = {repr(original[j]) for j in candidates}
            if not candidates or (len(distinct) > 1 and contains_mask(n)):
                out.append(n)                 # unknown or ambiguous: keep the mask, caller refuses
            else:
                used.add(candidates[0])
                out.append(original[candidates[0]])
        return out
    if isinstance(new, str) and MASK in new and _matches_masked(new, original):
        return original
    return new


def contains_secrets(data: Any, prefix: PathT = ()) -> list[PathT]:
    found: list[PathT] = []
    if isinstance(data, dict):
        for k, v in data.items():
            found += contains_secrets(v, prefix + (k,))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found += contains_secrets(v, prefix + (i,))
    elif is_secret(prefix, data):
        found.append(prefix)
    return found


_BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*[+-]?$")
_FLOW_PAIR = re.compile(r"(?P<key>[\"']?[\w\-.]+[\"']?)(?P<sep>\s*:\s*)(?P<val>\"[^\"]*\"|'[^']*'|[^,{}\[\]\s][^,{}\]]*)")


def _mask_flow(body: str) -> str:
    """``auth: {token: abc, user: bob}``: mask the secret pairs inside a flow mapping."""
    def pair(m: re.Match) -> str:
        key, val = m.group("key").strip("\"'"), m.group("val").strip()
        if is_secret((key,), val.strip("\"'")):
            return f"{m.group('key')}{m.group('sep')}{MASK}"
        return m.group(0)

    head, brace, rest = body.partition("{")
    return head + brace + _FLOW_PAIR.sub(pair, rest)


def mask_text(text: str) -> str:
    """Mask credentials in free text (YAML documents, diffs, logs, errors).

    Covers ``key: value`` lines with a secret-like key, the indented lines of a block value under such
    a key (``private_key: |``), secret pairs in flow mappings (``{token: …}``), token-shaped strings and
    passwords inside URLs.
    """
    if not text:
        return text
    out_lines = []
    block_indent: int | None = None          # inside the block value of a secret key: that key's indent
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        prefix = ""
        # Unified-diff lines start with +/-/space; keep the marker.
        if body[:1] in "+- " and not body.startswith(("+++", "---")):
            prefix, body = body[:1], body[1:]
        if block_indent is not None:
            indent = len(body) - len(body.lstrip(" "))
            if not body.strip() or indent > block_indent:
                if body.strip():
                    body = body[:indent] + MASK
                out_lines.append(prefix + body + ending)
                continue
            block_indent = None
        match = _YAML_SECRET_LINE.match(body)
        if match:
            key = match.group("key").strip("\"'")
            val = match.group("val")
            comment = ""
            if " #" in val:
                val, comment = val.split(" #", 1)
                comment = " #" + comment
            if _BLOCK_SCALAR.match(val.strip()):
                if is_secret_key(key):
                    block_indent = len(match.group("lead"))
            elif val not in ("''", '""', "null", "~") and is_secret((key,), val.strip("\"'")):
                body = f"{match.group('lead')}{match.group('key')}{match.group('sep')}{MASK}{comment}"
        if "{" in body:
            body = _mask_flow(body)
        body = _INLINE_TOKEN.sub(MASK, body)
        body = _URL_CREDENTIALS.sub(lambda m: f"{m.group('scheme')}{m.group('user')}:{MASK}@", body)
        out_lines.append(prefix + body + ending)
    return "".join(out_lines)
