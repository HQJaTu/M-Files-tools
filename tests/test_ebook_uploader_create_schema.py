"""
Offline tests for ebook-uploader-create-schema.py.

FakeVault answers the gRPC calls the script (and mfiles_grpc.structure) makes from an
in-memory structure, and applies the admin calls to it, so a second run sees what the
first one created. That is what re-entrancy means here: run twice, the second changes nothing.
"""

import argparse
import importlib.util
import itertools
from pathlib import Path
from unittest import mock

import pytest

from mfiles_grpc import pb

_SCRIPT = Path(__file__).resolve().parent.parent / "ebook-uploader-create-schema.py"
_spec = importlib.util.spec_from_file_location("create_schema", _SCRIPT)
create_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create_schema)

DOCUMENT_ACL = pb.AccessControlList(checked_out_to_user=-7)
KEYWORDS_ACL = pb.AccessControlList(checked_out_to_user=-26)
CREATED = 20
ALL_NAMES = ["eBook", "Author", "Publisher", "eBook bundle"]


def options(dry_run=False):
    return argparse.Namespace(dry_run=dry_run, object_type="eBook", object_class="eBook",
                              author_type="Author", publisher_type="Publisher", bundle_type="eBook bundle")


class FakeVault:
    """Only the built-ins the script relies on, until something is added."""

    ADMIN_CALLS = ("AddObjectType", "AddPropertyDef", "AddObjectClass", "ModifyObjectClass")

    def __init__(self):
        self._ids = itertools.count(1000)
        self.object_types = [pb.ValueList(id=0, name_singular="Document", is_real_object_type=True,
                                          acl=DOCUMENT_ACL)]
        self.property_defs = [
            pb.PropertyDef(id=0, name="Name or title", data_type=pb.DATATYPE_TEXT),
            pb.PropertyDef(id=CREATED, name="Created", data_type=pb.DATATYPE_TIMESTAMP,
                           update_type=pb.UPDATE_TYPE_SET_BY_SERVER),
            pb.PropertyDef(id=26, name="Keywords", data_type=pb.DATATYPE_TEXT, acl=KEYWORDS_ACL),
        ]
        self.classes: dict[int, pb.DocClass] = {}
        self.associations: dict[int, list[pb.AssociatedPropertyDef]] = {}

        self.client = mock.Mock()
        c = self.client
        c.object_types.GetObjectTypes.side_effect = \
            lambda _: pb.GetObjectTypesResponse(object_types=self.object_types)
        c.property_defs.GetPropertyDefs.side_effect = \
            lambda _: pb.GetPropertyDefsResponse(property_defs=self.property_defs)
        c.property_defs.GetObjectClassesAndGroups.side_effect = self._classes_and_groups
        c.property_defs_admin.GetObjectClasses.side_effect = \
            lambda _: pb.GetObjectClassesResponse(values=self.classes.values())
        c.property_defs_admin.GetAssociatedPropertyDefs.side_effect = \
            lambda r: pb.GetAssociatedPropertyDefsResponse(values=self.associations[r.document_profile])
        c.object_types_admin.GetObjectTypeDef.side_effect = self._object_type_def
        c.property_defs_admin.GetPropertyDefAdmin.side_effect = self._property_def_admin
        c.object_types_admin.AddObjectType.side_effect = self._add_object_type
        c.property_defs_admin.AddPropertyDef.side_effect = self._add_property_def
        c.property_defs_admin.AddObjectClass.side_effect = self._add_object_class
        c.property_defs_admin.ModifyObjectClass.side_effect = self._modify_object_class

    # Reads

    def _classes_and_groups(self, _):
        classes = [pb.ObjectClass(base_info=d.value_list_item, object_type=d.object_type,
                                  associated_property_defs=self.associations[class_id])
                   for class_id, d in self.classes.items()]
        return pb.GetObjectClassesAndGroupsResponse(classes=classes)

    def _object_type_def(self, request):
        found = next(t for t in self.object_types if t.id == request.object_type)
        return pb.GetObjectTypeDefResponse(obj_type_result=pb.ValueListAdmin(object_type=found))

    def _property_def_admin(self, request):
        found = next(p for p in self.property_defs if p.id == request.property_def)
        return pb.GetPropertyDefAdminResponse(ppropdefadmin=pb.PropertyDefAdmin(property_def=found))

    # Writes

    def _add_object_type(self, request):
        # Like M-Files, generate the owner and the multi-select lookup property with the type.
        created = pb.ValueList()
        created.CopyFrom(request.obj_type.object_type)
        created.id = next(self._ids)
        name = created.name_singular
        owner = pb.PropertyDef(id=next(self._ids), name=f"Owner ({name})", data_type=pb.DATATYPE_LOOKUP,
                               is_based_on_value_list=True, value_list=created.id)
        lookup = pb.PropertyDef(id=next(self._ids), name=name, data_type=pb.DATATYPE_MULTI_SELECT_LOOKUP,
                                is_based_on_value_list=True, value_list=created.id)
        self.property_defs += [owner, lookup]
        created.owner_property_def, created.default_property_def = owner.id, lookup.id
        self.object_types.append(created)
        return pb.AddObjectTypeResponse(obj_type_result=pb.ValueListAdmin(object_type=created))

    def _add_property_def(self, request):
        created = pb.PropertyDef()
        created.CopyFrom(request.propdefadmin.property_def)
        created.id = next(self._ids)
        self.property_defs.append(created)
        return pb.AddPropertyDefResponse(new_property_definition=pb.PropertyDefAdmin(property_def=created))

    def add_class(self, name, object_type, associated=()):
        class_id = next(self._ids)
        doc_class = pb.DocClass(object_type=object_type, value_list_item=pb.ValueListItem(
            item_info=pb.ItemInfo(obj_id=pb.ObjID(type=1, item_id=pb.ItemID(internal_id=class_id)), name=name)))
        self.classes[class_id] = doc_class
        # Like M-Files, list the server-maintained properties alongside the chosen ones.
        self.associations[class_id] = list(associated) + [pb.AssociatedPropertyDef(property_def=CREATED,
                                                                                   is_required=True)]
        return class_id

    def _add_object_class(self, request):
        return pb.AddObjectClassResponse(id=self.add_class(request.doc_class.value_list_item.item_info.name,
                                                           request.doc_class.object_type,
                                                           request.property_defs))

    def _modify_object_class(self, request):
        class_id = request.doc_class.value_list_item.item_info.obj_id.item_id.internal_id
        server_set = [a for a in self.associations[class_id] if a.property_def == CREATED]
        if any(a.property_def == CREATED for a in request.property_defs):
            raise RuntimeError("The parameter is incorrect")
        self.associations[class_id] = list(request.property_defs) + server_set
        return pb.ModifyObjectClassResponse()

    # Helpers for assertions

    def admin_calls(self):
        admin = (self.client.object_types_admin, self.client.property_defs_admin)
        return sum(getattr(a, name).call_count for a in admin for name in self.ADMIN_CALLS if hasattr(a, name))

    def by_name(self, items, name, name_of):
        return next(i for i in items if name_of(i) == name)

    def object_type(self, name):
        return self.by_name(self.object_types, name, lambda t: t.name_singular)

    def property_id(self, name):
        return self.by_name(self.property_defs, name, lambda p: p.name).id

    def class_id(self, name):
        return next(i for i, d in self.classes.items() if d.value_list_item.item_info.name == name)


