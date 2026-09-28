#!/usr/bin/env python3

# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
End-to-end check of the object helpers against a live vault.

Creates one throw-away eBook, then alters a property, adds three, empties one,
removes one, deletes the object and finally destroys it, reading the object back after each
step. Nothing else in the vault is touched: every write names the ID the create
returned. If a step fails the run stops and prints that ID, so the object can
be cleaned up by hand (search the vault for the title below).

Object type, class and properties are resolved by name, as in SCHEMA.md.

Usage: python scripts/live_object_test.py [--config client-config.toml] [--keep]
"""

import argparse
import logging
import sys

import grpc

from mfiles_grpc import Client, load_settings, objects, pb, structure, values

TITLE = "mfiles-grpc live test object - safe to delete"
NAME_OR_TITLE = 0
SINGLE_FILE = 22
KEYWORDS = 26  # not associated with the eBook class, so it can be removed
CLASS = 100

log = logging.getLogger("live_object_test")


class Vault:
    """IDs of what the test writes, looked up by name."""

    def __init__(self, client: Client):
        def prop(name, datatype):
            found = structure.property_def_by_name(client, name, datatype)
            if not found:
                raise LookupError(f"No property definition {name!r}")
            return found.id

        object_type = structure.object_type_by_name(client, "eBook")
        object_class = structure.object_class_by_name(client, "eBook")
        if not object_type or not object_class:
            raise LookupError("No eBook object type or class")
        self.object_type = object_type.id
        self.object_class = object_class.base_info.item_info.obj_id.item_id.internal_id
        self.class_value_list = next(p.value_list for p in structure.property_defs(client) if p.id == CLASS)
        self.page_count = prop("Page count", pb.DATATYPE_INTEGER)
        self.publishing_year = prop("Publishing year", pb.DATATYPE_INTEGER)
        self.source = prop("Source", pb.DATATYPE_MULTI_LINE_TEXT)


def read(client: Client, vault: Vault, object_id: int, step: str) -> tuple[int, dict]:
    version = objects.latest_version(client, vault.object_type, object_id)
    props = objects.get_property_values(client, vault.object_type, object_id, version)
    print(f"  {step}: version {version}, page count {props.get(vault.page_count)}, "
          f"year {props.get(vault.publishing_year)}, keywords {props.get(KEYWORDS)!r}, "
          f"source {props.get(vault.source)!r}")
    return version, props


def check(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)


def run(client: Client, vault: Vault, keep: bool) -> int:
    t = vault.object_type
    created = objects.create_object(client, t, {
        NAME_OR_TITLE: values.text(TITLE),
        CLASS: values.lookup(vault.class_value_list, vault.object_class),
        SINGLE_FILE: values.boolean(False),
        vault.page_count: values.integer(1),
    })
    object_id = created.object_info.obj_id.item_id.internal_id
    print(f"1. Created eBook {object_id}")
    try:
        version, props = read(client, vault, object_id, "read back")
        check(props.get(NAME_OR_TITLE) == TITLE, "title as created")
        check(props.get(vault.page_count) == 1, "page count as created")

        objects.set_properties(client, t, object_id, {vault.page_count: values.integer(2)},
                               expected_version=version)
        version, props = read(client, vault, object_id, "2. Altered page count")
        check(props.get(vault.page_count) == 2, "page count altered")

        objects.set_properties(client, t, object_id, {
            vault.publishing_year: values.integer(2026),
            vault.source: values.multiline_text("line one\nline two"),
            KEYWORDS: values.text("test, grpc"),
        }, expected_version=version)
        version, props = read(client, vault, object_id, "3. Added year, keywords and Source")
        check(props.get(vault.publishing_year) == 2026, "year added")
        check(props.get(KEYWORDS) == "test, grpc", "keywords added")
        check(values.normalise_newlines(props.get(vault.source) or "") == "line one\nline two", "Source added")
        check(props.get(vault.page_count) == 2 and props.get(NAME_OR_TITLE) == TITLE, "others kept")

        stale = version - 1
        try:
            objects.set_properties(client, t, object_id, {vault.page_count: values.integer(3)},
                                   expected_version=stale)
            raise AssertionError("write aimed at an older version was accepted")
        except grpc.RpcError as e:
            print(f"  Write aimed at version {stale} refused: {e.details()}")
        check(objects.latest_version(client, t, object_id) == version, "refused write left no version")

        # A property the class lists cannot be removed, only emptied.
        objects.set_properties(client, t, object_id, {vault.publishing_year: values.null(pb.DATATYPE_INTEGER)},
                               expected_version=version)
        version, props = read(client, vault, object_id, "4. Emptied year")
        check(props.get(vault.publishing_year) is None, "year emptied")

        objects.remove_properties(client, t, object_id, [KEYWORDS], expected_version=version)
        version, props = read(client, vault, object_id, "5. Removed keywords")
        check(KEYWORDS not in props, "keywords removed")
        check(props.get(vault.page_count) == 2 and props.get(vault.source), "others kept")

        if keep:
            print(f"--keep: leaving eBook {object_id} in the vault")
            return 0

        objects.delete_object(client, t, object_id)
        print("6. Deleted")
        objects.destroy_object(client, t, object_id)
        print("7. Destroyed")
        try:
            objects.latest_version(client, t, object_id)
            raise AssertionError("object still readable after destroy")
        except grpc.RpcError as e:
            print(f"  Reading it now fails as it should: {e.details()}")
        print("All steps passed.")
        return 0
    except BaseException:
        print(f"!! Stopped with eBook {object_id} ({TITLE!r}) still in the vault", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="client-config.toml", help="default: %(default)s")
    parser.add_argument("--keep", action="store_true", help="stop before deleting the test object")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    with Client.connect(load_settings(args.config)) as client:
        return run(client, Vault(client), args.keep)


if __name__ == "__main__":
    sys.exit(main())
