---
name: dreaming
description: "Offline consolidation and cleanup of memories."
version: 1.1.1
author: Daniel Wyatt
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [memory, dreaming, consolidation, memento]
    related_skills: []
---

# Memento Dreaming

Memory backend already consolidates related facts into observations. This skill supplies the missing pruning layer: it identifies obsolete, duplicate, or noisy **source facts** and either invalidates them or refines their text via patch.
Patch every fact in the same language as the input text. Never translate. Names, identifiers, code, and quoted text stay verbatim.
Hard-deletion is **not supported**.

**Mode:** Fully autonomous. No user confirmation. All decisions and mutations happen in one run.

---

## Phase 1: Recall

List current valid source memories. `memento_list(state="valid", limit=100, offset=0)` is paginated (default page size is 100).

Collect the full set by paging until a page returns fewer items than `limit` or you receive all `total` items.

If the final set is empty, stop and report “nothing to dream about.”

---

## Phase 2: Analyze & Decide

For each memory, decide:

- **Keep as-is** — already atomic, durable, and useful.
- **Edit** — same fact, but make it shorter / clearer / more atomic, or fold temporal history into one statement. Use `memento_patch` with a new `text`.
- **Invalidate** — pure noise, exact/near-duplicate, or clearly superseded by a later fact about the same subject. Use `memento_patch` with `state="invalidated"`.
- **If unsure → Keep** (lightly edit only if the wording is clearly improvable).

### Decision criteria

- **Atomicity** — exactly one fact / preference / constraint / observation.
- **Durability** — still useful in 30+ days.
- **Specificity** — precise and actionable.
- **Supersession** — a later fact makes an earlier one obsolete (prefer invalidating the older one).
- **Redundancy** — near-duplicate of something better (prefer invalidating the weaker one).

### Good atomic examples (after edit)

- “Prefers async communication and short updates.”
- “Allergic to shellfish.”
- “Has a golden retriever named Biscuit.”
- “Works best in the mornings.”

### 500-facts cap
If `total` facts > 500 after the normal hygiene pass of if memory bank is already over the limit:

1. Rank remaining facts by value (recency, durability, specificity, uniqueness, evidence sthrength).
2. Keep the top ***500*.
3. Invalidate the rest (lowest-ranked first), with reason: "bank size cap (500)".

Prefer invalidating older, less specific, or redundant facts over recent high-value ones. 

---

## Phase 3: Apply Changes

### Edit (preferred when meaning is preserved)

```
memento_patch(
  memory_id="<existing-id>",
  text="<new concise atomic text>",
  reason="made more atomic / clarified"
)
```

### Invalidate (soft-retire)

```
memento_patch(
  memory_id="<id-to-retire>",
  state="invalidated",
  reason="superseded by later fact"   # or "duplicate", "noise", etc.
)
```

You may combine an edit and other field updates (dates, entities) in one patch if needed.  
Do **not** attempt to hard-delete. Invalidation removes the fact from recall and consolidation while keeping an audit trail.

After a batch of patches on related facts, the backend will automatically re-consolidate the remaining evidence into observations.

---

## Phase 4: Log

Write a short dream log `$HERMES_HOME/dreams/YYYY-MM-DD-HHMM.md` with:

- Memories reviewed
- Edited (old → new + reason)
- Invalidated (id + reason)
- Kept unchanged

---

## Rules

- Never invent facts.
- Default to keeping when uncertain.
- Prefer edit over invalidate when the core fact is still useful.
- Prefer invalidate for clear supersessions, exact duplicates, or noise.
- Every surviving memory should be a concise, standalone fact.
- Run completely autonomously — no interactive prompts.
