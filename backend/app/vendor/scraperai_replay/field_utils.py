"""Field-bundle extraction (vendored from ``scraperai/parsers/utils.py``).

The ``build_validation_error_message`` and ``extract_items`` helpers (only used
during config building) are omitted.  See ``__init__.py`` for provenance.
"""
from typing import Any

from lxml import html

from .html_utils import extract_dynamic_fields_by_xpath, extract_field_by_xpath
from .models import StaticField, WebpageFields


def _prepare_xpath(xpath: str, select_context_node: bool) -> str:
    if select_context_node and not xpath.startswith('.'):
        return '.' + xpath
    return xpath


def extract_static_fields(tree, static_fields: list[StaticField], select_context_node: bool = False) -> dict[str, Any]:
    return {
        field.field_name: extract_field_by_xpath(
            tree,
            xpath=_prepare_xpath(field.field_xpath, select_context_node),
            multiple=field.multiple
        ) for field in static_fields
    }


def extract_fields_from_tree(tree, fields: WebpageFields, select_context_node: bool = False) -> dict[str, Any]:
    data = extract_static_fields(tree, fields.static_fields, select_context_node)
    for field in fields.dynamic_fields:
        items = extract_dynamic_fields_by_xpath(
            _prepare_xpath(field.name_xpath, select_context_node),
            _prepare_xpath(field.value_xpath, select_context_node),
            tree=tree
        )
        data.update(items)
    return data


def extract_fields_from_html(html_content: str, fields: WebpageFields) -> dict[str, Any]:
    tree = html.fromstring(html_content)
    return extract_fields_from_tree(tree, fields)
