# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Convert between Python values and M-Files TypedValue / PropertyValue messages.
"""

import datetime
from dataclasses import dataclass
from typing import Iterable, Optional

from google.protobuf.timestamp_pb2 import Timestamp

from .proto import pb

# M-Files silently cuts single-line text properties to this length. Refuse
# instead, so a too-long value is an error here and not a surprise later.
TEXT_MAX_LENGTH = 100


@dataclass(frozen=True)
class LookupItem:
    id: int
    name: str = ""


def _timestamp(value: datetime.datetime | datetime.date) -> Timestamp:
    if not isinstance(value, datetime.datetime):
        value = datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc)
    elif value.tzinfo is None:
        raise ValueError("Naive datetime: give it a tzinfo so the stored instant is unambiguous")
    ts = Timestamp()
    ts.FromDatetime(value.astimezone(datetime.timezone.utc))
    return ts


def null(datatype: int) -> pb.TypedValue:
    """:return: An empty value of the given pb.DATATYPE_*"""
    return pb.TypedValue(type=datatype, is_null_value=True)


def text(value: str) -> pb.TypedValue:
    if len(value) > TEXT_MAX_LENGTH:
        raise ValueError(f"Text is {len(value)} characters; M-Files keeps only {TEXT_MAX_LENGTH}. "
                         "Use multiline_text() or shorten it.")
    return pb.TypedValue(type=pb.DATATYPE_TEXT, data=pb.TypedValueUnion(text=value))


def multiline_text(value: str) -> pb.TypedValue:
    # M-Files stores line breaks as CRLF whatever is sent; see normalise_newlines().
    return pb.TypedValue(type=pb.DATATYPE_MULTI_LINE_TEXT, data=pb.TypedValueUnion(multi_line_text=value))


def integer(value: int) -> pb.TypedValue:
    return pb.TypedValue(type=pb.DATATYPE_INTEGER, data=pb.TypedValueUnion(integer=value))


def real_number(value: float) -> pb.TypedValue:
    return pb.TypedValue(type=pb.DATATYPE_REAL_NUMBER, data=pb.TypedValueUnion(real_number=value))


def boolean(value: bool) -> pb.TypedValue:
    return pb.TypedValue(type=pb.DATATYPE_BOOLEAN, data=pb.TypedValueUnion(boolean=value))


def date(value: datetime.date) -> pb.TypedValue:
    return pb.TypedValue(type=pb.DATATYPE_DATE, data=pb.TypedValueUnion(date=_timestamp(value)))


def timestamp(value: datetime.datetime) -> pb.TypedValue:
    return pb.TypedValue(type=pb.DATATYPE_TIMESTAMP, data=pb.TypedValueUnion(timestamp=_timestamp(value)))


def _lookup(value_list: int, item_id: int) -> pb.Lookup:
    return pb.Lookup(
        value_list_item_info=pb.ItemInfo(
            obj_id=pb.ObjID(type=value_list, item_id=pb.ItemID(internal_id=item_id))),
        version=pb.ObjVerVersion(type=pb.OBJ_VER_VERSION_TYPE_LATEST))


def lookup(value_list: int, item_id: int) -> pb.TypedValue:
    """
    :param value_list: Value list (or object type) ID the property is based on
    :param item_id: Item or object ID in it
    """
    return pb.TypedValue(type=pb.DATATYPE_LOOKUP,
                         data=pb.TypedValueUnion(lookup=_lookup(value_list, item_id)))


def multi_select_lookup(value_list: int, item_ids: Iterable[int]) -> pb.TypedValue:
    lookups = [_lookup(value_list, item_id) for item_id in item_ids]
    return pb.TypedValue(type=pb.DATATYPE_MULTI_SELECT_LOOKUP,
                         data=pb.TypedValueUnion(multi_select_lookup=pb.MultiSelectLookup(values=lookups)))


def property_value(property_def: int, value: pb.TypedValue) -> pb.PropertyValue:
    return pb.PropertyValue(property_def=property_def, value=value)


def _lookup_item(lookup_value: pb.Lookup) -> LookupItem:
    info = lookup_value.value_list_item_info
    return LookupItem(id=info.obj_id.item_id.internal_id, name=info.name)


def to_python(value: pb.TypedValue):
    """
    :return: str, int, float, bool, datetime, LookupItem, list[LookupItem] or None
    """
    if value.is_null_value:
        return None
    kind = value.data.WhichOneof("data")
    if kind is None:
        return None
    raw = getattr(value.data, kind)
    if kind in ("date", "time", "timestamp", "filetime"):
        return raw.ToDatetime(tzinfo=datetime.timezone.utc)
    if kind == "lookup":
        return _lookup_item(raw)
    if kind == "multi_select_lookup":
        return [_lookup_item(item) for item in raw.values]
    if kind == "decimal_number":
        return raw
    if isinstance(raw, (str, int, float, bool)):
        return raw
    # ACL and other structured values: hand back the message itself.
    return raw


def normalise_newlines(value: Optional[str]) -> Optional[str]:
    """Compare multi-line text only after this: M-Files turns LF into CRLF."""
    return None if value is None else value.replace("\r\n", "\n")
