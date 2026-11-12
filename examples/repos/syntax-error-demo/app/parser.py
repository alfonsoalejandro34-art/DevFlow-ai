def parse_pair(text: str) -> tuple[str, str]:
    key, value = text.split("=", 1)
    return key.strip(), value.strip(
