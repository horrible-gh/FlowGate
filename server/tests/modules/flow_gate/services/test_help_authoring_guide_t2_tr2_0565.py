from modules.flow_gate.services import help_catalog

def _ctx(code, locale):
    return {"doc_type":code,"locale":locale,
            "action_scope":"new","base_url":"/flowgate/api/v1"}

def test_guide_children_exist_in_all_locales():
    for code in ("T2","TR2"):
        for locale in ("ko","en","ja"):
            children=help_catalog.enumerate_children("authoring_guide",_ctx(code,locale))
            assert len(children)==1
            assert children[0]["name"]==code
            assert children[0]["title"]
            assert children[0]["url"].endswith("/"+code)

def test_child_guides_are_type_specific_and_contain_no_local_path():
    tr_body=help_catalog.build_child("authoring_guide","TR",_ctx("TR","en"))["content"]["body"]
    for code in ("T2","TR2"):
        for locale in ("ko","en","ja"):
            item=help_catalog.build_child("authoring_guide",code,_ctx(code,locale))
            body=item["content"]["body"]
            assert item["content"]["title"]
            assert item["content"]["type_code"]==code
            assert body != tr_body
            assert "C:\\" not in body
            assert "/workspace/" not in body
            assert "/scratch/" not in body
