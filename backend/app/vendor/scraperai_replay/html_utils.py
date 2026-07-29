"""XPath field-extraction helpers (vendored from ``scraperai/utils/html.py``).

Only the pure-``lxml`` extraction helpers used by the replay path are vendored;
the build-time helpers (``minify_html``, ``split_html``) are omitted so this
package does not depend on ``htmlmin``/``tiktoken``/``bs4``.  See ``__init__.py``
for provenance and licence notes.
"""
from typing import Any

from lxml import etree, html


def get_node_text(node) -> str:
    if isinstance(node, str):
        text = node
    else:
        text = etree.tostring(node, method="text", encoding='unicode')
    text = text.strip()
    return text


def extract_field_by_xpath(tree, xpath: str, multiple: bool | None = None) -> Any:
    nodes = tree.xpath(xpath)
    nodes = [get_node_text(node) for node in nodes]
    if len(nodes) == 0:
        return None
    if multiple is None:
        if len(nodes) == 1:
            return nodes[0]
        else:
            return nodes
    elif multiple:
        return nodes
    else:
        return nodes[0]


def extract_dynamic_fields_by_xpath(name_xpath: str,
                                    value_xpath: str,
                                    *,
                                    html_content: str = None,
                                    tree=None) -> dict[str, str]:
    if html_content:
        tree = html.fromstring(html_content)
    elif tree is None:
        raise ValueError('One of `html_content` or `tree` should not be None')
    labels = list(tree.xpath(name_xpath))
    values = list(tree.xpath(value_xpath))
    if len(labels) != len(values):
        raise ValueError(f'Labels and values are of different size ({len(labels)} != {len(values)}) '
                         f'for name_xpath={name_xpath} value_xpath={value_xpath}')
    return {get_node_text(key).strip(): get_node_text(value).strip() for key, value in zip(labels, values)}
