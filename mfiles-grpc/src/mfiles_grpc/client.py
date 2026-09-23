# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Connection, login and session handling for the M-Files gRPC API.

Verified against an M-Files Cloud vault (2026-09-23):
  * gRPC is served on the REST host, port 443, at /MFiles.<Service>/<Method>.
  * Anonymous calls work (GetServerCapabilities, GetPublicKeyAnonymous).
  * IRPCLogin/LogIn with AUTH_DATA_TYPE_CREDENTIALS returns a 48-byte session ID.

NOT yet verified: which call metadata carries that session ID on later calls.
The .proto does not say. It is configurable (session_header / session_encoding)
until it is known; see the README.
"""

import base64
import logging
from typing import Callable, Optional

import grpc

from .config import ConnectionSettings
from .proto import pb, rpc

log = logging.getLogger(__name__)

SESSION_ENCODINGS = ("hex", "base64", "raw")
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class SessionNotAccepted(Exception):
    """The server answered UNAUTHENTICATED to a call made with our session."""


def encode_session(session_id: bytes, header: str, encoding: str) -> str | bytes:
    """
    Turn a session ID into a call-metadata value.

    :param session_id: Raw session ID from LogIn
    :param header: Metadata key. gRPC requires a key ending in '-bin' to carry bytes.
    :param encoding: 'hex', 'base64' or 'raw' (ignored for '-bin' keys)
    :return: Metadata value
    """
    if header.endswith("-bin"):
        return session_id
    if encoding == "hex":
        return session_id.hex()
    if encoding == "base64":
        return base64.b64encode(session_id).decode("ascii")
    if encoding == "raw":
        return session_id.decode("ascii")
    raise ValueError(f"Unknown session encoding {encoding!r}, expected one of {SESSION_ENCODINGS}")


class _SessionInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Adds the session metadata, when there is a session, to every unary call."""

    def __init__(self, metadata: Callable[[], list[tuple[str, str | bytes]]]):
        self._metadata = metadata

    def intercept_unary_unary(self, continuation, client_call_details, request):
        extra = self._metadata()
        if extra:
            client_call_details = client_call_details._replace(
                metadata=list(client_call_details.metadata or []) + extra)
        return continuation(client_call_details, request)


