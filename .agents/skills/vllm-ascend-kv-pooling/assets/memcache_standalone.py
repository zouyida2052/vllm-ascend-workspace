import time

from memcache_hybrid import DistributedObjectStore


if __name__ == "__main__":
    store = DistributedObjectStore()
    result = store.init(0)
    if result != 0:
        raise SystemExit(f"Failed to initialize memcache, res = {result}")
    print("Successfully initialized memcache.", flush=True)
    while True:
        time.sleep(1)
