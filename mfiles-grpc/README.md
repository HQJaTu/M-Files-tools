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
| Using that session on later calls | **open**, see below |
| Object and structure helpers | unit-tested offline only |

## How the session travels

`LogIn` returns a session ID, but none of the request messages has a field
for it, so it must travel in call metadata (HTTP/2 headers). The `.proto` does
not name the header and M-Files does not document it publicly. A REST
`X-Authentication` token sent as `x-authentication`, `authorization: Bearer`
or a cookie is **not** accepted (tested: UNAUTHENTICATED).

So the header is configuration, not code:

```toml
[m-files.tool.grpc]
session-header = "<metadata key>"   # a key ending in -bin carries raw bytes
session-encoding = "hex"            # hex, base64 or raw
```

Once you know it, confirm it with one read-only call:

```
mfiles-grpc check-session --header <key> --encoding <hex|base64|raw>
```

The authoritative answer is what the M-Files Desktop client itself sends on
port 443, or M-Files support.

## Install

```
pip install -e ".[dev]"
```

Configuration is read from the same `client-config.toml` as the other tools in
this repository (`[m-files.tool.common]`); the host is taken from `rest-api-url`.

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
mfiles-grpc login           # are the credentials good?
mfiles-grpc check-session   # is the session header right?
mfiles-grpc structure       # object types, classes, custom properties
```

## Safety built in

* `objects.set_properties()` always sends `remove_unspecified_properties=False`.
  With `True`, `SetProperties` deletes every property not in the request;
  across `SetPropertiesMultiple` that would wipe a library's metadata.
  Use the raw stub if you really mean it.
* `expected_version=` makes a write fail rather than land on a version newer
  than the one you read.
* `values.text()` refuses more than 100 characters. M-Files silently truncates
  single-line text at 100; use `values.multiline_text()`.
* `values.normalise_newlines()`: M-Files stores multi-line text with CRLF, so
  compare read-backs only after normalising.
* `ConnectionSettings` never prints the password.

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
