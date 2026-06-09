"""Test finetune fixture."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Test finetune fixture.")
    parser.add_argument("--config", default="configs/example.yaml")
    parser.parse_args()
    print("test mtl")


if __name__ == "__main__":
    main()
