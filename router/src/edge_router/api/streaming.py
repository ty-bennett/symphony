import json
import queue
import threading

from edge_router.schemas import ChatRequest
from edge_router.service.orchestrator import Orchestrator


def threaded_events(service: Orchestrator, request: ChatRequest):
    """Bounded queue, cancellation, and one owner thread for the model execution lock."""
    items: queue.Queue = queue.Queue(maxsize=16)
    cancelled = threading.Event()

    def put(item):
        while not cancelled.is_set():
            try:
                items.put(item, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def produce():
        iterator = service.stream(request)
        try:
            for event in iterator:
                if not put(event):
                    break
        except Exception:
            put("error")  # No exception text: tokenizers may echo prompt contents.
        finally:
            iterator.close()
            put(None)

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    try:
        while True:
            item = items.get()
            if item is None:
                break
            if item == "error":
                yield 'event: error\ndata: {"code":"LOCAL_EXECUTION_FAILED"}\n\n'
            else:
                yield f"event: {item.event}\ndata: {json.dumps(item.data)}\n\n"
    finally:
        cancelled.set()
