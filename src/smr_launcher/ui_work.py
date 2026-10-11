"""Thread-safe delivery and bounded download presentation; contains no Tk calls."""
from collections import deque
from queue import Empty
from threading import Condition, Lock


class Activities:
    """UI-owned activity tokens: overlapping operations settle independently."""
    def __init__(self):
        self._next = 0
        self._jobs = {}

    def begin(self, label, priority=0):
        self._next += 1
        self._jobs[self._next] = (priority, label, None, None)
        return self._next

    def update(self, token, label, completed=None, total=None):
        if token in self._jobs:
            priority = self._jobs[token][0]
            self._jobs[token] = (priority, label, completed, total)

    def end(self, token):
        self._jobs.pop(token, None)

    def display(self):
        if not self._jobs:
            return None
        token = max(self._jobs, key=lambda key: (self._jobs[key][0], key))
        _, label, completed, total = self._jobs[token]
        return label, completed, total, len(self._jobs)


class LatestValue:
    """A bounded, coalescing worker-to-UI progress slot; never calls Tk."""
    def __init__(self):
        self._lock = Lock()
        self._value = None

    def put(self, value):
        with self._lock:
            self._value = value

    def take(self):
        with self._lock:
            value, self._value = self._value, None
            return value


class Mailbox:
    """Close atomically with publication, disposing queued or late owned results."""
    def __init__(self):
        self._lock = Lock()
        self._items = deque()
        self._closed = False

    def put(self, value, dispose=None):
        with self._lock:
            if not self._closed:
                self._items.append((value, dispose))
                return
        if dispose:
            dispose(value)

    def get_nowait(self):
        with self._lock:
            if not self._items:
                raise Empty
            return self._items.popleft()[0]

    def close(self):
        with self._lock:
            self._closed = True
            items = tuple(self._items)
            self._items.clear()
        for value, dispose in items:
            if dispose:
                dispose(value)


class DownloadEvents:
    """Coalesce in-flight byte counts; preserve transitions with backpressure.

    Only one mutable downloading slot per map is retained. Discrete events have
    a fixed capacity too: producers wait for the UI rather than dropping logs.
    A terminal map needs an explicit begin() before retry progress is accepted.
    """
    def __init__(self, capacity=4096):
        self.capacity = capacity
        self._condition = Condition()
        self._items = deque()
        self._progress = {}
        self._terminal = set()
        self._closed = False

    def begin(self, records):
        with self._condition:
            for record in records:
                self._terminal.discard(record.name)

    def put(self, event):
        record, state, *_ = event
        with self._condition:
            if self._closed or (record.name in self._terminal):
                return
            slot = self._progress.get(record.name)
            if state == "Downloading" and slot is not None:
                slot[0] = event
                return
            while len(self._items) >= self.capacity and not self._closed:
                self._condition.wait()
            if self._closed:
                return
            self._progress.pop(record.name, None)
            slot = [event]
            self._items.append(slot)
            if state == "Downloading":
                self._progress[record.name] = slot
            elif state in ("Imported", "Failed", "Canceled"):
                self._terminal.add(record.name)

    def get_nowait(self):
        with self._condition:
            if not self._items:
                raise Empty
            slot = self._items.popleft()
            record = slot[0][0]
            if self._progress.get(record.name) is slot:
                del self._progress[record.name]
            self._condition.notify_all()
            return slot[0]

    def empty(self):
        with self._condition:
            return not self._items

    def qsize(self):
        with self._condition:
            return len(self._items)

    def close(self):
        with self._condition:
            self._closed = True
            self._items.clear()
            self._progress.clear()
            self._terminal.clear()
            self._condition.notify_all()
