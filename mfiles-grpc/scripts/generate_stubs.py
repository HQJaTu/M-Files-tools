#!/usr/bin/env python3

# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Regenerate src/mfiles_grpc/_generated/ from the M-Files .proto file.

The .proto ships with the M-Files Desktop client, in
C:\\Program Files\\M-Files\\<client-version>\\Common\\Web\\GRPC\\proto\\
Re-run this after copying a newer one, then run the tests.

Usage: python scripts/generate_stubs.py [path/to/mfilesCombinedWithDataPush.proto]
"""

import os
import re
import sys

import grpc_tools
from grpc_tools import protoc

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROTO = os.path.join(HERE, "..", "mfilesCombinedWithDataPush.proto")
OUTPUT_DIR = os.path.join(HERE, "..", "src", "mfiles_grpc", "_generated")
MODULE = "mfilesCombinedWithDataPush"

# Backslashes Python accepts in a string literal. Anything else in the generated
# docstrings is a Windows path in an M-Files comment ("Filedata\Temp\FIX") and
# would raise a SyntaxWarning on every fresh import.
STRAY_BACKSLASH = re.compile(r'\\(?![\\\'"abfnrtv0-7xNuU\n])')


def _fix_generated(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        code = f.read()

    # grpc_tools emits a top-level import; inside a package it must be relative.
    code = code.replace(f"import {MODULE}_pb2 as", f"from . import {MODULE}_pb2 as")

    # Double every backslash that does not start a valid escape.
    code = STRAY_BACKSLASH.sub(r"\\\\", code)

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> int:
    proto = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROTO)
    proto_dir, proto_file = os.path.split(proto)
    well_known = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")

    rc = protoc.main([
        "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"-I{well_known}",
        f"--python_out={OUTPUT_DIR}",
        f"--grpc_python_out={OUTPUT_DIR}",
        proto_file,
    ])
    if rc != 0:
        print(f"protoc failed with {rc}", file=sys.stderr)
        return rc

    _fix_generated(os.path.join(OUTPUT_DIR, f"{MODULE}_pb2_grpc.py"))
    print(f"Generated from {proto} into {os.path.normpath(OUTPUT_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