def build(vault, dry_run=False):
    return create_schema.build_schema(vault.client, options(dry_run))


def test_empty_vault_gets_the_whole_schema():
    vault = FakeVault()
    changes = build(vault)

    assert len(changes) == 4 + 5 + 4
    for name in ALL_NAMES:
        object_type = vault.object_type(name)
        assert object_type.is_real_object_type
        assert object_type.options.can_have_files == (name == "eBook")
        assert vault.classes[vault.class_id(name)].object_type == object_type.id
    for name, datatype in create_schema.EBOOK_PROPERTIES:
        assert vault.by_name(vault.property_defs, name, lambda p: p.name).data_type == datatype

    ebook = {a.property_def: a.is_required for a in vault.associations[vault.class_id("eBook")]}
    wanted = [vault.object_type(n).default_property_def for n in ("Author", "Publisher", "eBook bundle")] \
        + [vault.property_id(n) for n, _ in create_schema.EBOOK_PROPERTIES]
    assert ebook[0] is True
    assert all(ebook[pid] is False for pid in wanted)


def test_second_run_changes_nothing():
    vault = FakeVault()
    build(vault)
    calls = vault.admin_calls()

    assert build(vault) == []
    assert vault.admin_calls() == calls


def test_dry_run_reports_and_writes_nothing():
    vault = FakeVault()
    changes = build(vault, dry_run=True)

    assert len(changes) == 13
    assert vault.admin_calls() == 0
    assert [t.name_singular for t in vault.object_types] == ["Document"]


def test_dry_run_on_complete_vault_reports_nothing():
    vault = FakeVault()
    build(vault)
    assert build(vault, dry_run=True) == []


