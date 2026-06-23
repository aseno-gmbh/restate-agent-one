"""Entry point: python -m app  or  python app/"""
import os
import sys


def main() -> None:
    from streamlit.web import cli as stcli

    ui_path = os.path.join(os.path.dirname(__file__), "ui.py")
    port = os.environ.get("UI_PORT", "8501")
    sys.argv = [
        "streamlit",
        "run",
        ui_path,
        f"--server.port={port}",
        "--server.address=0.0.0.0",
        "--server.headless=true",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
