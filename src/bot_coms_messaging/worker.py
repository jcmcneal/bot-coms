"""Retired entry point; messaging now executes inside the Hermes backend."""


def main():
    raise SystemExit('Standalone messaging workers are retired. Enable the messaging plugin in the Hermes dashboard backend.')


if __name__ == '__main__':
    main()
