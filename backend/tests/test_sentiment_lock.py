"""FinBERT's lazy load runs once, however many scheduler threads ask at once.

The 2-minute Reddit job and the 15-minute RSS job both fire at :00 and :30,
so after a deploy both can hit the first headline before the ~400MB model has
loaded.  No database or model download needed: `pipeline` is replaced with a
slow stand-in that counts how often it's built.
"""
import threading
import time

from app import sentiment


def test_concurrent_first_calls_build_one_pipeline(monkeypatch):
    built = []

    def slow_pipeline(*args, **kwargs):
        time.sleep(0.2)  # long enough for every thread to arrive mid-load
        built.append(object())
        return built[-1]

    monkeypatch.setattr(sentiment, "pipeline", slow_pipeline)
    monkeypatch.setattr(sentiment, "_sentiment_pipeline", None)

    threads_n = 8
    start = threading.Barrier(threads_n)
    results = []

    def first_call():
        start.wait()
        results.append(sentiment._get_pipeline())

    threads = [threading.Thread(target=first_call) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(built) == 1
    assert len(results) == threads_n
    assert all(r is built[0] for r in results)
