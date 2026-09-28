# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Reading and writing the properties of vault objects.

The raw IRPCObjectOperations stub has 127 methods and some sharp edges; these
helpers cover the everyday cases and keep the dangerous flags out of reach.
"""

from typing import Mapping, Optional

from .client import Client
from .proto import pb
from .values import to_python


def obj_id(object_type: int, object_id: int) -> pb.ObjID:
    return pb.ObjID(type=object_type, item_id=pb.ItemID(internal_id=object_id))


def obj_ver(object_type: int, object_id: int, version: Optional[int] = None) -> pb.ObjVer:
    """
    :param version: A specific version, or None for the LATEST marker. GetProperties
                    answers "Not found" to LATEST; the helpers below use
                    resolve_obj_ver() to send a real version number instead.
    """
    if version is None:
        ver = pb.ObjVerVersion(type=pb.OBJ_VER_VERSION_TYPE_LATEST)
    else:
        ver = pb.ObjVerVersion(type=pb.OBJ_VER_VERSION_TYPE_SPECIFIC, internal_version=version)
    return pb.ObjVer(obj_id=obj_id(object_type, object_id), version=ver)


def latest_version(client: Client, object_type: int, object_id: int) -> int:
    """:return: Version number of the latest checked-in version"""
    response = client.objects.GetLatestObjectVersion(
        pb.GetLatestObjectVersionRequest(obj_id=obj_id(object_type, object_id)))
    return response.object_version.internal_version


def resolve_obj_ver(client: Client, object_type: int, object_id: int,
                    version: Optional[int] = None) -> pb.ObjVer:
    """
    :param version: A specific version, or None to look up the latest one
    :return: ObjVer naming a specific version, which every call accepts
    """
    if version is None:
        version = latest_version(client, object_type, object_id)
    return obj_ver(object_type, object_id, version)


def get_properties(client: Client, object_type: int, object_id: int,
                   version: Optional[int] = None) -> dict[int, pb.TypedValue]:
    """
    :return: Property definition ID -> value, for one version (default latest)
    """
    response = client.objects.GetProperties(
        pb.GetPropertiesRequest(obj_ver=resolve_obj_ver(client, object_type, object_id, version)))
    return {p.property_def: p.value for p in response.properties}


def get_property_values(client: Client, object_type: int, object_id: int,
                        version: Optional[int] = None) -> dict[int, object]:
    """Same as get_properties(), converted to Python values."""
    return {pd: to_python(v) for pd, v in get_properties(client, object_type, object_id, version).items()}


def set_properties(client: Client, object_type: int, object_id: int,
                   values: Mapping[int, pb.TypedValue],
                   expected_version: Optional[int] = None) -> pb.SetPropertiesResponse:
    """
    Add or replace some properties of an object, leaving all others as they are.

    The server checks the object out, writes and checks it in as one operation,
    creating one new version. Nothing else is removed: the call always sends
    remove_unspecified_properties=False, which is the flag that would otherwise
    delete every property not in ``values``.

    :param values: Property definition ID -> value (see mfiles_grpc.values)
    :param expected_version: If given, fail instead of writing when the object has
                             moved past this version since it was read
    """
    request = pb.SetPropertiesRequest(
        obj_ver=resolve_obj_ver(client, object_type, object_id, expected_version),
        allow_modifying_checked_in_object=True,
        fail_if_newer_version_exists=expected_version is not None,
        remove_unspecified_properties=False,
        properties=[pb.PropertyValue(property_def=pd, value=v) for pd, v in values.items()],
    )
    return client.objects.SetProperties(request)


def remove_properties(client: Client, object_type: int, object_id: int,
                      property_defs: list[int],
                      expected_version: Optional[int] = None) -> pb.SetPropertiesResponse:
    """
    Remove the named properties from an object; everything else stays.

    Only properties the object's class does not list can be removed; the server
    refuses the rest ("cannot be removed from the object"). Empty those instead
    with set_properties() and values.null().
    """
    request = pb.SetPropertiesRequest(
        obj_ver=resolve_obj_ver(client, object_type, object_id, expected_version),
        allow_modifying_checked_in_object=True,
        fail_if_newer_version_exists=expected_version is not None,
        remove_unspecified_properties=False,
        remove=property_defs,
    )
    return client.objects.SetProperties(request)


# What the server attaches to values it stores. Left empty, create answers
# "Type mismatch.": the server reads confidence as a number.
_NO_VALUE_METADATA = pb.TypedValueMetadata(confidence="-1", batch_id="{00000000-0000-0000-0000-000000000000}")


def _with_metadata(value: pb.TypedValue) -> pb.TypedValueWithMetadata:
    # The *WithMetadata messages are wire-compatible supersets of the plain
    # ones, so the builders in mfiles_grpc.values serve here too.
    result = pb.TypedValueWithMetadata.FromString(value.SerializeToString())
    result.value_metadata.CopyFrom(_NO_VALUE_METADATA)
    return result


def create_object(client: Client, object_type: int,
                  values: Mapping[int, pb.TypedValue]) -> pb.ObjectVersionEx:
    """
    Create a new object and check it in.

    :param values: Property definition ID -> value. Include the class (100) and
                   anything required, e.g. Name or title (0) and Single file (22).
    :return: The created version; its object_info.obj_id names the new object
    """
    properties = [pb.PropertyValueWithMetadata(property_def=pd, value=_with_metadata(v))
                  for pd, v in values.items()]
    response = client.objects.CreateNewObjectWithPropertyMetadata(
        pb.CreateNewObjectWithPropertyMetadataRequest(
            object_type_id=object_type, properties_with_metadata=properties, check_in=True))
    return response.created_object.object_version


def delete_object(client: Client, object_type: int, object_id: int) -> None:
    """Mark an object deleted. It can be undeleted; destroy_object() is final."""
    client.objects.RemoveObject(pb.RemoveObjectRequest(obj_id=obj_id(object_type, object_id)))


def destroy_object(client: Client, object_type: int, object_id: int) -> None:
    """Destroy an object and all its versions permanently. There is no undo."""
    client.objects.DestroyObject(pb.DestroyObjectRequest(
        obj_id=obj_id(object_type, object_id), all_versions=True,
        version=pb.ObjVerVersion(type=pb.OBJ_VER_VERSION_TYPE_ALL)))
