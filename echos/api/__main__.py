"""python -m echos.api : serve on 127.0.0.1 (never 0.0.0.0)."""
import uvicorn

from echos.config import load_config

if __name__ == "__main__":
    a = load_config().get("api", {})
    uvicorn.run("echos.api.app:app", host="127.0.0.1", port=int(a.get("port", 8000)), log_level="warning")
