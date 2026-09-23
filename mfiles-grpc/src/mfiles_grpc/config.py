# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Read connection settings from the same client-config.toml the REST tools use.

    [m-files.tool.common]
    rest-api-url = "https://<prefix>.cloudvault.m-files.com/REST/"
    username = "..."
    password = "..."
    vault = "{GUID}"

    [m-files.tool.grpc]           # optional
    port = 443
    session-header = "..."        # see README: how the session travels
    session-encoding = "hex"      # hex, base64 or raw
"""

import tomllib
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass
class ConnectionSettings:
    host: str
    vault: str
    username: str
    password: str
    port: int = 443
    session_header: Optional[str] = None
    session_encoding: str = "hex"

    def __repr__(self) -> str:
        # Never let the password reach a log line or a traceback.
        return (f"ConnectionSettings(host={self.host!r}, vault={self.vault!r}, "
                f"username={self.username!r}, password=<{len(self.password)} chars>, "
                f"port={self.port}, session_header={self.session_header!r}, "
                f"session_encoding={self.session_encoding!r})")


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

    # gRPC is served on the same host as the REST API, at the root path.
    host = urlparse(common["rest-api-url"]).hostname
    if not host:
        raise ValueError(f"Cannot find a host name in rest-api-url {common['rest-api-url']!r}")

    return ConnectionSettings(
        host=host,
        vault=common["vault"],
        username=common["username"],
        password=common["password"],
        port=int(grpc_section.get("port", 443)),
        session_header=grpc_section.get("session-header"),
        session_encoding=grpc_section.get("session-encoding", "hex"),
    )
