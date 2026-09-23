# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Read the vault's metadata structure and resolve its parts by name.

IDs differ between vaults; names are what a tool should depend on. Name matching
is by casefold. M-Files itself also folds some letters (ß equals ss), so two
names that differ only that way are the same name to the server.
"""

from typing import Optional

from .client import Client
from .proto import pb


def object_types(client: Client) -> list[pb.ValueList]:
    """:return: Object types and value lists (is_real_object_type tells them apart)"""
    return list(client.object_types.GetObjectTypes(pb.GetObjectTypesRequest()).object_types)


def property_defs(client: Client) -> list[pb.PropertyDef]:
    return list(client.property_defs.GetPropertyDefs(pb.GetPropertyDefsRequest()).property_defs)


def object_classes(client: Client) -> list[pb.ObjectClass]:
    return list(client.property_defs.GetObjectClassesAndGroups(
        pb.GetObjectClassesAndGroupsRequest()).classes)


def _by_name(items, name: str, name_of):
    wanted = name.casefold()
    matches = [item for item in items if name_of(item).casefold() == wanted]
    if len(matches) > 1:
        raise LookupError(f"{len(matches)} items are named {name!r}")
    return matches[0] if matches else None


def object_type_by_name(client: Client, name: str) -> Optional[pb.ValueList]:
    return _by_name(object_types(client), name, lambda t: t.name_singular)


def property_def_by_name(client: Client, name: str,
                         datatype: Optional[int] = None) -> Optional[pb.PropertyDef]:
    """
    :param datatype: If given, a property of another data type is an error, not a match
    :return: The property definition, or None if there is none by that name
    """
    found = _by_name(property_defs(client), name, lambda p: p.name)
    if found is not None and datatype is not None and found.data_type != datatype:
        raise TypeError(f"Property {name!r} ({found.id}) has data type "
                        f"{pb.Datatype.Name(found.data_type)}, expected {pb.Datatype.Name(datatype)}")
    return found


def object_class_by_name(client: Client, name: str) -> Optional[pb.ObjectClass]:
    return _by_name(object_classes(client), name, lambda c: c.base_info.item_info.name)
