"""
Entry point for the multimodal proxy gateway.

Starts uvicorn with host and port read from environment variables:
- HOST: listen address (default 0.0.0.0)
- PORT: listen port (default 8080)
"""

import os

from .app import app

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
