"""Streams one agent-graph run as newline-delimited JSON events for the web UI.

Deliberately isolated from the core agent modules (main_agent.py,
architect_agent.py, solver.py, ...): this file only *imports* and *observes*
them (via stdout capture), it never edits their behavior. That keeps AI/backend
work on those files free to happen in parallel without touching the web service.
"""

import contextlib
import json
import logging
import queue
import re
import sqlite3
import threading
import time
import uuid

from langchain_core.globals import set_debug

from catalog import DB_NAME, get_oracle_card
from deck_state import diff_decks
from contracts import RunEvent, SCHEMA_VERSION
from main_agent import app as agent_graph, initial_graph_state, to_text
from scryfall_download import download_and_process_scryfall
from tools import set_deck_card_pool, set_deck_owned_only
from vectorize_cards import COLLECTION, generate_embeddings

# Makes LangChain print each tool call / LLM step to stdout as it happens, which
# _QueueWriter below turns into "log" events -- this is what makes the run
# transparent instead of just showing a "thinking" spinner between the four
# graph-node updates (which can each take many seconds on their own).
set_debug(True)

# The debug tracer's ConsoleCallbackHandler assumes every tool call has a single
# string input (run.inputs["input"]); our multi-arg tools (search_cards(colors=,
# role=, query=), etc.) don't have that key, so it logs a swallowed KeyError via
# Python's logging module on every such call. Harmless -- the run itself isn't
# affected, and this never reaches the stdout stream _QueueWriter captures for the
# UI -- but it floods the raw server console, so drop it below warning level.
logging.getLogger("langchain_core.callbacks.manager").setLevel(logging.ERROR)
# One-time informational notice from the google-genai SDK itself (langchain_google_genai
# calls Models.generate_content directly instead of through Chat.send_message, which is
# their recommended AFC entrypoint) -- not something we control from this codebase.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)


class _PrintHandler(logging.Handler):
    """Emits via print() instead of a captured stream. A plain logging.StreamHandler
    binds to whatever sys.stdout *object* was current at construction time; print()
    looks up sys.stdout fresh on every call, so this keeps following
    contextlib.redirect_stdout(writer) inside _run() below and lands in the same
    event queue the UI reads from, instead of the real server console."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            print(self.format(record))
        except Exception:
            self.handleError(record)


# llm_factory.py sets max_retries=8 on the Gemini client specifically to survive
# the free tier's 429s. google-genai retries those (and 408/500/502/503/504) via
# tenacity with exponential backoff (1s doubling, capped at 60s -- up to ~2min of
# pure backoff across 8 attempts, before whatever each attempt's own request time
# adds on top). tenacity already logs "retrying in Xs" before every wait via
# before_sleep_log(logger, logging.INFO) -- that's this exact logger -- but
# nothing configured a handler for it, so a stalled run showed total silence
# between "[llm/start]" and the next line instead of the retries actually
# happening. INFO here (not the module's usual ERROR-only loggers above) is
# deliberate: this is the one signal that answers "is it retrying or is it stuck".
_retry_logger = logging.getLogger("google_genai._api_client")
_retry_logger.setLevel(logging.INFO)
_retry_logger.addHandler(_PrintHandler())

_LINE_BREAK_RE = re.compile(r"[\r\n]")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SENTINEL = object()

# Generic LangChain chain-wrapper start/end (chain:LangGraph, chain:agent,
# chain:call_model, chain:RunnableSequence, chain:Prompt, chain:tools, ...).
# Unlike [llm/*] and [tool/*], these never carry real content -- LangChain's
# debug tracer prints the literal placeholder "[inputs]" / "[outputs]" for
# them instead of the actual payload -- and the same underlying event repeats
# once per nesting level (5-7+ deep here), so one LLM/tool call produces a
# dozen-plus of these carrying zero unique information between them.
_CHAIN_WRAPPER_RE = re.compile(r"^\[chain/(start|end)\]")


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

    def __init__(self, q: "queue.Queue", emit=None):
        self._q = q
        self._emit_event = emit
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
        if _CHAIN_WRAPPER_RE.match(line):
            # Drop the header and the "[inputs]"/"[outputs]" placeholder
            # line that always follows it -- neither carries information.
            self._swallow_next_line = True
            return
        if line:
            if self._emit_event:
                self._emit_event("log", "log", "manager", {"text": line})
            else:
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
            if self._emit_event:
                self._emit_event(
                    "log",
                    "log",
                    "manager",
                    {"text": f"→ {name}({args})"},
                )
            else:
                _put(self._q, {"type": "log", "text": f"→ {name}({args})"})

    def flush(self) -> None:
        pass

    def flush_remainder(self) -> None:
        line = _ANSI_RE.sub("", self._buffer).rstrip()
        self._buffer = ""
        if line and not self._swallowing:
            if self._emit_event:
                self._emit_event("log", "log", "manager", {"text": line})
            else:
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
    duplicating its logic; only runs when the collection is missing *or*
    empty. A present-but-empty collection (partial run, dir wiped and
    recreated, etc.) used to pass the "does it exist" check and silently
    leave every search_cards call returning zero hits forever.
    """
    from hybrid_search import get_chroma_client

    client = get_chroma_client()
    existing = {c.name for c in client.list_collections()}
    if COLLECTION in existing:
        collection = client.get_collection(COLLECTION)
        if collection.count() > 0:
            return
        print(f"Search index '{COLLECTION}' exists but is empty — rebuilding it...")
    else:
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


