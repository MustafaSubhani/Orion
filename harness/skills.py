"""Hermes-style procedural memory. Not typed F1–F8 healing and not RL.

Workers recover from tool errors in-loop (bounded). Successful recoveries become
SKILL.md files. Later requests inject matching skills. A curator merges overlap
and archives unused files so the prompt does not fill with stale procedures.
"""

from __future__ import annotations

import argparse
import atexit
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from harness.config import SKILLS_DIR, ensure_dirs
from harness.fsutil import atomic_write, file_lock, read_text_retry
ARCHIVE_DIR = SKILLS_DIR / "_archive"
_SLUG = re.compile(r"[^a-z0-9]+")
_BANNED = ("bench/", "MISTRAL_API_KEY", "eval(", "subprocess", "__import__", "../")
# Run-specific recipes that do not transfer are declared by the loaded domain packs
# (`stale_skill_patterns`). Core only bans generic hazards (see `_BANNED`).
_STALE: tuple[str, ...] = ()


@dataclass
class Skill:
    name: str
    description: str
    procedure: str
    agents: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    verbs: list[str] = field(default_factory=list)
    failure_class: str = ""
    hits: int = 0
    created_at: str = ""
    updated_at: str = ""
    scope: str = "global"  # global | user:<name> | agent:<id>

    def visible_to(self, user: Optional[str], agent_id: str = "") -> bool:
        """Who may be shown this skill. Unscoped (global) skills are visible to everyone."""
        if self.scope in {"", "global"}:
            return True
        kind, _, who = self.scope.partition(":")
        if kind == "user":
            return bool(user) and who == user
        if kind == "agent":
            return bool(agent_id) and who == agent_id
        return False

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def to_markdown(self) -> str:
        agents = ",".join(self.agents)
        entities = ",".join(self.entities)
        verbs = ",".join(self.verbs)
        return (
            "---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"agents: {agents}\n"
            f"entities: {entities}\n"
            f"verbs: {verbs}\n"
            f"failure_class: {self.failure_class}\n"
            f"scope: {self.scope}\n"
            f"hits: {self.hits}\n"
            f"created_at: {self.created_at}\n"
            f"updated_at: {self.updated_at}\n"
            "---\n\n"
            f"{self.procedure.strip()}\n"
        )

    def score(self, query: str, *, agent_id: str = "", entities: list[str] | None = None) -> float:
        q = set(re.findall(r"[a-z0-9]+", query.lower()))
        blob = set(
            re.findall(
                r"[a-z0-9]+",
                f"{self.name} {self.description} {self.procedure} {' '.join(self.verbs)}".lower(),
            )
        )
        if not q:
            return 0.0
        overlap = len(q & blob) / max(1, len(q))
        if agent_id and self.agents and agent_id not in self.agents:
            overlap *= 0.4
        if entities and self.entities and not (set(entities) & set(self.entities)):
            overlap *= 0.5
        return overlap


def slugify(name: str) -> str:
    slug = _SLUG.sub("-", (name or "").lower()).strip("-")
    return slug[:64] or "skill"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    raw = text.strip()
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip()] = val.strip()
    return meta, parts[2].strip()


def _split_csv(value: str) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def skill_from_markdown(text: str) -> Skill:
    meta, body = _parse_frontmatter(text)
    return Skill(
        name=meta.get("name") or "unnamed",
        description=meta.get("description") or "",
        procedure=body,
        agents=_split_csv(meta.get("agents") or ""),
        entities=_split_csv(meta.get("entities") or ""),
        verbs=_split_csv(meta.get("verbs") or ""),
        failure_class=meta.get("failure_class") or "",
        hits=int(meta.get("hits") or 0),
        created_at=meta.get("created_at") or "",
        updated_at=meta.get("updated_at") or "",
        scope=meta.get("scope") or "global",
    )


def skill_is_stale(text: str) -> Optional[str]:
    from harness.packs import registry

    blob = (text or "").lower()
    for needle in (*_STALE, *registry().stale_skill_patterns()):
        if needle in blob:
            return needle
    return None


