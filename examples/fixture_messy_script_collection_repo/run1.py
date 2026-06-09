"""Quick one-off runner."""


def normalize_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def main() -> None:
    print(normalize_name("Example Name"))


if __name__ == "__main__":
    main()
