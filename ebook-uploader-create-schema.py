#!/usr/bin/env python3
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Create the metadata structure ebook-uploader.py expects, over the M-Files gRPC API.

SCHEMA.md describes it: object types eBook, Author, Publisher and eBook bundle, one class
on each, five property definitions and the eBook class's associations. MFWS cannot do
this (POST /structure/properties answers 405); gRPC's admin services can.

Re-entrant. Everything is looked up by name first, and only what is missing is created or
associated. On a vault that already has the schema the run changes nothing and says so.
Something that exists with the wrong shape (a value list where an object type should be, a
property with another data type, a class on another object type) is reported and left alone:
fixing it is a decision for a person in M-Files Admin.

Everything is checked before anything is written. The run first goes through the whole
schema without writing (that pass is all a --dry-run does) and collects every conflict; if
there is one, it reports them all and changes nothing.

Needs a user with vault administrator rights.
"""

import argparse
import logging
import sys
from typing import Optional

from mfiles_grpc import Client, load_settings, pb, structure

log = logging.getLogger(__name__)

# Built-ins that exist in every vault. New items copy their access control from these,
# so a fresh definition gets the vault's usual permissions rather than an empty ACL.
MFILES_OBJECT_TYPE_DOCUMENT = 0
MFILES_PROPERTY_NAME_OR_TITLE = 0
MFILES_PROPERTY_KEYWORDS = 26
# Built-in value list that holds the classes
MFILES_VALUE_LIST_CLASSES = 1

# Property definitions made by hand; the lookups to Author, Publisher and eBook bundle
# are not here, because M-Files generates them with each object type.
# Same names and data types as EBOOK_PROPERTIES in ebook-uploader.py.
EBOOK_PROPERTIES = [
    ("ISBN", pb.DATATYPE_TEXT),
    ("Publishing year", pb.DATATYPE_INTEGER),
    ("Page count", pb.DATATYPE_INTEGER),
    ("Source SHA-1", pb.DATATYPE_TEXT),
    ("Source", pb.DATATYPE_MULTI_LINE_TEXT),
]


class SchemaConflict(Exception):
    """Things exist by the right names but with the wrong shape."""

    def __init__(self, conflicts: list[str]):
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts


class SchemaBuilder:

    def __init__(self, client: Client, write: bool, report_level: int = logging.INFO):
        """
        :param write: False to only check: record changes and conflicts, write nothing
        :param report_level: Log level for the changes a checking pass would make
        """
        self.client = client
        self.write = write
        self.report_level = report_level
        self.changes: list[str] = []
        self.conflicts: list[str] = []

    def _change(self, description: str) -> bool:
        """
        Record a change.
        :return: True if it should be made now, False when only checking
        """
        self.changes.append(description)
        if not self.write:
            log.log(self.report_level, "Would %s", description)
        return self.write

    def _conflict(self, description: str) -> None:
        """
        Record something that exists with the wrong shape. A checking pass collects them all.
        While writing there should be none, as the check found none; one means the vault
        changed in between, and stops the run.
        """
        if self.write:
            raise SchemaConflict([description])
        self.conflicts.append(description)

    def object_type(self, name: str, can_have_files: bool) -> Optional[pb.ValueList]:
        """
        :return: The object type, or None if it is missing and this is a dry run
        """
        existing = structure.object_type_by_name(self.client, name)
        if existing is not None:
            if not existing.is_real_object_type:
                self._conflict(f"'{name}' ({existing.id}) is a value list, not an object type")
                return None
            if can_have_files and not existing.options.can_have_files:
                log.warning("Object type '%s' (%d) cannot hold files; eBook uploads need it to",
                            name, existing.id)
            log.debug("Object type '%s' exists: %d", name, existing.id)
            return existing

        if not self._change(f"create object type '{name}'"):
            return None
        template = self.client.object_types_admin.GetObjectTypeDef(
            pb.GetObjectTypeDefRequest(object_type=MFILES_OBJECT_TYPE_DOCUMENT)).obj_type_result.object_type
        request = pb.AddObjectTypeRequest(obj_type=pb.ValueListAdmin(object_type=pb.ValueList(
            name_singular=name,
            name_plural=f"{name}s",
            is_real_object_type=True,
            options=pb.OBJTYPEFLAGS(can_have_files=can_have_files,
                                    can_add_items=True,
                                    show_new_object_command_in_task_pane=True),
            acl=template.acl,
        )))
        created = self.client.object_types_admin.AddObjectType(request).obj_type_result.object_type
        log.info("Created object type '%s': %d, lookup property %d", name, created.id,
                 created.default_property_def)
        return created

    def property_def(self, name: str, datatype: int) -> Optional[int]:
        """
        :return: The property definition ID, or None if it is missing and this is a dry run
        """
        try:
            existing = structure.property_def_by_name(self.client, name, datatype)
        except TypeError as exc:
            self._conflict(str(exc))
            return None
        if existing is not None:
            log.debug("Property '%s' exists: %d", name, existing.id)
            return existing.id

        if not self._change(f"create property '{name}' ({pb.Datatype.Name(datatype)})"):
            return None
        template = self.client.property_defs_admin.GetPropertyDefAdmin(
            pb.GetPropertyDefAdminRequest(property_def=MFILES_PROPERTY_KEYWORDS)).ppropdefadmin.property_def
        request = pb.AddPropertyDefRequest(propdefadmin=pb.PropertyDefAdmin(
            property_def=pb.PropertyDef(
                name=name,
                data_type=datatype,
                allow_for_all_object_types=True,
                acl=template.acl,
                options=pb.PROPERTYDEFFLAGS(can_use_in_search=True),
            ),
            automatic_value=pb.AutomaticValue(automatic_numbering_increment=1, calculation_order=100),
        ))
        created = self.client.property_defs_admin.AddPropertyDef(request).new_property_definition.property_def
        log.info("Created property '%s': %d", name, created.id)
        return created.id

    def object_class(self, name: str, object_type: Optional[pb.ValueList],
                     property_ids: list[Optional[int]]) -> None:
        """
        Make sure a class exists on the object type and has the properties associated,
        none of them required. Associations already there are kept as they are.
        """
        wanted = [pid for pid in property_ids if pid is not None]
        existing = structure.object_class_by_name(self.client, name)

        if existing is None:
            if not self._change(f"create class '{name}'"
                                + (f" with {len(property_ids)} properties" if property_ids else "")):
                return
            if object_type is None:
                raise RuntimeError(f"Cannot create class '{name}' without its object type")
            request = pb.AddObjectClassRequest(
                doc_class=pb.DocClass(
                    value_list_item=pb.ValueListItem(item_info=pb.ItemInfo(
                        obj_id=pb.ObjID(type=MFILES_VALUE_LIST_CLASSES), name=name)),
                    object_type=object_type.id,
                    name_property_def=MFILES_PROPERTY_NAME_OR_TITLE,
                ),
                property_defs=[pb.AssociatedPropertyDef(property_def=MFILES_PROPERTY_NAME_OR_TITLE,
                                                        is_required=True)]
                + [pb.AssociatedPropertyDef(property_def=pid, is_required=False) for pid in wanted],
            )
            class_id = self.client.property_defs_admin.AddObjectClass(request).id
            log.info("Created class '%s': %d", name, class_id)
            return

        class_id = existing.base_info.item_info.obj_id.item_id.internal_id
        if object_type is None:
            # Its object type is still to be created (or is itself a conflict), so the class
            # that already exists by this name is on some other object type.
            self._conflict(f"Class '{name}' ({class_id}) is on object type {existing.object_type}, "
                           f"not on the object type it is needed for")
            return
        if existing.object_type != object_type.id:
            self._conflict(f"Class '{name}' ({class_id}) is on object type {existing.object_type}, "
                           f"expected '{object_type.name_singular}' ({object_type.id})")
            return
        associated = list(self.client.property_defs_admin.GetAssociatedPropertyDefs(
            pb.GetAssociatedPropertyDefsRequest(document_profile=class_id)).values)
        have = {a.property_def for a in associated}
        missing = [pid for pid in wanted if pid not in have]
        # A property still to be created on a dry run has no ID yet, but it will be missing too.
        unknown = len(property_ids) - len(wanted)
        if not missing and not unknown:
            log.debug("Class '%s' exists with its properties: %d", name, class_id)
            return

        if not self._change(f"associate {len(missing) + unknown} properties with class '{name}'"):
            return
        doc_class = next(c for c in self.client.property_defs_admin.GetObjectClasses(
            pb.GetObjectClassesRequest()).values
            if c.value_list_item.item_info.obj_id.item_id.internal_id == class_id)
        # The list also holds the implicit, server-maintained properties (Created, Single file ...);
        # sending those back as associations is refused with "The parameter is incorrect".
        normal = {p.id for p in structure.property_defs(self.client) if p.update_type == pb.UPDATE_TYPE_NORMAL}
        request = pb.ModifyObjectClassRequest(
            doc_class=doc_class,
            modify_associations=True,
            property_defs=[a for a in associated if a.property_def in normal]
            + [pb.AssociatedPropertyDef(property_def=pid, is_required=False) for pid in missing],
        )
        self.client.property_defs_admin.ModifyObjectClass(request)
        log.info("Associated properties %s with class '%s' (%d)", missing, name, class_id)


def build_schema(client: Client, options: argparse.Namespace) -> list[str]:
    """
    Check the whole schema without writing, then, unless it is a dry run or nothing is
    missing, go through it again making the changes.
    :return: What was changed (or, on a dry run, would be); empty when the schema was complete
    :raises SchemaConflict: With every conflict found. Nothing has been written.
    """
    check = SchemaBuilder(client, write=False,
                          report_level=logging.INFO if options.dry_run else logging.DEBUG)
    _build(check, options)
    if check.conflicts:
        raise SchemaConflict(check.conflicts)
    if options.dry_run or not check.changes:
        return check.changes

    writer = SchemaBuilder(client, write=True)
    _build(writer, options)
    return writer.changes


def _build(builder: SchemaBuilder, options: argparse.Namespace) -> None:
    ebook = builder.object_type(options.object_type, can_have_files=True)
    author = builder.object_type(options.author_type, can_have_files=False)
    publisher = builder.object_type(options.publisher_type, can_have_files=False)
    bundle = builder.object_type(options.bundle_type, can_have_files=False)
    properties = [builder.property_def(name, datatype) for name, datatype in EBOOK_PROPERTIES]

    # Each object type's generated multi-select lookup is what points an eBook at it.
    lookups = [t.default_property_def if t is not None else None for t in (author, publisher, bundle)]
    builder.object_class(options.object_class, ebook, lookups + properties)
    builder.object_class(options.author_type, author, [])
    builder.object_class(options.publisher_type, publisher, [])
    builder.object_class(options.bundle_type, bundle, [])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the object types, classes and properties ebook-uploader.py needs. "
                    "Safe to run again: only what is missing is created.")
    parser.add_argument('-c', '--config', default="client-config.toml",
                        help="Connection settings, as for ebook-uploader.py (default: %(default)s)")
    parser.add_argument('--object-type', default="eBook",
                        help="eBook object type name (default: %(default)s)")
    parser.add_argument('--object-class', default="eBook",
                        help="eBook class name (default: %(default)s)")
    parser.add_argument('--author-type', default="Author",
                        help="Author object type and class name (default: %(default)s)")
    parser.add_argument('--publisher-type', default="Publisher",
                        help="Publisher object type and class name (default: %(default)s)")
    parser.add_argument('--bundle-type', default="eBook bundle",
                        help="eBook bundle object type and class name (default: %(default)s)")
    parser.add_argument('--dry-run', action='store_true',
                        help="Report what is missing, change nothing")
    parser.add_argument('--log-level', default="INFO",
                        help="Logging level (default: %(default)s)")
    options = parser.parse_args()

    logging.basicConfig(format="%(levelname)-7s %(message)s", level=options.log_level.upper())

    # Conflicts are reported outside the handler: they are expected findings, not a traceback.
    conflicts: list[str] = []
    with Client.connect(load_settings(options.config)) as client:
        try:
            changes = build_schema(client, options)
        except SchemaConflict as exc:
            conflicts = exc.conflicts
    if conflicts:
        for conflict in conflicts:
            log.error("%s", conflict)
        log.error("Nothing was changed. Fix %s in M-Files Admin, then run this again.",
                  "it" if len(conflicts) == 1 else "them")
        return 2

    if not changes:
        log.info("No changes made: the vault already has the eBook schema.")
    elif options.dry_run:
        log.info("Dry run: %d changes needed, none made.", len(changes))
    else:
        log.info("%d changes made.", len(changes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
