# mfiles-grpc

Python client for the M-Files gRPC API: the protocol M-Files' own clients use.
It reaches parts of the vault the REST API (MFWS) does not, most usefully the
metadata structure. `POST /REST/structure/properties` answers HTTP 405, while
gRPC has `IRPCPropertyDefsAdmin.AddPropertyDef`, `AddObjectClass`,
`IRPCObjectTypesAdmin.AddObjectType` and a declarative whole-structure
get/set (`IRPCDeclarativeMetadataStructure`).

> **Not a supported public API.** The `.proto` comes from the M-Files Desktop
> client install and can change with any server update. Regenerate the stubs
> (below) after upgrading, and run the tests.

## Status

| What | State |
|---|---|
| gRPC on the REST host, port 443, path `/MFiles.<Service>/<Method>` | verified live |
| The `.proto` matches the server (live replies decode field for field) | verified live |
| Anonymous calls (`GetServerCapabilities`, `GetPublicKeyAnonymous`) | verified live |
| `LogIn` with user name and password → 48-byte session ID | verified live |
| Using that session on later calls | verified live (2026-09-24) |
| Connecting through a capturing proxy (`address`, `ca-cert`; Proxide) | verified live (2026-09-25) |
| SSO: reading the vault's OAuth settings (`auth-config`), anonymously | verified live (2026-09-24) |
| SSO: browser sign-in, `LogIn` with the token, session accepted | verified live (2026-09-24) |
| Structure helpers, reading object properties | verified live |
| Writing object properties (`set_properties`, with `expected_version` guard) | verified live (2026-09-24) |
| `create_object`, `remove_properties`, `delete_object`, `destroy_object` | verified live (2026-09-24) |

`scripts/live_object_test.py` repeats that live check against a vault: it
creates a throwaway eBook, changes it, adds values, checks that a write aimed
at an old version is refused, empties one value and removes another, then
destroys the object. It **writes to the vault**; on failure it prints the ID
it left behind. `--keep` skips the delete.

```
PYTHONPATH=src python scripts/live_object_test.py --config ../client-config.toml
```

What it established:

* Creating an object needs `value_metadata` on every value (the helper adds
  it); without it the server answers "Type mismatch."
* An eBook cannot be created without `Single file` (22).
* A property the class lists cannot be removed, only set empty (null).
  `remove_properties` works only for properties the class does not list, such
  as `Keywords` (26).

## How the session travels

`LogIn` returns a session ID, but none of the request messages has a field
for it: it travels in call metadata (HTTP/2 headers). The `.proto` does not
say so; the header names came from M-Files. The client adds these to every call:

| Header | Value |
|---|---|
| `mfiles-session-id-bin` | `session_data.session_id` from `LogIn`, unchanged (after logging in) |
| `mfiles-is-remote-call` | `true` |
| `mfiles-activity-id-bin` | a new GUID for each call, for tracing |

gRPC base64-encodes `-bin` values itself. A call that needs a session and does
not carry `mfiles-session-id-bin` fails with UNAUTHENTICATED; a REST
`X-Authentication` token is not accepted in its place.

`mfiles-grpc check-session` logs in and makes one read-only call to confirm it.

## Logging in with SSO

Human users sign in through the vault's identity provider, not with an M-Files
password. The server does not run that sign-in for a client: the client gets a
token from the IdP itself and hands it to `LogIn`. The steps below mirror the
M-Files web client (26.10, `loginToServer` / `doPluginLogin` / `getConfig` in
`Common\Web\Public\mfapp-bundle-mfwebui.*.js`).

1. **Discovery, anonymous.** `IRPCLogin.GetAuthenticationConfiguration`, asked
   with both `vault_guid` and `host_name`, once with `ACCOUNT_TYPE_MFILES` and then
   with `ACCOUNT_TYPE_WINDOWS`. On M-Files Cloud only the Windows type answers.
   The plugin with assembly `MFiles.AuthenticationProviders.OAuth` carries the IdP
   settings as named values: `ClientID`, `AuthorizationEndpoint`,
   `TokenEndpoint`, `Scope`, `RedirectURI`, `Resource`, `ClientSecret`,
   `UseAccessTokenInWeb`. Its `system_configuration` gives the configuration
   scope (`Scope`, for example `*:WINDOWS:`). `GetAuthenticationPlugins` does not
   work here: anonymously it names the plugin but omits its configuration.
2. **Sign-in: authorization code with PKCE.** Like M-Files Desktop, the redirect is
   `RedirectURI`, or `http://localhost` when there is none. On M-Files Cloud it is
   `http://localhost/signin-oidc`. The IdP compares the redirect exactly, so the
   one-shot listener binds its exact port, which is **80** when none is given. It
   binds on both `127.0.0.1` and `::1`. The browser does the rest, including MFA.
3. **`LogIn` with `AUTH_DATA_TYPE_PLUGIN`**, in one round trip:

   | Field | Value |
   |---|---|
   | `plugin.plugin_name` | the plugin's `name` |
   | `plugin.configuration_scope` | `system_configuration["Scope"]` |
   | `plugin.authentication_attempt_identifier.data` | 32 zero bytes |
   | `plugin.data_format` | `PLUGIN_AUTH_DATA_FORMAT_UNENCRYPTED` |
   | `plugin.auth_data` | one named value, `Token`, as `DATATYPE_TEXT` |

   The token is the **ID token**, or the access token when `UseAccessTokenInWeb`
   is true. M-Files Cloud sets it to true. The session that comes back is the same kind as a password login,
   so everything after `LogIn` is unchanged.

Configure it with:

