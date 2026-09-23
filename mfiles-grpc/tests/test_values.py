import datetime

import pytest

from mfiles_grpc import values
from mfiles_grpc.proto import pb


def roundtrip(tv):
    # Through the wire format, as the server would see it.
    return values.to_python(pb.TypedValue.FromString(tv.SerializeToString()))


def test_scalars_roundtrip():
    assert roundtrip(values.text("ISBN 0-471-23712-4")) == "ISBN 0-471-23712-4"
    assert roundtrip(values.integer(2002)) == 2002
    assert roundtrip(values.boolean(True)) is True
    assert roundtrip(values.real_number(1.5)) == 1.5
    assert roundtrip(values.multiline_text("a\nb")) == "a\nb"


def test_datatypes_are_tagged():
    assert values.text("x").type == pb.DATATYPE_TEXT
    assert values.multiline_text("x").type == pb.DATATYPE_MULTI_LINE_TEXT
    assert values.integer(1).type == pb.DATATYPE_INTEGER
    assert values.multi_select_lookup(102, [1]).type == pb.DATATYPE_MULTI_SELECT_LOOKUP


def test_text_over_limit_is_refused_not_truncated():
    values.text("x" * values.TEXT_MAX_LENGTH)
    with pytest.raises(ValueError, match="multiline_text"):
        values.text("x" * (values.TEXT_MAX_LENGTH + 1))


def test_multiline_text_has_no_limit():
    assert len(roundtrip(values.multiline_text("x" * 5000))) == 5000


def test_timestamp_roundtrip_is_utc():
    when = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=3)))
    assert roundtrip(values.timestamp(when)) == when


def test_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="tzinfo"):
        values.timestamp(datetime.datetime(2026, 9, 23))


def test_date_roundtrip():
    assert roundtrip(values.date(datetime.date(2002, 10, 4))).date() == datetime.date(2002, 10, 4)


def test_lookups():
    tv = values.lookup(103, 11)
    assert tv.data.lookup.value_list_item_info.obj_id.type == 103
    assert roundtrip(tv) == values.LookupItem(id=11)
    assert roundtrip(values.multi_select_lookup(102, [5, 7])) == [values.LookupItem(5), values.LookupItem(7)]


def test_null():
    assert roundtrip(values.null(pb.DATATYPE_TEXT)) is None


def test_normalise_newlines():
    assert values.normalise_newlines("a\r\nb") == "a\nb"
    assert values.normalise_newlines(None) is None
