"""Copy of a helper with an awkward filename."""


def normalize_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def helper_summary() -> str:
    return normalize_name("Helper Copy")


if __name__ == "__main__":
    print(helper_summary())
