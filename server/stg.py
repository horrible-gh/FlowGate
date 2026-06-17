import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# FLOWGATE_STORAGE_DIR is provided by server/.env (no hardcoded host path).
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Resolve static/ relative paths relative to server/
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

if __name__ == "__main__":
    import uvicorn
    # reload=True per staging request; import-string form required for reload.
    uvicorn.run("routers.main:app", host="0.0.0.0", port=8089, reload=True)