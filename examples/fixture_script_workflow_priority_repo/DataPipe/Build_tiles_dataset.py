import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/example.yaml")
    parser.parse_args()


if __name__ == "__main__":
    main()
