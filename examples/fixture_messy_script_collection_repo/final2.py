"""Final script version two."""


def normalize_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def emit_report() -> None:
    print(normalize_name("Final Report"))


if __name__ == "__main__":
    emit_report()
