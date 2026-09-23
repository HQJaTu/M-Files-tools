import base64
from unittest import mock

import pytest

from mfiles_grpc import proto
from mfiles_grpc.client import Client, encode_session, _SessionInterceptor

SID = bytes(range(48))


def test_every_service_has_a_stub():
    names = proto.service_names()
    assert len(names) == 74
    assert {"IRPCLogin", "IRPCObjectOperations", "IRPCPropertyDefsAdmin"} <= set(names)
    for name in names:
        assert hasattr(proto.rpc, f"{name}Stub"), name


@pytest.mark.parametrize("header,encoding,expected", [
    ("session-id", "hex", SID.hex()),
    ("session-id", "base64", base64.b64encode(SID).decode()),
    ("session-id-bin", "hex", SID),
])
def test_encode_session(header, encoding, expected):
    assert encode_session(SID, header, encoding) == expected


def test_encode_session_rejects_unknown_encoding():
    with pytest.raises(ValueError):
        encode_session(SID, "session-id", "rot13")


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


def test_no_session_metadata_before_login_or_without_header():
    client = Client("example.invalid", session_header="session-id")
    try:
        assert client._session_metadata() == []
        client.session_id = SID
        assert client._session_metadata() == [("session-id", SID.hex())]
        client.session_header = None
        assert client._session_metadata() == []
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
