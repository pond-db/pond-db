"""Entry point: python -m mcp_server_ponddb"""

import argparse

from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="PondDB MCP Server")
    parser.add_argument(
        "--url",
        default="http://localhost:8432",
        help="PondDB server URL (default: http://localhost:8432)",
    )
    parser.add_argument("--api-key", required=True, help="PondDB API key")
    args = parser.parse_args()

    server = create_server(args.url, args.api_key)
    server.run()


if __name__ == "__main__":
    main()
