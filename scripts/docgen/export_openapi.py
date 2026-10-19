"""Write the API's OpenAPI schema to web/openapi.json (no server needed). Used by `npm run gen:api` in web/."""
import json
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ECHOS_API_NO_AUTOSTART"] = "1"
from echos.api import services as V  # noqa: E402
from echos.api.app import create_app  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402

if __name__ == "__main__":
    eng = S.get_engine(Path(tempfile.mkdtemp()) / "schema.db")
    out = Path(__file__).resolve().parents[2] / "web" / "openapi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(create_app(V.Ctx(eng=eng)).openapi(), indent=1))
    print("wrote", out)
