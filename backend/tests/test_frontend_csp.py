"""The UI runs on Alpine's CSP build, so the CSP can forbid 'unsafe-eval'.

That build evaluates template expressions with its own small parser instead of `new Function`. It accepts
one expression per attribute, and rejects things standard Alpine allows. A template that uses them fails
only at runtime in the browser, so this test catches them first.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# What the CSP build cannot evaluate (alpinejs.dev/advanced/csp, plus its parser source for ';' and '?.').
UNSUPPORTED = {
    "statement sequence (;)": re.compile(r";"),
    "optional chaining (?.)": re.compile(r"\?\."),
    "arrow function": re.compile(r"=>"),
    "template literal": re.compile(r"`"),
    "spread": re.compile(r"\.\.\."),
    "new": re.compile(r"\bnew\s"),
    # Identifiers only (not `.x`); alert/confirm only as calls, since `alert` is also a loop variable here.
    "global": re.compile(
        r"(?<![\w$.])(window|document|console|Math|JSON|Date|parseInt|parseFloat|Number|Object|Array|String|"
        r"localStorage|sessionStorage|location|navigator|setTimeout)\b|(?<![\w$.])(alert|confirm)\s*\("
    ),
    "property assignment": re.compile(r"[\w$\]]\.[\w$.\[\]]+\s*(=(?![=>])|\+\+|--|\+=|-=)"),
}
STRING = re.compile(r"'(?:[^'\\]|\\.)*'")


class _AlpineAttributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name.startswith(("x-", "@", ":")):
                self.attrs.append((self.getpos()[0], name, value or ""))


def _alpine_attributes():
    parser = _AlpineAttributes()
    parser.feed((FRONTEND / "index.html").read_text())
    return parser.attrs


def test_templates_only_use_syntax_the_csp_build_supports():
    problems = []
    for line, name, value in _alpine_attributes():
        if name == "x-html":
            problems.append(f"line {line}: x-html is not allowed")
            continue
        code = STRING.sub("''", value)  # string contents may contain anything
        problems += [
            f"line {line}: {name}={value!r} uses {what}" for what, rx in UNSUPPORTED.items() if rx.search(code)
        ]
    assert not problems, "\n".join(problems)


def test_component_is_registered_not_inline():
    values = [value for _, name, value in _alpine_attributes() if name == "x-data"]
    assert values and all(re.fullmatch(r"\w+", v) for v in values), values  # Alpine.data('name', ...)


def test_page_loads_the_csp_build_and_policy_forbids_eval(client):
    page = client.get("/")
    script = re.search(
        r'src="(https://cdn\.jsdelivr\.net/npm/@alpinejs/csp@[\d.]+/)dist/cdn\.min\.js" integrity="sha384-', page.text
    )
    assert script and "npm/alpinejs@" not in page.text
    policy = page.headers["content-security-policy"]
    assert "unsafe-eval" not in policy and "unsafe-inline" not in policy.split("script-src", 1)[1].split(";", 1)[0]
    assert script.group(1) in policy  # the CSP allows exactly the library the page loads
