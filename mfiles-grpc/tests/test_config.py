from mfiles_grpc.config import load_settings

CONFIG = '''
[m-files.tool.common]
rest-api-url = "https://example.cloudvault.m-files.com/REST/"
username = "someone"
password = "hunter2hunter2"
vault = "{11111111-2222-3333-4444-555555555555}"

[m-files.tool.grpc]
session-header = "session-id"
session-encoding = "base64"
'''


def test_load_settings(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG)
    s = load_settings(str(path))
    assert s.host == "example.cloudvault.m-files.com"
    assert s.port == 443
    assert (s.session_header, s.session_encoding) == ("session-id", "base64")


def test_grpc_section_is_optional(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG.split("[m-files.tool.grpc]")[0])
    s = load_settings(str(path))
    assert s.session_header is None
    assert s.session_encoding == "hex"


def test_repr_hides_password(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG)
    assert "hunter2" not in repr(load_settings(str(path)))
