from unittest import mock

from mfiles_grpc import objects, values
from mfiles_grpc.proto import pb


def fake_client():
    client = mock.Mock()
    client.objects.SetProperties.return_value = pb.SetPropertiesResponse()
    return client


def sent_request(client) -> pb.SetPropertiesRequest:
    (request,), _ = client.objects.SetProperties.call_args
    return request


def test_obj_ver_latest_and_specific():
    assert objects.obj_ver(101, 214).version.type == pb.OBJ_VER_VERSION_TYPE_LATEST
    v = objects.obj_ver(101, 214, 3).version
    assert (v.type, v.internal_version) == (pb.OBJ_VER_VERSION_TYPE_SPECIFIC, 3)


def test_set_properties_never_removes_unspecified():
    client = fake_client()
    objects.set_properties(client, 101, 214, {1036: values.integer(370)})
    request = sent_request(client)
    assert request.remove_unspecified_properties is False
    assert request.allow_modifying_checked_in_object is True
    assert [(p.property_def, p.value.data.integer) for p in request.properties] == [(1036, 370)]
    assert request.obj_ver.obj_id.item_id.internal_id == 214


def test_set_properties_expected_version_guards_against_newer():
    client = fake_client()
    objects.set_properties(client, 101, 214, {1036: values.integer(370)}, expected_version=2)
    request = sent_request(client)
    assert request.fail_if_newer_version_exists is True
    assert request.obj_ver.version.internal_version == 2


def test_set_properties_without_expected_version_targets_latest():
    client = fake_client()
    objects.set_properties(client, 101, 214, {})
    request = sent_request(client)
    assert request.fail_if_newer_version_exists is False
    assert request.obj_ver.version.type == pb.OBJ_VER_VERSION_TYPE_LATEST


def test_remove_properties_removes_only_named():
    client = fake_client()
    objects.remove_properties(client, 101, 214, [1038])
    request = sent_request(client)
    assert list(request.remove) == [1038]
    assert request.remove_unspecified_properties is False
    assert not request.properties
