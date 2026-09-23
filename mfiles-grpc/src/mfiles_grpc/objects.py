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
    :param version: A specific version, or None for the latest
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


def get_properties(client: Client, object_type: int, object_id: int,
                   version: Optional[int] = None) -> dict[int, pb.TypedValue]:
    """
    :return: Property definition ID -> value, for one version (default latest)
    """
    response = client.objects.GetProperties(
        pb.GetPropertiesRequest(obj_ver=obj_ver(object_type, object_id, version)))
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
        obj_ver=obj_ver(object_type, object_id, expected_version),
        allow_modifying_checked_in_object=True,
        fail_if_newer_version_exists=expected_version is not None,
        remove_unspecified_properties=False,
        properties=[pb.PropertyValue(property_def=pd, value=v) for pd, v in values.items()],
    )
    return client.objects.SetProperties(request)


def remove_properties(client: Client, object_type: int, object_id: int,
                      property_defs: list[int],
                      expected_version: Optional[int] = None) -> pb.SetPropertiesResponse:
    """Remove the named properties from an object; everything else stays."""
    request = pb.SetPropertiesRequest(
        obj_ver=obj_ver(object_type, object_id, expected_version),
        allow_modifying_checked_in_object=True,
        fail_if_newer_version_exists=expected_version is not None,
        remove_unspecified_properties=False,
        remove=property_defs,
    )
    return client.objects.SetProperties(request)