def test_new_definitions_copy_the_vault_permissions():
    vault = FakeVault()
    build(vault)
    assert vault.object_type("eBook").acl == DOCUMENT_ACL
    assert vault.by_name(vault.property_defs, "Source", lambda p: p.name).acl == KEYWORDS_ACL


def test_missing_associations_are_added_and_existing_kept():
    vault = FakeVault()
    build(vault)
    class_id = vault.class_id("eBook")
    source, isbn = vault.property_id("Source"), vault.property_id("ISBN")
    # Someone made ISBN required by hand and dropped Source from the class.
    vault.associations[class_id] = [
        pb.AssociatedPropertyDef(property_def=a.property_def,
                                 is_required=a.is_required or a.property_def == isbn)
        for a in vault.associations[class_id] if a.property_def != source]

    assert build(vault) == ["associate 1 properties with class 'eBook'"]

    request = vault.client.property_defs_admin.ModifyObjectClass.call_args.args[0]
    assert request.modify_associations is True
    sent = {a.property_def: a.is_required for a in request.property_defs}
    assert CREATED not in sent
    assert sent[isbn] is True
    assert sent[source] is False
    assert build(vault) == []


def test_dry_run_counts_associations_for_properties_not_yet_created():
    vault = FakeVault()
    build(vault)
    vault.property_defs = [p for p in vault.property_defs if p.name != "Page count"]

    assert build(vault, dry_run=True) == ["create property 'Page count' (DATATYPE_INTEGER)",
                                          "associate 1 properties with class 'eBook'"]
    assert vault.admin_calls() == 4 + 5 + 4


def test_value_list_by_the_name_is_a_conflict():
    vault = FakeVault()
    vault.object_types.append(pb.ValueList(id=50, name_singular="Author", is_real_object_type=False))
    with pytest.raises(create_schema.SchemaConflict, match="value list"):
        build(vault)
    assert vault.admin_calls() == 0


def test_property_with_other_data_type_is_a_conflict():
    vault = FakeVault()
    vault.property_defs.append(pb.PropertyDef(id=60, name="Page count", data_type=pb.DATATYPE_TEXT))
    with pytest.raises(create_schema.SchemaConflict, match="Page count"):
        build(vault)
    assert vault.admin_calls() == 0


def test_class_on_other_object_type_is_a_conflict():
    vault = FakeVault()
    build(vault)
    calls = vault.admin_calls()
    vault.classes[vault.class_id("Author")].object_type = vault.object_type("Publisher").id
    # Would otherwise be a change to make
    vault.property_defs = [p for p in vault.property_defs if p.name != "ISBN"]

    with pytest.raises(create_schema.SchemaConflict, match="Class 'Author'"):
        build(vault)
    assert vault.admin_calls() == calls


def test_class_by_the_name_of_a_missing_object_type_is_a_conflict():
    vault = FakeVault()
    vault.add_class("Publisher", object_type=0)
    with pytest.raises(create_schema.SchemaConflict, match="Class 'Publisher'"):
        build(vault)
    assert vault.admin_calls() == 0


def test_every_conflict_is_reported_and_nothing_written():
    vault = FakeVault()
    vault.object_types.append(pb.ValueList(id=50, name_singular="Author", is_real_object_type=False))
    vault.property_defs.append(pb.PropertyDef(id=60, name="Page count", data_type=pb.DATATYPE_TEXT))
    vault.property_defs.append(pb.PropertyDef(id=61, name="Source", data_type=pb.DATATYPE_TEXT))

    with pytest.raises(create_schema.SchemaConflict) as raised:
        build(vault)

    assert len(raised.value.conflicts) == 3
    assert vault.admin_calls() == 0


def test_dry_run_raises_conflicts_too():
    vault = FakeVault()
    vault.property_defs.append(pb.PropertyDef(id=60, name="Source", data_type=pb.DATATYPE_TEXT))
    with pytest.raises(create_schema.SchemaConflict, match="Source"):
        build(vault, dry_run=True)


def test_main_reports_conflict_with_exit_code_2(monkeypatch, caplog):
    vault = FakeVault()
    vault.property_defs.append(pb.PropertyDef(id=60, name="Source", data_type=pb.DATATYPE_TEXT))
    connect = mock.MagicMock()
    connect.return_value.__enter__.return_value = vault.client
    monkeypatch.setattr(create_schema.Client, "connect", connect)
    monkeypatch.setattr(create_schema, "load_settings", mock.Mock())
    monkeypatch.setattr("sys.argv", ["ebook-uploader-create-schema.py"])

    assert create_schema.main() == 2
    assert vault.admin_calls() == 0
    assert "Nothing was changed" in caplog.text
