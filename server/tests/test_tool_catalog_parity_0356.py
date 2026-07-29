"""Catalog parity and completeness regressions for group 0356."""

import pytest

from modules.flow_gate.services import remote_tool_service, tool_registry


LOCALES = ("ko", "ja", "en")
LOCALIZED_TOOL_TABLES = (
    tool_registry.FIELDS,
    tool_registry.ERRORS,
    tool_registry.CAUTIONS,
)
COMMON_TABLES = (
    tool_registry.EXAMPLE_BODIES,
    tool_registry.EXAMPLE_RESPONSES,
)


def test_display_order_matches_executable_operations():
    assert set(tool_registry.DISPLAY_ORDER) == set(remote_tool_service.OPS)


def test_tool_classification_matches_operation_scopes():
    displayed = set(tool_registry.DISPLAY_ORDER)
    assert tool_registry.READ_TOOLS | tool_registry.WRITE_TOOLS == displayed
    assert tool_registry.READ_TOOLS.isdisjoint(tool_registry.WRITE_TOOLS)

    classifications = {
        "read": tool_registry.READ_TOOLS,
        "grep": tool_registry.READ_TOOLS,
        "write": tool_registry.WRITE_TOOLS,
        "remove": tool_registry.WRITE_TOOLS,
    }
    for name in displayed:
        assert name in classifications[remote_tool_service.OP_SCOPE[name]]


def test_every_tool_has_complete_localized_and_common_descriptions():
    for name in tool_registry.DISPLAY_ORDER:
        assert all(tool_registry.SUMMARY[locale].get(name) for locale in LOCALES)
        for table in LOCALIZED_TOOL_TABLES:
            assert name in table
            assert set(table[name]) >= set(LOCALES)
            assert all(table[name][locale] for locale in LOCALES)
        for table in COMMON_TABLES:
            assert name in table
            assert table[name]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_tool_detail_builds(locale):
    for name in tool_registry.DISPLAY_ORDER:
        detail = tool_registry.build_tool_detail(name, locale, "http://test/api/v1")
        assert detail["name"] == name
        assert detail["request_fields"]
        assert detail["errors"]
        assert detail["cautions"]