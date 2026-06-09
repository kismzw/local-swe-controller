"""Temporary processing helper."""


def normalize_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def preview() -> None:
    print(normalize_name("Tmp Value"))


if __name__ == "__main__":
    preview()
