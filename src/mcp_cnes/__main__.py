"""Entrypoint stdio oficial do pacote."""

from mcp_cnes.mcp_app import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
