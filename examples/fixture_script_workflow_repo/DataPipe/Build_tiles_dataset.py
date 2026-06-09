"""Build tiles dataset fixture."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare tile metadata.")
    parser.add_argument("--config", default="configs/example.yaml")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.dry_run:
        print(f"dry-run tiles with {args.config}")
        return
    print(f"tiles using {args.config}")


if __name__ == "__main__":
    main()
