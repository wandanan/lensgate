"""
Entry point for the multimodal proxy gateway.

Starts uvicorn with host and port from ProxyConfig (env / .env file).
Override via HOST / PORT environment variables.
"""

import os

from .app import app

if __name__ == "__main__":
    import uvicorn

    from backend.src.core.config import ProxyConfig

    config = ProxyConfig()
    host = os.environ.get("HOST", config.proxy_host)
    port = int(os.environ.get("PORT", str(config.proxy_port)))
    uvicorn.run(app, host=host, port=port)
