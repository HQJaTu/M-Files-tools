from unittest import mock

import pytest

from mfiles_grpc import proto, sso
from mfiles_grpc.config import ConnectionSettings
from mfiles_grpc.client import Client, SessionNotAccepted, call_metadata, _SessionInterceptor

SID = bytes(range(48))


def test_every_service_has_a_stub():
    names = proto.service_names()
    assert len(names) == 74
    assert {"IRPCLogin", "IRPCObjectOperations", "IRPCPropertyDefsAdmin"} <= set(names)
    for name in names:
        assert hasattr(proto.rpc, f"{name}Stub"), name


def test_call_metadata_carries_the_session_bytes_unchanged():
    md = dict(call_metadata(SID))
    assert md["mfiles-session-id-bin"] == SID
    assert md["mfiles-is-remote-call"] == "true"
    assert len(md["mfiles-activity-id-bin"]) == 16


def test_call_metadata_before_login_has_no_session():
    assert "mfiles-session-id-bin" not in dict(call_metadata(None))


def test_activity_id_is_new_for_every_call():
    first = dict(call_metadata(SID))["mfiles-activity-id-bin"]
    second = dict(call_metadata(SID))["mfiles-activity-id-bin"]
    assert first != second


class Details(tuple):
    # Stand-in for grpc.ClientCallDetails: a namedtuple with _replace().
    def __new__(cls, metadata):
        return super().__new__(cls, (metadata,))

    metadata = property(lambda self: self[0])

    def _replace(self, metadata):
        return Details(metadata)


def test_interceptor_appends_session_metadata():
    seen = {}
    interceptor = _SessionInterceptor(lambda: [("session-id", "abc")])
    interceptor.intercept_unary_unary(lambda d, r: seen.update(md=d.metadata),
                                      Details([("x", "1")]), None)
    assert seen["md"] == [("x", "1"), ("session-id", "abc")]


def test_interceptor_leaves_anonymous_calls_alone():
    seen = {}
    interceptor = _SessionInterceptor(lambda: [])
    details = Details(None)
    interceptor.intercept_unary_unary(lambda d, r: seen.update(d=d), details, None)
    assert seen["d"] is details


def test_client_sends_session_only_after_login():
    client = Client("example.invalid")
    try:
        assert "mfiles-session-id-bin" not in dict(client._session_metadata())
        client.session_id = SID
        assert dict(client._session_metadata())["mfiles-session-id-bin"] == SID
    finally:
        client.session_id = None
        client.close()


def test_unknown_service_is_a_clear_error():
    client = Client("example.invalid")
    try:
        with pytest.raises(ValueError, match="IRPCNope"):
            client.stub("IRPCNope")
    finally:
        client.close()


def test_login_request_uses_credentials_and_keeps_session():
    client = Client("example.invalid")
    login = mock.Mock()
    login.LogIn.return_value = proto.pb.LogInResponse(
        session_data=proto.pb.SessionData(session_id=SID, keep_alive_interval_in_seconds=60))
    client._stubs["IRPCLogin"] = login
    try:
        client.log_in("user", "secret", "{VAULT}")
        (request,), _ = login.LogIn.call_args
        assert request.authentication_data.type == proto.pb.AUTH_DATA_TYPE_CREDENTIALS
        assert request.authentication_data.data.credentials.username == "user"
        assert request.login_data.vault_guid == "{VAULT}"
        assert client.session_id == SID
    finally:
        client.close()
    login.LogOut.assert_called_once()


@pytest.fixture
def channel(monkeypatch):
    """Record how the gRPC channel is made instead of making one."""
    made = mock.MagicMock()
    monkeypatch.setattr("mfiles_grpc.client.grpc.secure_channel", made)
    credentials = mock.Mock(return_value="credentials")
    monkeypatch.setattr("mfiles_grpc.client.grpc.ssl_channel_credentials", credentials)
    return made, credentials


def test_channel_goes_to_host_and_port(channel):
    made, credentials = channel
    Client("vault.example", 8443)
    target, _ = made.call_args.args
    assert target == "vault.example:8443"
    credentials.assert_called_once_with(root_certificates=None)
    assert "grpc.ssl_target_name_override" not in dict(made.call_args.kwargs["options"])


def test_address_redirects_the_channel_but_keeps_the_vault_name(channel):
    made, _ = channel
    client = Client("vault.example", 443, address="localhost:4443")
    target, _ = made.call_args.args
    assert target == "localhost:4443"
    assert dict(made.call_args.kwargs["options"])["grpc.ssl_target_name_override"] == "vault.example"

    login = mock.Mock()
    login.LogIn.return_value = proto.pb.LogInResponse(session_data=proto.pb.SessionData(session_id=SID))
    client._stubs["IRPCLogin"] = login
    client.log_in("user", "secret", "{VAULT}")
    (request,), _ = login.LogIn.call_args
    assert request.login_data.server_hostname == "vault.example"