# run_id -> "please stop" flag, so POST /api/deck/run/{run_id}/cancel (below) can
# reach a run's background thread without any other shared state between the two.
# Best-effort by construction: agent_graph.stream() only yields once per finished
# graph node (a whole Architect ReAct loop, not each LLM call inside it), so this
# flag is only checked -- see the loop below -- at node boundaries. It cannot abort
# a single LLM call already in flight (e.g. an Architect turn stuck retrying a 429).
# That's fine: the client aborts its fetch the instant Stop is clicked regardless,
# which is what actually makes the UI stop waiting; this registry only exists to
# also stop burning further LLM calls (Inventory/Solver/Supervisor) server-side
# after the user has walked away.
_cancel_flags: dict[str, threading.Event] = {}
_cancel_flags_lock = threading.Lock()


def cancel_deck_run(run_id: str) -> bool:
    with _cancel_flags_lock:
        flag = _cancel_flags.get(run_id)
    if flag is None:
        return False
    flag.set()
    return True


def _run(
    query: str,
    deck: dict | None,
    q: "queue.Queue",
    cancel_flag: threading.Event,
    run_id: str,
) -> None:
    current_node: str | None = None
    cancelled = False
    sequence = 0

    def emit(
        compatibility_type: str,
        event_type: str,
        node: str,
        payload: dict | None = None,
        state_revision: int = 0,
    ) -> None:
        nonlocal sequence
        sequence += 1
        event = RunEvent(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            sequence=sequence,
            node=node or "manager",
            state_revision=max(0, int(state_revision or 0)),
            event_type=event_type,
            payload=payload or {},
        ).model_dump()
        # Keep the existing UI's top-level event names while making the
        # canonical protocol available through event_type/payload.
        event["type"] = compatibility_type
        event["ts"] = time.time()
        if payload:
            event.update(payload)
        q.put(event)

    writer = _QueueWriter(q, emit=emit)
    try:
        with contextlib.redirect_stdout(writer):
            _ensure_data_ready()
            if cancel_flag.is_set():
                emit("cancelled", "cancelled", current_node or "manager")
                return
            state = initial_graph_state(query, deck)
            # Fail in milliseconds, not after an ~8-minute run: if a commander
            # is already set (autocomplete, a saved deck being edited) but
            # doesn't resolve in the catalog, don't let the graph start at
            # all. This does NOT cover a free-text request ("build me a deck
            # for Teval") where no commander is set yet -- there, the
            # Architect infers one itself, and a typo/hallucinated name can
            # still silently resolve to a *different real* commander with no
            # error raised anywhere; that's a separate, harder problem.
            commander_name = (state["deck"] or {}).get("commander")
            if commander_name and not get_oracle_card(commander_name):
                raise ValueError(
                    f"Commander '{commander_name}' was not found in the catalog."
                )
            # owned_only is constant for the whole run (agents don't mutate it),
            # so setting this once here covers every search_cards call the
            # Architect makes across every iteration of this run.
            set_deck_owned_only(state["deck"].get("owned_only", False))
            # Same lifetime as owned_only above: constant for the whole run, only
            # meaningful when pool_only is set (empty pool = no restriction).
            set_deck_card_pool(
                state["deck"].get("card_pool") if state["deck"].get("pool_only") else None
            )
            initial_deck = state["deck"]
            final_deck = initial_deck
            emit(
                "start",
                "run_started",
                "manager",
                {"deck": initial_deck},
                initial_deck.get("revision", 0),
            )
            # stream_mode=["updates", "debug"]: "debug" adds a "task" event the
            # instant a node starts (before it produces any output), so we know
            # which node -- and therefore which agent's LLM call -- is running
            # even if it never finishes (e.g. it hits a rate limit mid-call).
            for mode, payload in agent_graph.stream(state, stream_mode=["updates", "debug"]):
                if cancel_flag.is_set():
                    cancelled = True
                    break
                if mode == "debug" and payload.get("type") == "task":
                    current_node = payload["payload"]["name"]
                    emit("node_start", "node_started", current_node)
                    continue
                if mode != "updates":
                    continue
                for node_name, partial in payload.items():
                    event_payload: dict = {}
                    messages = partial.get("messages") or []
                    if messages:
                        last = messages[-1]
                        event_payload["agent"] = getattr(last, "name", node_name)
                        event_payload["text"] = to_text(getattr(last, "content", None))
                    for key in (
                        "deck",
                        "validation",
                        "gate_decision",
                        "manager_explanation",
                        "supervisor_decision",
                        "solver_report",
                    ):
                        if key in partial:
                            event_payload[key] = partial[key]
                    if "deck" in partial:
                        final_deck = partial["deck"]
                    emit(
                        "node",
                        "node_finished",
                        node_name,
                        event_payload,
                        (event_payload.get("deck") or {}).get("revision", 0),
                    )
        writer.flush_remainder()
    except Exception as exc:  # surface to the UI instead of a silent 500 mid-stream
        writer.flush_remainder()
        emit(
            "error",
            "error",
            current_node or "manager",
            {"message": str(exc)},
        )
    else:
        if cancelled:
            emit("cancelled", "cancelled", current_node or "manager")
        else:
            # Computed from the before/after card_list()s, not from any agent's
            # self-reported delta -- the architect/solver "swap" notes can drift
            # from what the deck state actually ended up with.
            emit(
                "done",
                "done",
                "manager",
                {"deck_diff": diff_decks(initial_deck, final_deck)},
                (final_deck or {}).get("revision", 0),
            )
    finally:
        q.put(_SENTINEL)