class Client:
    """
    One gRPC channel to an M-Files server, optionally logged in to one vault.

    Stubs for any service are available as ``client.stub('IRPCObjectOperations')``,
    and the common ones as attributes (``client.objects``, ``client.property_defs`` ...).

    Use as a context manager so the session is logged out and the channel closed:

        with Client.connect(load_settings()) as client:
            ...
    """

    def __init__(self, host: str, port: int = 443,
                 session_header: Optional[str] = None, session_encoding: str = "hex"):
        if session_encoding not in SESSION_ENCODINGS:
            raise ValueError(f"Unknown session encoding {session_encoding!r}")
        self.host = host
        self.port = port
        self.session_header = session_header
        self.session_encoding = session_encoding
        self.session_id: Optional[bytes] = None
        self.session: Optional[pb.SessionData] = None
        self.vault: Optional[str] = None

        self._raw_channel = grpc.secure_channel(
            f"{host}:{port}", grpc.ssl_channel_credentials(),
            options=[("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
                     ("grpc.max_send_message_length", MAX_MESSAGE_BYTES)])
        self.channel = grpc.intercept_channel(self._raw_channel,
                                              _SessionInterceptor(self._session_metadata))
        self._stubs: dict[str, object] = {}

    @classmethod
    def connect(cls, settings: ConnectionSettings) -> "Client":
        """
        :param settings: From config.load_settings()
        :return: A client logged in to settings.vault
        """
        client = cls(settings.host, settings.port,
                     session_header=settings.session_header,
                     session_encoding=settings.session_encoding)
        try:
            client.log_in(settings.username, settings.password, settings.vault)
        except Exception:
            client.close()
            raise
        return client

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _session_metadata(self) -> list[tuple[str, str | bytes]]:
        if self.session_id is None or not self.session_header:
            return []
        return [(self.session_header,
                 encode_session(self.session_id, self.session_header, self.session_encoding))]

    def stub(self, service: str):
        """
        :param service: Service name from the .proto, e.g. 'IRPCObjectOperations'
        :return: Stub whose methods are the service's RPCs
        """
        if service not in self._stubs:
            try:
                stub_class = getattr(rpc, f"{service}Stub")
            except AttributeError:
                raise ValueError(f"No service {service!r} in the M-Files .proto") from None
            self._stubs[service] = stub_class(self.channel)
        return self._stubs[service]

    # The services used most; anything else through stub().
    login_service = property(lambda self: self.stub("IRPCLogin"))
    objects = property(lambda self: self.stub("IRPCObjectOperations"))
    object_types = property(lambda self: self.stub("IRPCObjectTypes"))
    property_defs = property(lambda self: self.stub("IRPCPropertyDefs"))
    value_lists = property(lambda self: self.stub("IRPCValueLists"))
    search = property(lambda self: self.stub("IRPCSearch"))
    property_defs_admin = property(lambda self: self.stub("IRPCPropertyDefsAdmin"))
    object_types_admin = property(lambda self: self.stub("IRPCObjectTypesAdmin"))

    def server_capabilities(self) -> pb.ServerVaultCapabilities:
        """Anonymous; works without logging in."""
        return self.login_service.GetServerCapabilities(pb.GetServerCapabilitiesRequest()).server_capabilities

    def log_in(self, username: str, password: str, vault: str,
               client_name: str = "mfiles-grpc") -> pb.SessionData:
        """
        Log in with an M-Files user name and password.

        :param vault: Vault GUID, with or without braces
        :return: The session; its session_id is kept on the client
        """
        request = pb.LogInRequest(
            environment_data=pb.EnvironmentData(client_name=client_name, host_platform="Python"),
            client_data=pb.ClientData(type=pb.CLIENT_TYPE_SERVER_API, language="en"),
            login_data=pb.LoginData(server_hostname=self.host, vault_guid=vault),
            authentication_data=pb.AuthDataClient(
                type=pb.AUTH_DATA_TYPE_CREDENTIALS,
                data=pb.AuthDataClientUnion(credentials=pb.Credentials(
                    username=username, password=password, type=pb.CREDENTIALS_TYPE_MFILES))))
        response = self.login_service.LogIn(request)
        if not response.session_data.session_id:
            raise SessionNotAccepted("LogIn returned no session ID")

        self.session = response.session_data
        self.session_id = response.session_data.session_id
        self.vault = vault
        log.info("Logged in to %s vault %s: session ID %d bytes, keep-alive every %d s",
                 self.host, vault, len(self.session_id), self.session.keep_alive_interval_in_seconds)
        if not self.session_header:
            log.warning("No session header configured: calls that need a session will fail "
                        "with UNAUTHENTICATED. See README, 'How the session travels'.")
        return self.session

    def check_session(self) -> None:
        """
        Make one cheap read-only call that needs a session.

        :raises SessionNotAccepted: The server did not accept the session metadata
        """
        try:
            self.object_types.GetObjectTypes(pb.GetObjectTypesRequest())
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                raise SessionNotAccepted(
                    f"Server rejected the session sent as {self.session_header!r} "
                    f"({self.session_encoding}): {e.details()}") from e
            raise

    def keep_alive(self) -> None:
        """Call at least every session.keep_alive_interval_in_seconds on long runs."""
        self.login_service.KeepAlive(pb.KeepAliveRequest())

    def close(self) -> None:
        if self.session_id is not None:
            try:
                self.login_service.LogOut(pb.LogOutRequest())
            except grpc.RpcError as e:
                # Best effort: an unrecognised session cannot be logged out either,
                # and the server times it out.
                log.debug("LogOut failed: %s", e.code().name)
            self.session_id = None
            self.session = None
        self._raw_channel.close()
