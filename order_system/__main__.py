"""Start the order system:  uv run python -m order_system

It listens on http://127.0.0.1:8010. To use another port:

    ARDEN_API_PORT=8020 uv run python -m order_system

and set ARDEN_API_URL=http://127.0.0.1:8020 for the programs that call it.
"""

import os

import uvicorn

PORT = int(os.environ.get("ARDEN_API_PORT", "8010"))

if __name__ == "__main__":
    print(f"Arden's order system is starting on http://127.0.0.1:{PORT}")
    print(f"Try it in a browser: http://127.0.0.1:{PORT}/docs    Stop it with Ctrl+C.")
    uvicorn.run("order_system.app:app", host="127.0.0.1", port=PORT, log_level="warning")
