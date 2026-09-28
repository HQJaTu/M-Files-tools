# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Connection, login and session handling for the M-Files gRPC API.

Verified against an M-Files Cloud vault (2026-09-23, 2026-09-24):
  * gRPC is served on the REST host and port (443 for M-Files Cloud), at
    /MFiles.<Service>/<Method>.
  * Anonymous calls work (GetServerCapabilities, GetPublicKeyAnonymous).
  * IRPCLogin/LogIn with AUTH_DATA_TYPE_CREDENTIALS returns a 48-byte session ID.
  * SSO logs in with AUTH_DATA_TYPE_PLUGIN (log_in_with_token), giving the same kind of session.
  * Later calls are authenticated by that session ID in call metadata; see
    call_metadata(). The .proto does not say this; the header names came from M-Files.
"""

import dataclasses
import logging
import uuid
from typing import Callable, Optional

import grpc

from . import sso
from .config import ConnectionSettings
from .proto import pb, rpc

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# Call metadata M-Files' own clients send. gRPC base64-encodes '-bin' values itself,
# so these carry raw bytes.
SESSION_HEADER = "mfiles-session-id-bin"          # LogIn's session_id, unchanged
REMOTE_CALL_HEADER = "mfiles-is-remote-call"
ACTIVITY_HEADER = "mfiles-activity-id-bin"        # a new GUID per call, for tracing

# The named value an SSO login carries its token in, as the M-Files web client sends it.
PLUGIN_TOKEN_KEY = "Token"


class SessionNotAccepted(Exception):
    """The server answered UNAUTHENTICATED to a call made with our session."""


def call_metadata(session_id: Optional[bytes]) -> list[tuple[str, str | bytes]]:
    """
    Metadata for one call.

    :param session_id: Session ID from LogIn, or None before logging in
    :return: Metadata pairs; the session header only when there is a session
    """
    metadata: list[tuple[str, str | bytes]] = [
        (REMOTE_CALL_HEADER, "true"),
        (ACTIVITY_HEADER, uuid.uuid4().bytes),
    ]
    if session_id is not None:
        metadata.insert(0, (SESSION_HEADER, session_id))
    return metadata


class _SessionInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Adds the M-Files metadata, with the session when there is one, to every unary call."""

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

    def __init__(self, host: str, port: int = 443, address: Optional[str] = None,
                 ca_cert: Optional[str] = None):
        """
        :param host: The vault host: logged in to, and the name its certificate must carry
        :param port: The vault's port
        :param address: host:port to connect to instead, e.g. a capturing proxy. The
            certificate is still checked against host, so the proxy must present one for it.
        :param ca_cert: PEM file of root certificates to trust instead of the system's
        """
        self.host = host
        self.port = port
        self.target = address or f"{host}:{port}"
        self.session_id: Optional[bytes] = None
        self.session: Optional[pb.SessionData] = None
        self.vault: Optional[str] = None

        options = [("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
                   ("grpc.max_send_message_length", MAX_MESSAGE_BYTES)]
        if address:
            options.append(("grpc.ssl_target_name_override", host))
            log.info("Connecting to %s through %s", host, address)
        root_certificates = None
        if ca_cert:
            with open(ca_cert, "rb") as f:
                root_certificates = f.read()
        self._raw_channel = grpc.secure_channel(
            self.target, grpc.ssl_channel_credentials(root_certificates=root_certificates),
            options=options)
        self.channel = grpc.intercept_channel(self._raw_channel,
                                              _SessionInterceptor(self._session_metadata))
        self._stubs: dict[str, object] = {}

    @classmethod
    def connect(cls, settings: ConnectionSettings) -> "Client":
        """
        :param settings: From config.load_settings()
        :return: A client logged in to settings.vault
        """
        client = cls(settings.host, settings.port, settings.address, settings.ca_cert)
        try:
            if settings.auth == "sso":
                config = sso.discover(client, settings.vault)
                if settings.sso_token:
                    config = dataclasses.replace(config, use_access_token=settings.sso_token == "access")
                token = settings.token or sso.acquire_token(config)
                client.log_in_with_token(token, settings.vault, config.plugin_name, config.configuration_scope)
            else:
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
        return call_metadata(self.session_id)

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
        return self._log_in(pb.AuthDataClient(
            type=pb.AUTH_DATA_TYPE_CREDENTIALS,
            data=pb.AuthDataClientUnion(credentials=pb.Credentials(
                username=username, password=password, type=pb.CREDENTIALS_TYPE_MFILES))),
            vault, client_name)

    def log_in_with_token(self, token: str, vault: str, plugin_name: str, configuration_scope: str,
                          client_name: str = "mfiles-grpc") -> pb.SessionData:
        """
        Log in with a token from the vault's identity provider (SSO); see sso.py.

        The request is the one the M-Files web client sends: the token as the only
        named value, "Token", with an all-zero attempt ID, in one round trip.

        :param token: ID token, or access token if the plugin is configured for it
        :param plugin_name: OAuthConfig.plugin_name
        :param configuration_scope: OAuthConfig.configuration_scope
        :return: The session; its session_id is kept on the client
        """
        # Not values.text(): it refuses more than 100 characters, and a JWT is far longer.
        token_value = pb.TypedValue(type=pb.DATATYPE_TEXT, data=pb.TypedValueUnion(text=token))
        return self._log_in(pb.AuthDataClient(
            type=pb.AUTH_DATA_TYPE_PLUGIN,
            data=pb.AuthDataClientUnion(plugin=pb.AuthDataClientUnion.PluginData(
                authentication_attempt_identifier=pb.AuthenticationAttemptIdentifier(data=bytes(32)),
                plugin_name=plugin_name,
                configuration_scope=configuration_scope,
                data_format=pb.PLUGIN_AUTH_DATA_FORMAT_UNENCRYPTED,
                auth_data=[pb.NamedValue(key=PLUGIN_TOKEN_KEY, value=token_value)]))),
            vault, client_name)

    def _log_in(self, authentication: pb.AuthDataClient, vault: str, client_name: str) -> pb.SessionData:
        request = pb.LogInRequest(
            environment_data=pb.EnvironmentData(client_name=client_name, host_platform="Python"),
            client_data=pb.ClientData(
                type=pb.CLIENT_TYPE_SERVER_API, language="en",
                # The 32-byte attempt ID of a plugin login is the long form.
                capabilities=pb.ClientCapabilities(long_authentication_attempt_identifier=True)),
            login_data=pb.LoginData(server_hostname=self.host, vault_guid=vault),
            authentication_data=authentication)
        response = self.login_service.LogIn(request)
        if not response.session_data.session_id:
            if response.authentication_data.data.WhichOneof("data") == "plugin" \
                    and not response.authentication_data.data.plugin.is_done:
                raise SessionNotAccepted("LogIn wants a multi-step plugin exchange, which is not supported")
            raise SessionNotAccepted("LogIn returned no session ID")

        self.session = response.session_data
        self.session_id = response.session_data.session_id
        self.vault = vault
        log.info("Logged in to %s vault %s: session ID %d bytes, keep-alive every %d s",
                 self.host, vault, len(self.session_id), self.session.keep_alive_interval_in_seconds)
        return self.session

    def check_session(self) -> None:
        """
        Make one cheap read-only call that needs a session.

        :raises SessionNotAccepted: The server did not accept the session
        """
        try:
            self.object_types.GetObjectTypes(pb.GetObjectTypesRequest())
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                raise SessionNotAccepted(f"Server rejected the session: {e.details()}") from e
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
