import pytest

from mfiles_grpc.config import load_settings

CONFIG = '''
[m-files.tool.common]
rest-api-url = "https://example.cloudvault.m-files.com/REST/"
username = "someone"
password = "hunter2hunter2"
vault = "{11111111-2222-3333-4444-555555555555}"

[m-files.tool.grpc]
port = 8443
'''


def test_load_settings(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG)
    s = load_settings(str(path))
    assert s.host == "example.cloudvault.m-files.com"
    assert s.port == 8443


def test_grpc_section_is_optional(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG.split("[m-files.tool.grpc]")[0])
    s = load_settings(str(path))
    assert s.port == 443


def test_repr_hides_password(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG)
    assert "hunter2" not in repr(load_settings(str(path)))


def _settings(tmp_path, text):
    path = tmp_path / "client-config.toml"
    path.write_text(text)
    return load_settings(str(path))


COMMON_ONLY = CONFIG.split("[m-files.tool.grpc]")[0]


def test_port_comes_from_rest_api_url(tmp_path):
    s = _settings(tmp_path, COMMON_ONLY.replace("example.cloudvault.m-files.com/", "localhost:4443/"))
    assert (s.host, s.port) == ("localhost", 4443)
    assert s.target == "localhost:4443"


def test_grpc_port_overrides_the_url_port(tmp_path):
    s = _settings(tmp_path, CONFIG.replace("example.cloudvault.m-files.com/", "example.cloudvault.m-files.com:4443/"))
    assert s.port == 8443


def test_address_and_ca_cert(tmp_path):
    s = _settings(tmp_path, CONFIG + 'address = "localhost:4443"\nca-cert = "proxide-ca.pem"\n')
    assert s.host == "example.cloudvault.m-files.com"
    assert s.address == "localhost:4443"
    assert s.target == "localhost:4443"
    assert s.ca_cert == "proxide-ca.pem"
    assert "hunter2" not in repr(s)


def test_no_address_means_host_and_port(tmp_path):
    s = _settings(tmp_path, CONFIG)
    assert s.address is None and s.ca_cert is None
    assert s.target == "example.cloudvault.m-files.com:8443"


@pytest.mark.parametrize("address", ["localhost", "localhost:", ":4443", "localhost:https"])
def test_bad_address_is_refused(tmp_path, address):
    with pytest.raises(ValueError, match="host:port"):
        _settings(tmp_path, CONFIG + f'address = "{address}"\n')


SSO_CONFIG = '''
[m-files.tool.common]
rest-api-url = "https://example.cloudvault.m-files.com/REST/"
vault = "{11111111-2222-3333-4444-555555555555}"

[m-files.tool.grpc]
auth = "sso"
sso-token = "access"
'''


def test_sso_needs_no_user_name_or_password(tmp_path, monkeypatch):
    monkeypatch.delenv("MFILES_GRPC_TOKEN", raising=False)
    path = tmp_path / "client-config.toml"
    path.write_text(SSO_CONFIG)
    s = load_settings(str(path))
    assert s.auth == "sso"
    assert s.sso_token == "access"
    assert s.username is None and s.password is None and s.token is None


def test_password_login_is_the_default(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG)
    assert load_settings(str(path)).auth == "password"


def test_password_login_without_a_password_is_an_error(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(CONFIG.replace('password = "hunter2hunter2"\n', ""))
    with pytest.raises(ValueError, match="password"):
        load_settings(str(path))


def test_unknown_auth_is_an_error(tmp_path):
    path = tmp_path / "client-config.toml"
    path.write_text(SSO_CONFIG.replace('auth = "sso"', 'auth = "kerberos"'))
    with pytest.raises(ValueError, match="kerberos"):
        load_settings(str(path))


def test_token_comes_from_the_environment_and_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("MFILES_GRPC_TOKEN", "eyJsecret-token")
    path = tmp_path / "client-config.toml"
    path.write_text(SSO_CONFIG)
    s = load_settings(str(path))
    assert s.token == "eyJsecret-token"
    assert "secret-token" not in repr(s)
