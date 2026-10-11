"""python -m echos.api : serve on 127.0.0.1 (never 0.0.0.0)."""
import os

import uvicorn

from echos.config import load_config

if __name__ == "__main__":
    a = load_config().get("api", {})
    uvicorn.run("echos.api.app:app", host="127.0.0.1", port=int(os.environ.get("ECHOS_API_PORT") or a.get("port", 8000)), log_level="warning")
