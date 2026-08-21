"""Streams one agent-graph run as newline-delimited JSON events for the web UI.

Deliberately isolated from the core agent modules (main_agent.py,
architect_agent.py, solver.py, ...): this file only *imports* and *observes*
them (via stdout capture), it never edits their behavior. That keeps AI/backend
work on those files free to happen in parallel without touching the web service.
"""

import contextlib
import json
import queue
import re
import sqlite3
import threading
import time

from langchain_core.globals import set_debug

from catalog import DB_NAME
from main_agent import app as agent_graph, initial_graph_state, to_text
from scryfall_download import download_and_process_scryfall
from tools import set_deck_owned_only
from vectorize_cards import COLLECTION, generate_embeddings

# Makes LangChain print each tool call / LLM step to stdout as it happens, which
# _QueueWriter below turns into "log" events -- this is what makes the run
# transparent instead of just showing a "thinking" spinner between the four
# graph-node updates (which can each take many seconds on their own).
set_debug(True)

_LINE_BREAK_RE = re.compile(r"[\r\n]")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SENTINEL = object()


def _put(q: "queue.Queue", event: dict) -> None:
    event["ts"] = time.time()
    q.put(event)


class _QueueWriter:
    """A stdout-like object that turns each printed line into a queued log event.

    Three things keep this readable instead of a wall of text:
    - Splits on \\r too, not just \\n, so tqdm-style progress bars (e.g. the
      embedding step below) show up as a live-updating stream of lines instead
      of one giant buffered line dumped at the end.
    - Swallows pretty-printed JSON blocks (LangChain debug mode dumps the full
      prompt/response for every LLM and chain call). A block starts on a line
      that's exactly "{" or "[" and ends on an unindented "}" / "]" -- the
      header line right before it (e.g. "[llm/end] ... [15.16s] Exiting LLM
      run...") is what actually matters for "is this stuck", so that's kept.
    - Except: a block following an "[llm/end]" header is parsed instead of
      dropped, to pull out just the tool call(s) the model made (name + exact
      args) as a compact "-> search_cards({...})" line. That's the one part of
      the raw dump worth keeping -- it's what answers "did the agent actually
      pass owned_only, or did it omit it and fall back to the tool's default".
    - A "[.../error]" header (LLM/chain/tool failure) is followed not by a
      bracketed block but by one single, often huge, line: repr(exception)
      turns the traceback's real newlines into literal backslash-n text, so
      the brace-based swallow never triggers on it. That one line is dropped
      outright -- the header already says what failed, and the concise
      message ends up in the "error" event anyway once the exception
      propagates out of the stream loop.
    """

    def __init__(self, q: "queue.Queue"):
        self._q = q
        self._buffer = ""
        self._swallowing = False
        self._capturing: list[str] | None = None
        self._swallow_next_line = False
        self._last_header = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while True:
            match = _LINE_BREAK_RE.search(self._buffer)
            if not match:
                break
            raw_line, self._buffer = self._buffer[: match.start()], self._buffer[match.end() :]
            self._handle_line(_ANSI_RE.sub("", raw_line).rstrip())
        return len(text)

    def _handle_line(self, line: str) -> None:
        if self._swallow_next_line:
            self._swallow_next_line = False
            return
        if self._swallowing:
            if self._capturing is not None:
                self._capturing.append(line)
            if line in ("}", "]"):
                self._swallowing = False
                if self._capturing is not None:
                    self._emit_tool_calls("\n".join(self._capturing))
                    self._capturing = None
            return
        if line.strip() in ("{", "["):
            self._swallowing = True
            if "[llm/end]" in self._last_header:
                self._capturing = [line]
            return
        if line:
            _put(self._q, {"type": "log", "text": line})
            if "/error]" in line:
                self._swallow_next_line = True
        self._last_header = line

    def _emit_tool_calls(self, blob: str) -> None:
        try:
            data = json.loads(blob)
            message = data["generations"][0][0]["message"]["kwargs"]
        except Exception:
            return
        for call in message.get("tool_calls") or []:
            name = call.get("name", "?")
            args = json.dumps(call.get("args", {}), default=str)
            _put(self._q, {"type": "log", "text": f"→ {name}({args})"})

    def flush(self) -> None:
        pass

    def flush_remainder(self) -> None:
        line = _ANSI_RE.sub("", self._buffer).rstrip()
        self._buffer = ""
        if line and not self._swallowing:
            _put(self._q, {"type": "log", "text": line})


