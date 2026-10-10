from __future__ import annotations

import difflib
import random
from pathlib import Path

import pytest

from hermes_config_manager.core.paths import MISSING, deep_copy, delete_in, get_in, iter_leaves, set_in
from hermes_config_manager.core.yamlpatch import (
    PatchError,
    YamlParseError,
    diff_paths,
    fast_load,
    render_patched,
    strict_equal,
)

from .conftest import sample_files

DOC = """\
# Hermes config
model:
  default: glm-5.3   # primary
  provider: zai
  reasoning_effort: high
fallback_providers:
- model: google/gemini-3.8-flash
  provider: vertex
- model: grok-4.6
  provider: xai-oauth
delegation:
  provider: 'zai'
  model: "glm-5.3-flash"
  fallback_providers:
    - provider: zai
      model: glm-5.3-flash
      reasoning_effort: medium
  max_iterations: 50
  orchestrator_enabled: false
toolsets: [hermes-cli]
empty_value:
long_text: >
  folded text
  continues here
last: 1
"""


def patch(text, mutate):
    data = fast_load(text)
    target = deep_copy(data)
    mutate(target)
    res = render_patched(text, target)
    assert strict_equal(fast_load(res.text), target)
    return res


def changed_lines(a: str, b: str) -> list[str]:
    return [ln for ln in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


def test_unchanged_returns_same_text():
    res = render_patched(DOC, fast_load(DOC))
    assert res.strategy == "unchanged" and res.text == DOC


def test_scalar_edit_keeps_comment_and_everything_else():
    res = patch(DOC, lambda d: d["model"].__setitem__("default", "glm-5.4"))
    assert "  default: glm-5.4   # primary\n" in res.text
    assert len(changed_lines(DOC, res.text)) == 2  # one line removed, one added


@pytest.mark.parametrize("path,value,expect", [
    (("delegation", "provider"), "openai", "provider: 'openai'"),
    (("delegation", "model"), "gpt-5.6", 'model: "gpt-5.6"'),
    (("model", "provider"), "on", "provider: 'on'"),          # YAML 1.1 bool word must be quoted
    (("model", "provider"), "0755", "provider: '0755'"),
    (("model", "provider"), "", "provider: ''"),
    (("model", "provider"), "a: b", "provider: 'a: b'"),
    (("delegation", "max_iterations"), 75, "max_iterations: 75"),
    (("delegation", "orchestrator_enabled"), True, "orchestrator_enabled: true"),
    (("delegation", "max_iterations"), None, "max_iterations: null"),
    (("delegation", "max_iterations"), 1.5, "max_iterations: 1.5"),
])
def test_scalar_styles_and_ambiguous_strings(path, value, expect):
    res = patch(DOC, lambda d: set_in(d, path, value))
    assert expect in res.text


def test_type_strictness():
    assert not strict_equal(1, True) and not strict_equal(1, 1.0) and not strict_equal("1", 1)
    assert strict_equal({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]})
    assert not strict_equal({"a": 1}, {"a": 1, "b": 2})
    assert strict_equal(MISSING, MISSING) and not strict_equal(MISSING, None)


def test_diff_paths():
    a = {"x": 1, "l": [1, 2], "m": {"k": 1}}
    b = {"x": True, "l": [1, 2, 3], "m": {"k": 1, "n": 2}, "new": 0}
    assert sorted(diff_paths(a, b), key=str) == sorted([("x",), ("l",), ("m", "n"), ("new",)], key=str)


def test_add_and_remove_keys():
    def mutate(d):
        d["delegation"]["child_timeout_seconds"] = 3000
        del d["delegation"]["orchestrator_enabled"]
        d["brand_new"] = {"a": 1, "b": [1, 2]}
    res = patch(DOC, mutate)
    assert "  child_timeout_seconds: 3000\n" in res.text
    assert "orchestrator_enabled" not in res.text
    assert res.text.rstrip().endswith("- 2") or "brand_new:" in res.text


def test_remove_last_key_makes_empty_mapping():
    text = "a:\n  only: 1\nb: 2\n"
    res = patch(text, lambda d: d["a"].pop("only"))
    assert fast_load(res.text) == {"a": {}, "b": 2}


def test_fallback_list_reorder_extend_shrink_and_edit_in_place():
    res = patch(DOC, lambda d: d["fallback_providers"].reverse())
    assert res.text.index("grok-4.6") < res.text.index("google/gemini-3.8-flash")
    res = patch(DOC, lambda d: d["fallback_providers"].append({"provider": "zai", "model": "glm-5.3"}))
    assert ("fallback_providers",) in [p for p, _ in res.rewritten_blocks]
    res = patch(DOC, lambda d: d["delegation"]["fallback_providers"].clear())
    assert "fallback_providers: []" in res.text
    res = patch(DOC, lambda d: d["fallback_providers"][1].__setitem__("model", "grok-4.7"))
    assert len(changed_lines(DOC, res.text)) == 2 and res.rewritten_blocks == []


