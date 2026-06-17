import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# Fix so that static/ relative paths are resolved relative to reward_tool_plus/
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("routers.main:app", host="0.0.0.0", port=8088, reload=True)