def validate_skill_text(*parts: str) -> Optional[str]:
    blob = "\n".join(parts).lower()
    for needle in _BANNED:
        if needle.lower() in blob:
            return f"skill text contains banned token '{needle}'"
    stale = skill_is_stale(blob)
    if stale:
        return f"skill text is run-specific ('{stale}'); write a schema-level procedure instead"
    return None


class SkillStore:
    FLUSH_EVERY = 10
    FLUSH_SECONDS = 30.0

    def __init__(self, root: Path | None = None, *, embedder: Optional[Callable[[list[str]], list[list[float]]]] = None) -> None:
        self.root = root or SKILLS_DIR
        self.archive_root = self.root / "_archive"
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self._embedder = embedder  # None means the shared sentence-transformer, loaded lazily
        self._embedding_cache: dict[tuple[str, str], list[float]] = {}
        self._pending_hits: dict[str, int] = {}
        self._hits_lock = threading.Lock()
        self._flush_lock = threading.Lock()  # serialises read-modify-write of hit counts
        self._last_flush = time.monotonic()
        self._saves_since_curate = 0
        self._curating = threading.Lock()
        atexit.register(self.flush_hits)

    def path_for(self, name: str) -> Path:
        slug = slugify(name)
        if not slug or slug.startswith(".") or slug == "_archive" or "/" in slug or "\\" in slug:
            raise ValueError("invalid skill name")
        # `slugify` only emits [a-z0-9-], so the slug cannot traverse. The child is deliberately not
        # `.resolve()`d: on Windows a resolved missing path can differ from the resolved root (8.3 names).
        return self.root.resolve() / slug / "SKILL.md"

    def list(self, query: str = "") -> list[Skill]:
        out: list[Skill] = []
        q = query.lower().strip()
        if not self.root.exists():
            return out
        for skill_md in sorted(self.root.glob("*/SKILL.md")):
            if "_archive" in skill_md.parts:
                continue
            try:
                skill = skill_from_markdown(read_text_retry(skill_md))
            except OSError:
                continue
            blob = f"{skill.name} {skill.description}".lower()
            if q and q not in blob and q not in skill.slug:
                continue
            out.append(skill)
        return out

    def get(self, name: str) -> Optional[Skill]:
        path = self.path_for(name)
        if not path.exists():
            return None
        return skill_from_markdown(read_text_retry(path))

    def save(self, skill: Skill) -> Skill:
        err = validate_skill_text(skill.name, skill.description, skill.procedure)
        if err:
            raise ValueError(err)
        if not skill.created_at:
            skill.created_at = _now()
        skill.updated_at = _now()
        path = self.path_for(skill.name)
        with file_lock(path):
            atomic_write(path, skill.to_markdown())
        self._saves_since_curate += 1
        return skill

    def create(
        self,
        *,
        name: str,
        description: str,
        procedure: str,
        agents: list[str] | None = None,
        entities: list[str] | None = None,
        verbs: list[str] | None = None,
        failure_class: str = "",
        scope: str = "global",
    ) -> Skill:
        existing = self.get(name)
        if existing:
            existing.description = description or existing.description
            existing.procedure = procedure or existing.procedure
            if agents:
                existing.agents = agents
            if entities:
                existing.entities = entities
            if verbs:
                existing.verbs = verbs
            if failure_class:
                existing.failure_class = failure_class
            return self.save(existing)
        return self.save(
            Skill(
                name=slugify(name),
                description=description[:240],
                procedure=procedure[:4000],
                agents=list(agents or []),
                entities=list(entities or []),
                verbs=list(verbs or []),
                failure_class=failure_class,
                scope=scope,
            )
        )

    def touch(self, name: str) -> None:
        """Count a use in memory. Disk writes are batched by `flush_hits`, not done per selection."""
        with self._hits_lock:
            self._pending_hits[slugify(name)] = self._pending_hits.get(slugify(name), 0) + 1
            due = (
                sum(self._pending_hits.values()) >= self.FLUSH_EVERY
                or time.monotonic() - self._last_flush > self.FLUSH_SECONDS
            )
        if due:
            self.flush_hits()

    def flush_hits(self) -> int:
        """Persist pending hit counts. Returns how many skills were updated."""
        with self._flush_lock:
            with self._hits_lock:
                pending, self._pending_hits = self._pending_hits, {}
                self._last_flush = time.monotonic()
            updated = 0
            for slug, count in pending.items():
                try:
                    skill = self.get(slug)
                    if skill is None:
                        continue
                    skill.hits += count
                    self.save(skill)
                    updated += 1
                except (OSError, ValueError):
                    continue
            return updated

    def archive(self, name: str) -> bool:
        path = self.path_for(name)
        if not path.exists():
            return False
        dest_dir = self.archive_root / slugify(name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
        with file_lock(path):
            atomic_write(dest, path.read_text(encoding="utf-8"))
            path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is not None:
            return self._embedder(texts)
        from retrieval.embeddings import get_embeddings_batch

        return get_embeddings_batch(texts)

    def _semantic_scores(self, query: str, skills: list[Skill]) -> Optional[list[float]]:
        """Cosine similarity of the query to each skill, or None if embeddings are unavailable."""
        if not skills:
            return []
        try:
            missing = [s for s in skills if (s.slug, s.updated_at) not in self._embedding_cache]
            if missing:
                vectors = self._embed([f"{s.name}. {s.description}\n{s.procedure}"[:1500] for s in missing])
                for skill, vec in zip(missing, vectors):
                    self._embedding_cache[(skill.slug, skill.updated_at)] = list(vec)
            (qvec,) = self._embed([query[:1500]])
        except Exception:  # model not installed or failed to load: fall back to lexical ranking
            return None
        qnorm = sum(x * x for x in qvec) ** 0.5 or 1.0
        scores = []
        for skill in skills:
            vec = self._embedding_cache[(skill.slug, skill.updated_at)]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            scores.append(max(0.0, sum(a * b for a, b in zip(qvec, vec)) / (qnorm * norm)))
        return scores

    def select(
        self,
        query: str,
        *,
        agent_id: str = "",
        entities: list[str] | None = None,
        k: int = 2,
        min_score: float = 0.12,
        user: Optional[str] = None,
        semantic: bool = False,
    ) -> list[Skill]:
        """Top-k skills visible to `user`/`agent_id`.

        `semantic=True` ranks by embedding similarity (live chat); the default is lexical overlap so
        benchmark selection is deterministic. Selecting never writes to disk.
        """
        pool = [s for s in self.list() if s.visible_to(user, agent_id)]
        sem = self._semantic_scores(query, pool) if semantic else None
        floor = max(min_score, 0.30) if sem is not None else min_score
        scored = []
        for i, skill in enumerate(pool):
            base = sem[i] if sem is not None else skill.score(query, agent_id=agent_id, entities=entities)
            if sem is not None:
                if agent_id and skill.agents and agent_id not in skill.agents:
                    base *= 0.4
                if entities and skill.entities and not (set(entities) & set(skill.entities)):
                    base *= 0.5
            scored.append((base, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [s for score, s in scored if score >= floor][:k]
        for skill in picked:
            self.touch(skill.name)
        return picked

    def promote(self, name: str) -> bool:
        """Make a user-scoped skill visible to everyone (an administrator decision)."""
        skill = self.get(name)
        if skill is None:
            return False
        skill.scope = "global"
        self.save(skill)
        return True

    def maybe_curate(self, every: int) -> bool:
        """Run the curator in the background once `every` saves have happened. Never blocks the caller."""
        if self._saves_since_curate < max(1, every) or not self._curating.acquire(blocking=False):
            return False
        self._saves_since_curate = 0

        def run() -> None:
            try:
                self.curate()
            except (OSError, ValueError):
                pass
            finally:
                self._curating.release()

        threading.Thread(target=run, name="orion-skill-curator", daemon=True).start()
        return True

    def remember_recovery(
        self,
        *,
        verb: str,
        error: str,
        fix_preview: str,
        agent_id: str,
        entities: list[str],
        failure_class: str,
        user: Optional[str] = None,
        curate_every: int = 0,
    ) -> Optional[Skill]:
        """Extractive save after fail→success on the same verb. No extra LLM."""
        if failure_class in {"F6", "F7", "F6_SEMANTIC", "F7_POLICY"}:
            return None
        name = slugify(f"{agent_id}-{verb}-{failure_class or 'recover'}")
        description = f"After {verb} failed ({failure_class or 'tool error'}), use the corrected call."
        procedure = (
            f"Tool `{verb}` failed with:\n{error[:500]}\n\n"
            f"Working follow-up:\n{fix_preview[:1500]}\n\n"
            "Do not invent columns or entities. If the same error returns, abstain."
        )
        skill = self.create(
            name=name,
            description=description,
            procedure=procedure,
            agents=[agent_id] if agent_id else [],
            entities=list(entities or []),
            verbs=[verb] if verb else [],
            failure_class=failure_class,
            scope=f"user:{user}" if user else "global",  # live chat never writes a global skill
        )
        if curate_every:
            self.maybe_curate(curate_every)
        return skill

    def curate(self, *, merge_threshold: float = 0.72, max_active: int = 40) -> dict[str, Any]:
        """Drop run-specific recipes, merge overlap, archive extras past max_active."""
        stale_archived = 0
        for skill in list(self.list()):
            if skill_is_stale(f"{skill.name} {skill.description} {skill.procedure}"):
                self.archive(skill.name)
                stale_archived += 1
        skills = self.list()
        merged = 0
        archived = 0
        used: set[str] = set()
        for i, a in enumerate(skills):
            if a.slug in used:
                continue
            for b in skills[i + 1 :]:
                if b.slug in used:
                    continue
                if _jaccard(a.description + a.procedure, b.description + b.procedure) < merge_threshold:
                    continue
                keeper, drop = (a, b) if a.hits >= b.hits else (b, a)
                keeper.procedure = keeper.procedure.strip()
                if drop.name not in keeper.procedure:
                    keeper.procedure += f"\n\n(Merged from `{drop.name}`.)"
                keeper.hits += drop.hits
                self.save(keeper)
                self.archive(drop.name)
                used.add(drop.slug)
                merged += 1
        remaining = [s for s in self.list()]
        remaining.sort(key=lambda s: (s.hits, s.updated_at), reverse=True)
        for extra in remaining[max_active:]:
            self.archive(extra.name)
            archived += 1
        return {
            "active": len(self.list()),
            "merged": merged,
            "archived": archived,
            "stale_archived": stale_archived,
        }


def _jaccard(a: str, b: str) -> float:
    sa = set(re.findall(r"[a-z0-9]+", a.lower()))
    sb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def render_for_prompt(skills: list[Skill], *, max_chars: int = 700) -> str:
    blocks = []
    for skill in skills:
        body = skill.procedure.strip()
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "…"
        blocks.append(
            f"### skill `{skill.slug}`\n"
            f"When: {skill.description}\n"
            f"{body}"
        )
    return "\n\n".join(blocks)


def self_eval_note(*, n_calls: int, verbs: list[str], last_error: str, recoveries: int) -> str:
    recent = ", ".join(verbs[-8:]) or "(none)"
    err = last_error[:300] if last_error else "none"
    return (
        f"[self-eval after {n_calls} tool calls]\n"
        f"Recent verbs: {recent}\n"
        f"Last error: {err}\n"
        f"Fail→success recoveries this turn: {recoveries}\n"
        "If a corrected spec now works, call skill_manage op=create so later sessions skip this mistake. "
        "Do not retry policy denials. Do not invent columns."
    )


_STORES: dict[str, SkillStore] = {}
_STORES_LOCK = threading.Lock()


def default_store() -> SkillStore:
    """The shared store for the configured skills directory (one instance, so hit batching works)."""
    ensure_dirs()
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    key = str(SKILLS_DIR.resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = SkillStore(SKILLS_DIR)
        return _STORES[key]


def main() -> None:
    parser = argparse.ArgumentParser(description="Curate Orion procedural skills")
    parser.add_argument("--curate", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    store = default_store()
    if args.list:
        items = store.list()
        print(json.dumps([{"name": s.name, "hits": s.hits, "desc": s.description} for s in items], indent=2))
        return
    stats = store.curate()
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
