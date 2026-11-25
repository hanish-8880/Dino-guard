"""Policy source templates used when no LLM is configured."""
from __future__ import annotations


def _header(name: str, text: str) -> str:
    return (f'"""Drafted from: {text!r}. Review before publishing."""\n'
            "import re\n\nfrom dinoguard import allow, deny, policy\n\n\n")


def path_template(name: str, text: str, target: str) -> str:
    return _header(name, text) + f'''TARGET = {target!r}


@policy(name={name!r}, description={("Keep the agent out of " + target)!r}, events=["PreToolUse"],
        category="custom")
def check(ctx):
    seg = "/" + TARGET.strip("/") + "/"
    if ctx.tool_name in ("Write", "Edit", "Read"):
        p = "/" + str(ctx.tool_input.get("file_path", "")).replace("\\\\", "/").lstrip("/")
        if seg in p + "/" or p.endswith("/" + TARGET.strip("/")):
            return deny(f"{{TARGET}} is off limits for the agent. Leave it unchanged and tell the user what you "
                        "would change there.")
    if ctx.tool_name == "Bash":
        cmd = str(ctx.tool_input.get("command", "")).replace("\\\\", "/")
        if re.search(r"(^|[\\s/'\\"])" + re.escape(TARGET.strip("/")) + r"(/|\\s|$|['\\"])", cmd):
            return deny(f"This command touches {{TARGET}}, which is off limits. Ask the user first.")
    return allow()
'''


def command_template(name: str, text: str, command: str) -> str:
    return _header(name, text) + f'''PATTERN = re.compile(r"(^|[;&|]\\s*|\\s)" + re.escape({command!r}) + r"\\b")


@policy(name={name!r}, description={("Block running " + command)!r}, events=["PreToolUse"], category="custom")
def check(ctx):
    if ctx.tool_name == "Bash" and PATTERN.search(" " + str(ctx.tool_input.get("command", ""))):
        return deny({("Running `" + command + "` is not allowed here. Explain what you need and ask the user.")!r})
    return allow()
'''


def regex_template(name: str, text: str, pattern: str) -> str:
    return _header(name, text) + f'''PATTERN = re.compile({pattern!r}, re.I)


@policy(name={name!r}, description={("Block actions matching " + pattern)!r}, events=["PreToolUse"], category="custom")
def check(ctx):
    inp = ctx.tool_input
    text = " ".join(str(inp.get(k, "")) for k in ("command", "file_path", "content", "new_string"))
    if PATTERN.search(text):
        return deny("This action matches a blocked pattern for this project. Ask the user how to proceed.")
    return allow()
'''
