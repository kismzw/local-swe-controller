import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    parser.parse_args()


if __name__ == "__main__":
    main()
