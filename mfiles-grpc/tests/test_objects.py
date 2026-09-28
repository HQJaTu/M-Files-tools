from unittest import mock

from mfiles_grpc import objects, values
from mfiles_grpc.proto import pb


def fake_client():
    client = mock.Mock()
    client.objects.SetProperties.return_value = pb.SetPropertiesResponse()
    client.objects.GetLatestObjectVersion.return_value = pb.GetLatestObjectVersionResponse(
        object_version=pb.ObjVerVersion(type=pb.OBJ_VER_VERSION_TYPE_SPECIFIC, internal_version=8))
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
    # The server refuses the LATEST marker, so the latest version is looked up.
    v = request.obj_ver.version
    assert (v.type, v.internal_version) == (pb.OBJ_VER_VERSION_TYPE_SPECIFIC, 8)


def test_get_properties_sends_a_specific_version():
    client = fake_client()
    client.objects.GetProperties.return_value = pb.GetPropertiesResponse()
    objects.get_properties(client, 101, 214)
    (request,), _ = client.objects.GetProperties.call_args
    assert request.obj_ver.version.internal_version == 8
    objects.get_properties(client, 101, 214, 3)
    (request,), _ = client.objects.GetProperties.call_args
    assert request.obj_ver.version.internal_version == 3


def test_remove_properties_removes_only_named():
    client = fake_client()
    objects.remove_properties(client, 101, 214, [1038])
    request = sent_request(client)
    assert list(request.remove) == [1038]
    assert request.remove_unspecified_properties is False
    assert not request.properties


def test_create_object_carries_values_and_checks_in():
    client = fake_client()
    client.objects.CreateNewObjectWithPropertyMetadata.return_value = \
        pb.CreateNewObjectWithPropertyMetadataResponse()
    objects.create_object(client, 101, {0: values.text("Test"), 100: values.lookup(1, 2)})
    (request,), _ = client.objects.CreateNewObjectWithPropertyMetadata.call_args
    assert request.object_type_id == 101 and request.check_in is True
    got = {p.property_def: p.value for p in request.properties_with_metadata}
    assert got[0].data.text == "Test"
    assert got[100].data.lookup.value_list_item_info.obj_id.item_id.internal_id == 2
    assert got[0].value_metadata.confidence == "-1"


def test_destroy_object_names_one_object_all_versions():
    client = fake_client()
    objects.destroy_object(client, 101, 9999)
    (request,), _ = client.objects.DestroyObject.call_args
    assert request.obj_id.item_id.internal_id == 9999 and request.all_versions is True
