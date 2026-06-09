"""Train regression fixture."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a small regression fixture.")
    parser.add_argument("--config", default="configs/example.yaml")
    parser.parse_args()
    print("train regression")


if __name__ == "__main__":
    main()
