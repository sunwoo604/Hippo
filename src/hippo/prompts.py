"""Agent prompts. They ship inside the package and are read from there only.

Prompts are coupled to the code (commands they name, sandbox rules, file
formats), so there are no user-editable copies that could drift out of date.
"""

from importlib import resources


def template_text(name: str) -> str:
    return resources.files("hippo").joinpath("templates", name).read_text()


def render(name: str, **values: str) -> str:
    text = template_text(name)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
