import contextlib
import json
import logging
import uuid
from collections.abc import Callable
from queue import Queue
from threading import Thread
from typing import Any

from agent.memory_provider import MemoryProvider, spawn_context_thread
from hermes_time import now as hermes_now
from hindsight_client import Hindsight
from hindsight_client_api.models import UpdateMemoryRequest
from tools.registry import tool_error, tool_result

from .config import CONFIG_SCHEMA, memento_load_config, memento_save_config
from .runtime import run_sync

logger = logging.getLogger(__name__)

_META_ATTRS = (
    "platform",
    "user_id",
    "user_name",
    "chat_id",
    "agent_identity",
)

_RECALL_TOOL_SCHEMA: dict[str, Any] = {
    "name": "memento_recall",
    "description": "Search the user's memories by meaning; returns facts ranked by relevance.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
        },
        "required": ["query"],
    },
}

_REFLECT_TOOL_SCHEMA: dict[str, Any] = {
    "name": "memento_reflect",
    "description": (
        "Synthesize a reasoned answer from long-term memory. "
        "Reasons across stored memories to produce a coherent response."
    ),
    "parameters": {
        "type": "object",
        "required": ["query"],
        "properties": {"query": {"type": "string", "description": "The question to reflect on."}},
    },
}

_LIST_TOOL_SCHEMA: dict[str, Any] = {
    "name": "memento_list",
    "description": (
        "List all memory units. Returns paginated results. Use this to browse or scan facts systematically (dreaming)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max items to return (default 100).",
                "minimum": 1,
                "maximum": 100,
            },
            "offset": {"type": "integer", "description": "Pagination offset.", "minimum": 0},
        },
    },
}

_PATCH_TOOL_SCHEMA: dict[str, Any] = {
    "name": "memento_patch",
    "description": (
        "Curate a single memory unit. Use to edit its content or to invalidate/restore it. Invalidation is reversible."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "ID of the memory to curate."},
            "text": {
                "type": "string",
                "description": "New fact text (optional)",
            },
            "state": {
                "type": "string",
                "enum": ["valid", "invalidated"],
                "description": "Set to 'invalidated' to soft-retire the fact. Set to 'valid' to restore.",
            },
            "reason": {
                "type": "string",
                "description": "Short reason for the change (recommended, especially for invalidation).",
            },
        },
        "required": ["memory_id"],
    },
}

_SYSTEM_PROMPT_BLOCK: str = """
# Persistent memory
You have a persistent memory system (Memento) that retains facts about the user across conversations.

Judge which tool fits the need:

- **memento_recall** - fast: semantic search, keyword matching, entity graph traversal, and reranking.
- **memento_reflect** - slow: reasons across stored memories to produce a coherent, synthesized answer. \
Use it ONLY when the question needs synthesis or judgment over history rather than lookup of specific facts.
Do not invent memories — if nothing relevant is returned, say so.

**For normal conversation:**
Before answering anything that depends on prior context (preferences, history, people, projects, \
routines, or earlier decisions), use **memento_recall** or **memento_reflect**. \
Do not invent memories — if nothing relevant is returned, say so.

**Do not use memento_list or memento_patch during ordinary conversation.**
These tools are reserved for the dreaming process (consolidation and cleanup) \
or when the user explicitly asks you to list, inspect, invalidate, or edit memories.

Memory is the source of truth for long-term user context. Check it before responding on user-specific topics.
"""

type ToolHandler = Callable[[dict[str, Any]], str]


def _event_timestamp() -> str:
    event_time = hermes_now()
    if event_time.tzinfo is None or event_time.utcoffset() is None:
        event_time = event_time.astimezone()
    return event_time.isoformat(timespec="seconds")


def _make_document_id(session_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, session_id))


def _sentinel() -> None:
    pass


class MementoProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "memento"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        config = memento_load_config()
        self._hindsight = Hindsight(base_url=config.api_url)
        self._session_id: str = session_id
        self._platform: str = kwargs.get("platform", "tui")
        self._user_id: str | None = kwargs.get("user_id")
        self._user_name: str | None = kwargs.get("user_name")
        self._chat_id: str | None = kwargs.get("chat_id")
        self._agent_identity: str | None = kwargs.get("agent_identity")
        if config.agent_id:
            self._agent_identity = config.agent_id
        self._document_id: str = _make_document_id(session_id)
        self._bank_id: str = config.bank_id
        self._recall_n: int = config.recall_n
        self._worker_queue: Queue[Callable[[], None]] = Queue()
        self._worker_thread: Thread | None = None
        self._tool_handlers: dict[str, tuple[ToolHandler, str]] = {
            "memento_recall": (self._tool_recall, "Failed to recall memories"),
            "memento_reflect": (self._tool_reflect, "Failed to reflect memories"),
            "memento_list": (self._tool_list, "Failed to list memories"),
            "memento_patch": (self._tool_patch, "Failed to patch memory"),
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        return CONFIG_SCHEMA

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        memento_save_config(values)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
        turn_author: dict[str, Any] | None = None,
    ) -> None:
        retain_kwargs = self._build_retain_kwargs(
            json.dumps(
                [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": assistant_content},
                ],
                ensure_ascii=False,
            )
        )

        def _retain_job() -> None:
            run_sync(self._hindsight.aretain(**retain_kwargs))

        self._enqueue(_retain_job)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            _RECALL_TOOL_SCHEMA,
            _REFLECT_TOOL_SCHEMA,
            _LIST_TOOL_SCHEMA,
            _PATCH_TOOL_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        if not tool_name in self._tool_handlers:
            return tool_error(f"Unknown tool: {tool_name}")
        handler, failure = self._tool_handlers[tool_name]
        try:
            return handler(args)
        except Exception as exc:
            logger.warning("%s failed: %s", tool_name, exc, exc_info=True)
            return tool_error(f"{failure}: {exc}")

    def system_prompt_block(self) -> str:
        return _SYSTEM_PROMPT_BLOCK

    def shutdown(self) -> None:
        if (writer := self._worker_thread) is not None and writer.is_alive():
            self._worker_queue.put(_sentinel)
            writer.join(timeout=10.0)
            if writer.is_alive():
                logger.warning(
                    "Worker did not stop within 10s; abandoning %d pending jobs(s)",
                    self._worker_queue.qsize(),
                )
        with contextlib.suppress(Exception):
            self._hindsight.close()

    def _build_retain_kwargs(self, content: str) -> dict[str, Any]:
        context = "conversation between Hermes Agent and the User"
        if self._agent_identity != "default":
            context = f"conversation between {self._agent_identity} (Agent) and the User"
        return {
            "bank_id": self._bank_id,
            "content": content,
            "timestamp": _event_timestamp(),
            "context": context,
            "document_id": self._document_id,
            "metadata": self._build_metadata(),
            "update_mode": "append",
            "retain_async": True,
        }

    def _build_metadata(self) -> dict[str, Any]:
        return {name: value for name in _META_ATTRS if (value := getattr(self, f"_{name}"))}

    def _enqueue(self, job: Callable[[], None]) -> None:
        self._ensure_worker()
        self._worker_queue.put(job)

    def _ensure_worker(self) -> None:
        if (thread := self._worker_thread) is not None and thread.is_alive():
            return
        thread = spawn_context_thread(self._worker_loop, name="memento-worker")
        self._worker_thread = thread
        thread.start()

    def _worker_loop(self) -> None:
        while True:
            job = self._worker_queue.get()
            try:
                if job is _sentinel:
                    return
                job()
            except Exception as exc:
                logger.warning("Job failed: %s", exc, exc_info=True)
            finally:
                self._worker_queue.task_done()

    def _tool_recall(self, args: dict[str, Any]) -> str:
        if "query" not in args:
            raise KeyError("recall: missing 'query'")
        query = str(args["query"]).strip()
        if not query:
            raise ValueError("recall: 'query' is empty")
        response = run_sync(
            self._hindsight.arecall(
                bank_id=self._bank_id,
                query=query,
                types=["observation"],
                min_scores={"final": 0.5},
            )
        )
        records = [r.text for r in response.results][: self._recall_n]
        return tool_result({"result": records})

    def _tool_reflect(self, args: dict[str, Any]) -> str:
        if "query" not in args:
            raise KeyError("reflect: missing 'query'")
        query = str(args["query"]).strip()
        if not query:
            raise ValueError("reflect: 'query' is empty")
        response = run_sync(
            self._hindsight.areflect(
                bank_id=self._bank_id,
                query=query,
                fact_types=["observation"],
                exclude_mental_models=True,
            )
        )
        return tool_result({"result": {"text": response.text}})

    def _tool_list(self, args: dict[str, Any]) -> str:
        response = run_sync(
            self._hindsight.alist_memories(
                bank_id=self._bank_id,
                type="world",
                state="valid",
                consolidation_state="done",
                limit=args.get("limit", 100),
                offset=args.get("offset", 0),
            )
        )
        items = [
            {
                "id": item.id,
                "text": item.text,
                "proof_count": item.proof_count,
                "mentioned_at": item.mentioned_at,
            }
            for item in response.items
        ]
        return tool_result({"result": {"items": items, "total": response.total}})

    def _tool_patch(self, args: dict[str, Any]) -> str:
        if "memory_id" not in args:
            raise KeyError("patch: missing 'memory_id'")
        memory_id = str(args["memory_id"]).strip()
        if not memory_id:
            raise ValueError("patch: 'memory_id' is empty")
        response = run_sync(
            self._hindsight.memory.update_memory(
                bank_id=self._bank_id,
                memory_id=memory_id,
                update_memory_request=UpdateMemoryRequest(
                    text=args.get("text"),
                    state=args.get("state"),
                    reason=args.get("reason"),
                ),
            )
        )
        logger.info("response: type: %s", (type(response), response))
        return tool_result({"result": response})
