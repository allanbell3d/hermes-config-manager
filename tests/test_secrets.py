"""Credential masking in free text and values."""

from hermes_config_manager.core.secrets import mask_text, mask_value, restore_masks

TEXT = """a:
  private_key: |
    LINE1
    LINE2

  api_key: >-
    folded-secret
  other: 1
auth: {token: abcdefghijklmnop12345, user: bob}
proxy: https://bob:pw123@proxy.local:8080
- private_key: |
-   OLDKEY
+ private_key: |
+   NEWKEY
 keep: me
"""


def test_block_values_flow_maps_and_url_passwords_are_masked_in_text():
    out = mask_text(TEXT)
    for bad in ("LINE1", "LINE2", "folded-secret", "abcdefghijklmnop12345", "pw123", "OLDKEY", "NEWKEY"):
        assert bad not in out, bad
    assert "other: 1" in out and "user: bob" in out and "keep: me" in out


def test_a_url_password_is_masked_in_values_and_restored_when_left_masked():
    url = "https://bob:pw123@proxy.local:8080"
    shown = mask_value(("proxy",), url)
    assert "pw123" not in shown and shown.startswith("https://bob:")
    assert restore_masks(shown, url) == url
