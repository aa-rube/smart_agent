# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\file_utils.py
import os
import time


def safe_remove(path: str, retries: int = 5, delay: float = 0.2) -> bool:
    if not path:
        return True
    for _ in range(retries):
        try:
            os.remove(path)
            return True
        except PermissionError:
            time.sleep(delay)
        except FileNotFoundError:
            return True
        except Exception:
            break
    try:
        os.remove(path)
        return True
    except Exception:
        return False
