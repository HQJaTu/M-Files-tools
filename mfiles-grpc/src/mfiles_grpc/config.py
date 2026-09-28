# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Read connection settings from the same client-config.toml the REST tools use.

    [m-files.tool.common]
    rest-api-url = "https://<prefix>.cloudvault.m-files.com/REST/"
    username = "..."              # only for auth = "password"
    password = "..."
    vault = "{GUID}"

    [m-files.tool.grpc]           # optional
    port = 443                    # default: the port in rest-api-url, else 443
    address = "localhost:4443"    # connect here instead, e.g. a capturing proxy
    ca-cert = "proxy-ca.pem"      # trust these root certificates (PEM) instead of the system's
    auth = "sso"                  # "password" (default) or "sso"
    sso-token = "access"          # optional: "id" or "access"; default as the vault's plugin says

gRPC is served on the REST host. With address set, only the connection goes there: the
vault host from rest-api-url is still the name logged in to and the name the server
certificate must carry, so a proxy has to present a certificate for the vault host.

With auth = "sso", a token in the MFILES_GRPC_TOKEN environment variable is used
as it is, instead of signing in through the browser.
"""

import os
import tomllib
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

DEFAULT_PORT = 443
AUTH_METHODS = ("password", "sso")
SSO_TOKEN_KINDS = ("id", "access")
TOKEN_ENVIRONMENT_VARIABLE = "MFILES_GRPC_TOKEN"


@dataclass
class ConnectionSettings:
    host: str
    vault: str
    username: Optional[str] = None
    password: Optional[str] = None
    port: int = DEFAULT_PORT
    address: Optional[str] = None
    ca_cert: Optional[str] = None
    auth: str = "password"
    sso_token: Optional[str] = None
    token: Optional[str] = None

    def __repr__(self) -> str:
        # Never let the password or a token reach a log line or a traceback.
        def hidden(secret: Optional[str]) -> str:
            return "None" if secret is None else f"<{len(secret)} chars>"

        return (f"ConnectionSettings(host={self.host!r}, vault={self.vault!r}, "
                f"username={self.username!r}, password={hidden(self.password)}, "
                f"port={self.port}, address={self.address!r}, ca_cert={self.ca_cert!r}, "
                f"auth={self.auth!r}, sso_token={self.sso_token!r}, token={hidden(self.token)})")

    @property
    def target(self) -> str:
        """:return: Where the connection goes: address if set, else host:port"""
        return self.address or f"{self.host}:{self.port}"


def _check_address(address: str) -> str:
    """
    :param address: host:port, as the address setting takes it
    :return: address unchanged
    :raises ValueError: If it is not host:port
    """
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"address = {address!r}: expected host:port, e.g. \"localhost:4443\"")
    return address


def load_settings(path: str = "client-config.toml") -> ConnectionSettings:
    """
    :param path: TOML file in the client-config.toml layout
    :return: Settings for Client.connect()
    """
    with open(path, "rb") as f:
        config = tomllib.load(f)

    tool = config.get("m-files", {}).get("tool", {})
    common = tool.get("common", {})
    grpc_section = tool.get("grpc", {})

    # gRPC is served on the same host and port as the REST API, at the root path.
    url = urlparse(common["rest-api-url"])
    host = url.hostname
    if not host:
        raise ValueError(f"Cannot find a host name in rest-api-url {common['rest-api-url']!r}")
    address = grpc_section.get("address")

    auth = grpc_section.get("auth", "password")
    if auth not in AUTH_METHODS:
        raise ValueError(f"auth = {auth!r}: expected one of {', '.join(AUTH_METHODS)}")
    sso_token = grpc_section.get("sso-token")
    if sso_token is not None and sso_token not in SSO_TOKEN_KINDS:
        raise ValueError(f"sso-token = {sso_token!r}: expected one of {', '.join(SSO_TOKEN_KINDS)}")
    if auth == "password" and (not common.get("username") or not common.get("password")):
        raise ValueError('auth = "password" needs username and password in [m-files.tool.common]')

    return ConnectionSettings(
        host=host,
        vault=common["vault"],
        username=common.get("username"),
        password=common.get("password"),
        port=int(grpc_section.get("port", url.port or DEFAULT_PORT)),
        address=_check_address(address) if address is not None else None,
        ca_cert=grpc_section.get("ca-cert"),
        auth=auth,
        sso_token=sso_token,
        token=os.environ.get(TOKEN_ENVIRONMENT_VARIABLE) if auth == "sso" else None,
    )
