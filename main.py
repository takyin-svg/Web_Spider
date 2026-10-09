import logging
import warnings
import builtins
from src.orchestrator import Orchestrator

# 1. 強制讓 print 即時輸出，防止 GitHub Actions 日誌卡頓
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    builtins.print(*args, **kwargs)

# 2. 徹底隱藏 Scrapling 與底層庫的無關警告
warnings.filterwarnings("ignore")
logging.getLogger('scrapling').setLevel(logging.ERROR)
logging.getLogger('httpx').setLevel(logging.ERROR)

if __name__ == "__main__":
    app = Orchestrator()
    app.run()