def stream_deck_run(query: str, deck: dict | None):
    """Yields NDJSON lines: a "run_id" line first (so the caller can POST
    /api/deck/run/{run_id}/cancel), then log lines as they're printed, a "node"
    event each time a graph node finishes, then a closing "done"/"cancelled"/
    "error" event. Every event carries a "ts" (unix seconds) so the UI can show
    elapsed time.

    Runs the (blocking, multi-LLM-call) graph on a worker thread so log lines
    can be forwarded to the caller as soon as they're printed, rather than only
    after each node fully completes.
    """
    run_id = uuid.uuid4().hex
    cancel_flag = threading.Event()
    with _cancel_flags_lock:
        _cancel_flags[run_id] = cancel_flag

    q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run,
        args=(query, deck, q, cancel_flag, run_id),
        daemon=True,
    )
    thread.start()

    try:
        yield (
            json.dumps(
                {
                    "type": "run_id",
                    "schema_version": SCHEMA_VERSION,
                    "event_type": "run_started",
                    "run_id": run_id,
                    "sequence": 0,
                    "node": "manager",
                    "state_revision": 0,
                    "payload": {},
                    "ts": time.time(),
                }
            )
            + "\n"
        )
        while True:
            item = q.get()
            if item is _SENTINEL:
                break
            yield json.dumps(item, default=str) + "\n"
    finally:
        # Doesn't fire reliably on a client-side abort (the worker thread may
        # still be blocked in q.get() with nothing consuming it), so a
        # cancelled-but-still-hung run can leave its flag here indefinitely --
        # a bounded, harmless leak for a single-process dev server, not worth
        # a reaper thread for.
        with _cancel_flags_lock:
            _cancel_flags.pop(run_id, None)
