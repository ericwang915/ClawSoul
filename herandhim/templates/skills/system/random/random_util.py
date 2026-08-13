#!/usr/bin/env python3
"""Generate random numbers, UUIDs, passwords, or pick random items."""

import argparse
import random
import string
import uuid


def main():
    parser = argparse.ArgumentParser(description="Random value generator")
    parser.add_argument("--int", nargs=2, type=int, metavar=("MIN", "MAX"),
                        help="Random integer in [MIN, MAX]")
    parser.add_argument("--float", nargs=2, type=float, metavar=("MIN", "MAX"),
                        help="Random float in [MIN, MAX]")
    parser.add_argument("--uuid", action="store_true", help="Generate a UUID4")
    parser.add_argument("--password", type=int, metavar="LENGTH",
                        help="Generate a random password of given length")
    parser.add_argument("--choice", type=str,
                        help="Comma-separated list to pick from")
    parser.add_argument("--count", type=int, default=1,
                        help="Number of items to pick (for --choice)")
    args = parser.parse_args()

    if args.int:
        print(random.randint(args.int[0], args.int[1]))
    elif args.float:
        print(round(random.uniform(args.float[0], args.float[1]), 6))
    elif args.uuid:
        print(uuid.uuid4())
    elif args.password is not None:
        length = max(args.password, 4)
        chars = string.ascii_letters + string.digits + string.punctuation
        print("".join(random.choices(chars, k=length)))
    elif args.choice:
        items = [x.strip() for x in args.choice.split(",") if x.strip()]
        count = min(args.count, len(items))
        picks = random.sample(items, count)
        print(", ".join(picks))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