```toml
[m-files.tool.common]
rest-api-url = "https://<vault>.cloudvault.m-files.com/REST/"
vault = "{GUID}"                # no username or password

[m-files.tool.grpc]
auth = "sso"
# sso-token = "access"          # if the server refuses the ID token
```

`mfiles-grpc auth-config` shows what discovery found, anonymously. Check it
first. The redirect URI must be a loopback address. If the vault's IdP accepts
only the web client's `/signin-oidc`, a command line client cannot receive the
sign-in. In that case, put a token obtained elsewhere in the
`MFILES_GRPC_TOKEN` environment variable; it is used instead of the browser.
It goes in the environment rather than on the command line so that it does not
land in shell history or process listings.

On Linux, binding port 80 needs root or `CAP_NET_BIND_SERVICE`. Without either,
use `MFILES_GRPC_TOKEN`.

## Install

```
pip install -e ".[dev]"
```

Configuration is read from the same `client-config.toml` as the other tools in
this repository (`[m-files.tool.common]`); the host and port are taken from
`rest-api-url` (port 443 when the URL has none). An optional `[m-files.tool.grpc]`
section overrides them:

```toml
[m-files.tool.grpc]
port = 443                     # overrides the port in rest-api-url
address = "localhost:4443"     # connect here instead, e.g. a capturing proxy
ca-cert = "proxide_ca.crt"     # trust these root certificates (PEM) instead of the system's
auth = "sso"                   # "password" (default) or "sso"; see above
sso-token = "access"           # optional: "id" or "access"
```

### Capturing traffic through a proxy

To watch the calls in a man-in-the-middle proxy such as
[Proxide](https://github.com/Rantanen/proxide), leave `rest-api-url` at the real vault
and set `address` and `ca-cert`:

```
proxide monitor -l 4443 -t <vault>.cloudvault.m-files.com:443
```

```toml
[m-files.tool.grpc]
address = "localhost:4443"
ca-cert = "/path/to/proxide_ca.crt"
```

With `address` set only the connection goes there. The vault host is still the
name sent in the TLS handshake (SNI) and at login, and the name the certificate is
checked against. Proxide makes its certificate from that name and forwards it to
the vault. Do **not** point `rest-api-url` at the proxy instead: the handshake and
login would then name `localhost`, and the REST tools reading the same file would go
through the proxy too.

The capture contains the login request, **password included**; treat the proxy's
log as secret. With SSO it contains the token instead, which is just as secret.

## Use

```python
from mfiles_grpc import Client, load_settings, objects, structure, values, pb

with Client.connect(load_settings()) as client:
    source = structure.property_def_by_name(client, "Source", pb.DATATYPE_MULTI_LINE_TEXT)
    print(objects.get_property_values(client, 101, 214))
    objects.set_properties(client, 101, 214,
                           {source.id: values.multiline_text("…")},
                           expected_version=3)
```

Every service in the `.proto` is available as `client.stub("IRPC<Name>")`,
with the common ones as attributes: `client.objects`, `client.object_types`,
`client.property_defs`, `client.value_lists`, `client.search`,
`client.property_defs_admin`, `client.object_types_admin`. Messages and enum
values are on `pb` (`pb.SetPropertiesRequest`, `pb.DATATYPE_TEXT`).

### Command line

```
mfiles-grpc capabilities    # anonymous; does the host speak gRPC?
mfiles-grpc auth-config     # anonymous; the vault's SSO settings
mfiles-grpc login           # are the credentials good?
mfiles-grpc check-session   # is the session accepted?
mfiles-grpc structure       # object types, classes, custom properties
```

## Safety built in

* `objects.set_properties()` always sends `remove_unspecified_properties=False`.
  With `True`, `SetProperties` deletes every property not in the request;
  across `SetPropertiesMultiple` that would wipe a library's metadata.
  Use the raw stub if you really mean it.
* `expected_version=` makes a write fail rather than land on a version newer
  than the one you read. Without it, the latest version is looked up first:
  `GetProperties` answers "Not found" to the `LATEST` version marker, so the
  helpers always send a real version number.
* `values.text()` refuses more than 100 characters. M-Files silently truncates
  single-line text at 100; use `values.multiline_text()`.
* `values.normalise_newlines()`: M-Files stores multi-line text with CRLF, so
  compare read-backs only after normalising.
* `ConnectionSettings` never prints the password or the SSO token.

Unchanged by the protocol: `Comment` (33) is per-version and not carried to new
versions; lookup names fold `ß` to `ss`; the Windows client still fails on
paths over 260 characters.

## Source of the .proto

| | |
|---|---|
| File | `mfilesCombinedWithDataPush.proto` |
| Taken from | M-Files Desktop client **26.9.16459.6**, `C:\Program Files\M-Files\26.9.16459.6\Common\Web\GRPC\proto\` |
| Date | 2026-09-23 |
| Size | 1053262 bytes |
| SHA-256 | `fcdfe3e144871941436b1869c28a25047d92db16771b2c7d3c31dbc7e3edaf0a` |

After a client upgrade, compare the new client's file against this hash:

```
sha256sum mfilesCombinedWithDataPush.proto                                   # Linux
Get-FileHash -Algorithm SHA256 mfilesCombinedWithDataPush.proto              # PowerShell
```

If it differs, copy the new file here, regenerate the stubs, run the tests, and
update this table.

## Regenerating the stubs

```
python scripts/generate_stubs.py [path/to/mfilesCombinedWithDataPush.proto]
```

Defaults to `mfilesCombinedWithDataPush.proto` in this directory. The script makes the
generated import package-relative and escapes the Windows paths in M-Files'
comments that would otherwise raise `SyntaxWarning` on import.

## Tests

```
pytest
```

Offline; they need no vault.