def test_indented_sequence_style_is_kept():
    res = patch(DOC, lambda d: d["delegation"]["fallback_providers"].append({"provider": "vertex", "model": "g"}))
    assert "    - provider: vertex" in res.text  # dash offset 2 under delegation retained


def test_remove_first_key_of_list_item():
    res = patch(DOC, lambda d: d["fallback_providers"][0].pop("model"))
    assert fast_load(res.text)["fallback_providers"][0] == {"provider": "vertex"}


def test_flow_and_null_and_folded_values():
    res = patch(DOC, lambda d: d["toolsets"].append("web"))
    assert fast_load(res.text)["toolsets"] == ["hermes-cli", "web"]
    res = patch(DOC, lambda d: d.__setitem__("empty_value", {"x": 1}))
    assert fast_load(res.text)["empty_value"] == {"x": 1}
    res = patch(DOC, lambda d: d.__setitem__("long_text", "short"))
    assert "long_text: short" in res.text and "last: 1" in res.text


def test_crlf_bom_and_missing_final_newline_are_preserved():
    crlf = DOC.replace("\n", "\r\n")
    res = patch(crlf, lambda d: d["delegation"].__setitem__("new_key", "v"))
    assert "\n" not in res.text.replace("\r\n", "")
    bom = "\ufeff" + DOC
    res = patch(bom, lambda d: d["model"].__setitem__("provider", "x"))
    assert res.text.startswith("\ufeff")
    no_eol = DOC.rstrip("\n")
    res = patch(no_eol, lambda d: d.__setitem__("appended", 5))
    assert fast_load(res.text)["appended"] == 5


def test_four_space_indentation_file():
    text = "model:\n    default: a\n    provider: b\nx:\n    y: 1\n"
    res = patch(text, lambda d: d["x"].__setitem__("z", {"deep": True}))
    assert "    z:\n        deep: true" in res.text


def test_parse_errors_have_line_numbers():
    with pytest.raises(YamlParseError) as info:
        fast_load("a: 1\n b: 2\n  - c\n")
    assert info.value.line is not None


def test_non_mapping_root_requires_full_rewrite():
    with pytest.raises(PatchError):
        render_patched("- a\n- b\n", {"a": 1})
    res = render_patched("- a\n- b\n", {"a": 1}, allow_full_rewrite=True)
    assert res.strategy == "full-rewrite" and fast_load(res.text) == {"a": 1}


def test_empty_document_gets_content():
    res = render_patched("# only a comment\n", {"a": 1})
    assert res.text.startswith("# only a comment\n") and fast_load(res.text) == {"a": 1}


# ---------------------------------------------------------------- corpus
PARSEABLE = []
# the test home and broken files, plus every 8th generated benchmark config (comments, 2/4-space indents)
_BENCH = sorted((Path(__file__).resolve().parents[1] / "bench" / "fixtures" / "generated-100").glob("**/config.yaml"))
CORPUS = sample_files() + _BENCH[::8]
for _f in CORPUS:
    _t = _f.read_bytes().decode("utf-8")
    try:
        fast_load(_t)
        PARSEABLE.append(_f)
    except YamlParseError:
        pass


def test_corpus_has_malformed_files_with_line_numbers():
    bad = [f for f in sample_files() if f not in PARSEABLE]
    assert bad, "the sample corpus includes malformed backups"
    for f in bad:
        with pytest.raises(YamlParseError) as info:
            fast_load(f.read_text(encoding="utf-8"))
        assert info.value.line


@pytest.mark.parametrize("path", PARSEABLE[:40], ids=lambda p: p.parent.name)
def test_corpus_random_edits_are_minimal_and_verified(path):
    text = path.read_bytes().decode("utf-8")
    data = fast_load(text)
    rng = random.Random(path.parent.name)
    leaves = [p for p, v in iter_leaves(data) if p and not isinstance(v, (dict, list))]
    for trial in range(12):
        p = rng.choice(leaves)
        target = deep_copy(data)
        value = get_in(target, p)
        if isinstance(value, bool):
            new = not value
        elif isinstance(value, int):
            new = value + 7
        elif isinstance(value, str) and "\n" not in value and value and any(value in line for line in text.splitlines()):
            # Only values written on one line: a folded multi-line plain scalar is
            # legitimately re-rendered on one line (covered by the corpus property run).
            new = value + "-x"
        else:
            continue
        set_in(target, p, new)
        res = render_patched(text, target)
        assert strict_equal(fast_load(res.text), target)
        assert len(changed_lines(text, res.text)) == 2, (p, changed_lines(text, res.text))
    # A structural edit: delete one key, add one key.
    target = deep_copy(data)
    victim = rng.choice([p for p in leaves if len(p) > 1 and isinstance(p[-1], str)])
    delete_in(target, victim)
    target.setdefault("delegation", {})["added_by_test"] = "yes"
    res = render_patched(text, target)
    assert strict_equal(fast_load(res.text), target)