def _ensure_catalog() -> None:
    """Downloads the Scryfall oracle-card bulk data if the cards table is
    completely empty, instead of every search silently returning nothing.

    Calls scryfall_download.download_and_process_scryfall() (unmodified) --
    a public, unauthenticated, read-only endpoint -- and only when the table
    has zero rows, never to "refresh" prices on an already-populated catalog.
    """
    conn = sqlite3.connect(DB_NAME)
    try:
        has_cards = conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        has_cards = False  # table doesn't exist yet either
    finally:
        conn.close()
    if has_cards:
        return
    print("Card catalog is empty — downloading the Scryfall oracle-card bulk data now (one-time, ~24MB)...")
    download_and_process_scryfall()


def _ensure_search_index() -> None:
    """Build the Chroma card-search index on first use instead of failing.

    Calls vectorize_cards.generate_embeddings() (unmodified) rather than
    duplicating its logic; only runs when the collection is actually missing,
    so normal runs pay no extra cost.
    """
    from hybrid_search import get_chroma_client

    client = get_chroma_client()
    existing = {c.name for c in client.list_collections()}
    if COLLECTION in existing:
        return
    print(
        f"Search index '{COLLECTION}' not found — building it now from the card catalog "
        "(first run only, can take a few minutes)..."
    )
    generate_embeddings()


# Guards the two provisioning steps above so two concurrent runs (e.g. two
# browser tabs hitting Play before the catalog exists) don't both start a
# download / a full re-embed at once.
_provision_lock = threading.Lock()


def _ensure_data_ready() -> None:
    with _provision_lock:
        _ensure_catalog()
        _ensure_search_index()


def _run(query: str, deck: dict | None, q: "queue.Queue") -> None:
    writer = _QueueWriter(q)
    current_node: str | None = None
    try:
        with contextlib.redirect_stdout(writer):
            _ensure_data_ready()
            state = initial_graph_state(query, deck)
            # owned_only is constant for the whole run (agents don't mutate it),
            # so setting this once here covers every search_cards call the
            # Architect makes across every iteration of this run.
            set_deck_owned_only(state["deck"].get("owned_only", False))
            _put(q, {"type": "start", "deck": state["deck"]})
            # stream_mode=["updates", "debug"]: "debug" adds a "task" event the
            # instant a node starts (before it produces any output), so we know
            # which node -- and therefore which agent's LLM call -- is running
            # even if it never finishes (e.g. it hits a rate limit mid-call).
            for mode, payload in agent_graph.stream(state, stream_mode=["updates", "debug"]):
                if mode == "debug" and payload.get("type") == "task":
                    current_node = payload["payload"]["name"]
                    _put(q, {"type": "node_start", "node": current_node})
                    continue
                if mode != "updates":
                    continue
                for node_name, partial in payload.items():
                    event: dict = {"type": "node", "node": node_name}
                    messages = partial.get("messages") or []
                    if messages:
                        last = messages[-1]
                        event["agent"] = getattr(last, "name", node_name)
                        event["text"] = to_text(getattr(last, "content", None))
                    for key in ("deck", "validation", "supervisor_decision", "solver_report"):
                        if key in partial:
                            event[key] = partial[key]
                    _put(q, event)
        writer.flush_remainder()
    except Exception as exc:  # surface to the UI instead of a silent 500 mid-stream
        writer.flush_remainder()
        _put(q, {"type": "error", "message": str(exc), "node": current_node})
    else:
        _put(q, {"type": "done"})
    finally:
        q.put(_SENTINEL)


def stream_deck_run(query: str, deck: dict | None):
    """Yields NDJSON lines: log lines as they're printed, a "node" event each
    time a graph node finishes, then a closing "done"/"error" event. Every
    event carries a "ts" (unix seconds) so the UI can show elapsed time.

    Runs the (blocking, multi-LLM-call) graph on a worker thread so log lines
    can be forwarded to the caller as soon as they're printed, rather than only
    after each node fully completes.
    """
    q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(target=_run, args=(query, deck, q), daemon=True)
    thread.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield json.dumps(item, default=str) + "\n"
