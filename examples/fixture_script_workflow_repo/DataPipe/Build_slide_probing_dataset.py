"""Build probing dataset fixture."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare probing dataset.")
    parser.add_argument("--config", default="configs/example.yaml")
    parser.parse_args()
    print("probe dataset")


if __name__ == "__main__":
    main()