def test_ca_cert_is_trusted(channel, tmp_path):
    _, credentials = channel
    pem = tmp_path / "ca.pem"
    pem.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    Client("vault.example", ca_cert=str(pem))
    credentials.assert_called_once_with(root_certificates=b"-----BEGIN CERTIFICATE-----\n")


def test_token_login_sends_plugin_data_like_the_web_client():
    client = Client("example.invalid")
    login = mock.Mock()
    login.LogIn.return_value = proto.pb.LogInResponse(
        session_data=proto.pb.SessionData(session_id=SID, keep_alive_interval_in_seconds=60))
    client._stubs["IRPCLogin"] = login
    token = "eyJ" + "x" * 1500    # a JWT is far longer than values.text() allows
    try:
        client.log_in_with_token(token, "{VAULT}", "Entra", "vault-scope")
        (request,), _ = login.LogIn.call_args
        auth = request.authentication_data
        assert auth.type == proto.pb.AUTH_DATA_TYPE_PLUGIN
        plugin = auth.data.plugin
        assert plugin.plugin_name == "Entra"
        assert plugin.configuration_scope == "vault-scope"
        assert plugin.data_format == proto.pb.PLUGIN_AUTH_DATA_FORMAT_UNENCRYPTED
        assert plugin.authentication_attempt_identifier.data == bytes(32)
        assert [nv.key for nv in plugin.auth_data] == ["Token"]
        assert plugin.auth_data[0].value.type == proto.pb.DATATYPE_TEXT
        assert plugin.auth_data[0].value.data.text == token
        assert request.client_data.capabilities.long_authentication_attempt_identifier
        assert request.login_data.vault_guid == "{VAULT}"
        assert client.session_id == SID
    finally:
        client.close()


def test_token_login_without_a_session_is_refused():
    client = Client("example.invalid")
    login = mock.Mock()
    login.LogIn.return_value = proto.pb.LogInResponse(
        authentication_data=proto.pb.AuthDataServer(
            type=proto.pb.AUTH_DATA_TYPE_PLUGIN,
            data=proto.pb.AuthDataServerUnion(plugin=proto.pb.AuthDataServerUnion.PluginData(is_done=False))))
    client._stubs["IRPCLogin"] = login
    try:
        with pytest.raises(SessionNotAccepted, match="multi-step"):
            client.log_in_with_token("token", "{VAULT}", "Entra", "")
        assert client.session_id is None
    finally:
        client.close()


def sso_settings(**overrides):
    values = dict(host="example.invalid", vault="{VAULT}", auth="sso")
    values.update(overrides)
    return ConnectionSettings(**values)


def test_connect_with_sso_discovers_signs_in_and_logs_in():
    config = mock.Mock(plugin_name="Entra", configuration_scope="scope-1")
    with mock.patch.object(sso, "discover", return_value=config) as discover, \
            mock.patch.object(sso, "acquire_token", return_value="ID-TOKEN") as acquire, \
            mock.patch.object(Client, "log_in_with_token") as log_in:
        client = Client.connect(sso_settings())
    client.close()
    assert discover.call_args.args[1] == "{VAULT}"
    acquire.assert_called_once_with(config)
    log_in.assert_called_once_with("ID-TOKEN", "{VAULT}", "Entra", "scope-1")


def test_connect_with_a_given_token_skips_the_browser():
    config = mock.Mock(plugin_name="Entra", configuration_scope="")
    with mock.patch.object(sso, "discover", return_value=config), \
            mock.patch.object(sso, "acquire_token") as acquire, \
            mock.patch.object(Client, "log_in_with_token") as log_in:
        Client.connect(sso_settings(token="PASTED")).close()
    acquire.assert_not_called()
    assert log_in.call_args.args[0] == "PASTED"


def test_connect_can_force_the_access_token():
    config = sso.OAuthConfig(plugin_name="Entra", configuration_scope="", vault_guid="", client_id="c",
                             authorization_endpoint="a", token_endpoint="t", scopes=("openid",),
                             redirect_uri="http://localhost")
    with mock.patch.object(sso, "discover", return_value=config), \
            mock.patch.object(sso, "acquire_token", return_value="T") as acquire, \
            mock.patch.object(Client, "log_in_with_token"):
        Client.connect(sso_settings(sso_token="access")).close()
    assert acquire.call_args.args[0].use_access_token is True


def test_connect_with_sso_keeps_the_proxy_address(channel):
    made, _ = channel
    config = mock.Mock(plugin_name="Entra", configuration_scope="")
    with mock.patch.object(sso, "discover", return_value=config), \
            mock.patch.object(Client, "log_in_with_token"):
        Client.connect(sso_settings(host="vault.example", token="T", address="localhost:4443")).close()
    target, _ = made.call_args.args
    assert target == "localhost:4443"
