# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
The generated M-Files protobuf messages (``pb``) and service stubs (``rpc``).

Every message in the .proto is ``pb.<Name>``, every enum value is a module-level
constant on ``pb`` (``pb.DATATYPE_TEXT``), and every service ``IRPC<Name>`` has a
stub ``rpc.IRPC<Name>Stub``.
"""

from ._generated import mfilesCombinedWithDataPush_pb2 as pb  # noqa: F401
from ._generated import mfilesCombinedWithDataPush_pb2_grpc as rpc  # noqa: F401


def service_names() -> list[str]:
    """
    :return: Names of every gRPC service in the M-Files .proto, e.g. 'IRPCObjectOperations'
    """
    return sorted(pb.DESCRIPTOR.services_by_name)
