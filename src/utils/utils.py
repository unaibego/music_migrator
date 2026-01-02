from typing import  Optional


def prompt(msg: str, default: Optional[str] = None) -> str:
    sfx = f" [{default}]" if default else ""
    val = input(f"{msg}{sfx}: ").strip()
    if not val and default is not None:
        return default
    return val


def prompt_yn(msg: str, default_yes: bool = True) -> bool:
    d = "S" if default_yes else "n"
    ans = prompt(f"{msg} (s/n)", d).lower()
    return ans.startswith("s")
