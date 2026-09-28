# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Command line checks for an M-Files gRPC connection.

    mfiles-grpc capabilities     anonymous; proves the host speaks gRPC
    mfiles-grpc auth-config      anonymous; shows the vault's SSO (OAuth) settings
    mfiles-grpc login            logs in and out; proves the credentials
    mfiles-grpc check-session    logs in and makes one read that needs the session
    mfiles-grpc structure        lists object types, classes and property definitions
"""

import argparse
import logging
import sys

from google.protobuf import json_format

from . import sso, structure
from .client import Client, SessionNotAccepted
from .config import load_settings
from .proto import pb

log = logging.getLogger(__name__)


def _capabilities(args, settings) -> int:
    client = Client(settings.host, settings.port, settings.address, settings.ca_cert)
    try:
        caps = client.server_capabilities()
    finally:
        client.close()
    enabled = sorted(k for k, v in json_format.MessageToDict(
        caps, preserving_proto_field_name=True).items() if v is True)
    print(f"{settings.target} answers gRPC; {len(enabled)} capabilities enabled")
    for name in enabled:
        print(f"  {name}")
    return 0


def _auth_config(args, settings) -> int:
    client = Client(settings.host, settings.port, settings.address, settings.ca_cert)
    try:
        config = sso.discover(client, settings.vault)
    finally:
        client.close()
    for name, value in (
            ("plugin", config.plugin_name), ("configuration scope", config.configuration_scope),
            ("vault", config.vault_guid), ("client ID", config.client_id),
            ("authorization endpoint", config.authorization_endpoint),
            ("token endpoint", config.token_endpoint), ("scopes", " ".join(config.scopes)),
            ("redirect URI", config.redirect_uri), ("resource", config.resource),
            ("client secret", "set" if config.client_secret else "none"),
            ("token sent", "access" if config.use_access_token else "id")):
        print(f"{name:>22}: {value}")
    return 0


def _login(args, settings) -> int:
    with Client.connect(settings) as client:
        s = client.session
        print(f"Logged in: session ID {len(client.session_id)} bytes, "
              f"keep-alive every {s.keep_alive_interval_in_seconds} s")
    return 0


def _check_session(args, settings) -> int:
    with Client.connect(settings) as client:
        try:
            client.check_session()
        except SessionNotAccepted as e:
            print(f"Not accepted: {e}", file=sys.stderr)
            return 1
    print("Session accepted")
    return 0


def _structure(args, settings) -> int:
    with Client.connect(settings) as client:
        print("Object types:")
        for t in structure.object_types(client):
            if t.is_real_object_type:
                print(f"  {t.id:5}  {t.name_singular}")
        print("Classes:")
        for c in structure.object_classes(client):
            info = c.base_info.item_info
            print(f"  {info.obj_id.item_id.internal_id:5}  {info.name}  (object type {c.object_type})")
        print("Property definitions (custom):")
        for p in structure.property_defs(client):
            if not p.is_predefined:
                print(f"  {p.id:5}  {p.name}  [{pb.Datatype.Name(p.data_type)}]")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mfiles-grpc", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="client-config.toml", help="default: %(default)s")
    parser.add_argument("--log-level", default="WARNING")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities").set_defaults(run=_capabilities)
    commands.add_parser("auth-config").set_defaults(run=_auth_config)
    commands.add_parser("login").set_defaults(run=_login)
    commands.add_parser("check-session").set_defaults(run=_check_session)
    commands.add_parser("structure").set_defaults(run=_structure)

    args = parser.parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-5.5s]  [%(name)s] %(message)s",
        level=args.log_level.upper())
    return args.run(args, load_settings(args.config))


if __name__ == "__main__":
    sys.exit(main())
