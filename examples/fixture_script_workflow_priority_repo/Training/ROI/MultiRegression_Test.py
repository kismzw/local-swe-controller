import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.parse_args()


if __name__ == "__main__":
    main()
