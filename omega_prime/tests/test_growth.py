"""Skills, memory, session search, and the curator."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.curator import review_turn
from omega_prime.agent.model import ScriptedModel
from omega_prime.agent.runtime import OmegaPrimeAgent
from omega_prime.memory.manager import MemoryManager
from omega_prime.memory.provider import (
    BuiltinMemoryProvider,
    MemoryProvider,
    is_trivial_prompt,
)
from omega_prime.memory.store import USER_CHAR_LIMIT, MemoryStore
from omega_prime.providers.fake import FakeTransport
from omega_prime.session.search import SessionStore, session_search
from omega_prime.skills_runtime.manager import skill_manage, skill_view
from omega_prime.substrate.client import SubstrateClient
from omega_prime.tooling.roster import roster_names
from omega_prime.tools.agent_message import (
    MESSAGING_TOOL_NAMES,
    register_messaging_tools,
)
from omega_prime.tools.autonomous import (
    AUTONOMOUS_TOOL_NAMES,
    register_autonomous_tools,
)
from omega_prime.tools.coding import CODING_TOOL_NAMES, register_coding_tools
from omega_prime.tools.cron_admin import (
    CRON_ADMIN_TOOL_NAMES,
    register_cron_admin_tools,
)
from omega_prime.tools.delegate import DELEG_TOOL_NAMES, register_delegate_tools
from omega_prime.tools.discord import (
    DISCORD_TOOL_NAMES,
    DiscordClient,
    register_discord_tools,
)
from omega_prime.tools.durable_surface import (
    DURABLE_TOOL_NAMES,
    register_durable_surface_tools,
)
from omega_prime.tools.goals import GOAL_TOOL_NAMES, register_goal_tools
from omega_prime.tools.growth import GROWTH_TOOL_NAMES, register_growth_tools
from omega_prime.tools.harness import HARNESS_TOOL_NAMES, register_harness_tools
from omega_prime.tools.heartbeat import HEARTBEAT_TOOL_NAMES, register_heartbeat_tools
from omega_prime.tools.ide import IDE_TOOL_NAMES, register_ide_tools
from omega_prime.tools.infra import (
    INFRA_TOOL_NAMES,
    InfraClient,
    InfraContext,
    register_infra_tools,
)
from omega_prime.tools.lead import (
    LEAD_TOOL_NAMES,
    LeadClient,
    LeadContext,
    register_lead_tools,
)
from omega_prime.tools.learning_surface import (
    LEARNING_TOOL_NAMES,
    register_learning_surface_tools,
)
from omega_prime.tools.mobile import (
    MOBILE_TOOL_NAMES,
    MobileClient,
    MobileContext,
    register_mobile_tools,
)
from omega_prime.tools.offer import offered_schemas
from omega_prime.tools.omega_command import (
    OMEGA_COMMAND_TOOL_NAMES,
    register_omega_command_tools,
)
from omega_prime.tools.packs import (
    PACKS_TOOL_NAMES,
    PacksClient,
    PacksContext,
    register_packs_tools,
)
from omega_prime.tools.platform import PLATFORM_TOOL_NAMES, register_platform_tools
from omega_prime.tools.prime_runtime import (
    KERNEL_TOOL_NAMES,
    register_prime_kernel_tools,
)
from omega_prime.tools.quality import (
    QUALITY_TOOL_NAMES,
    QualityClient,
    QualityContext,
    register_quality_tools,
)
from omega_prime.tools.redteam import (
    REDTEAM_TOOL_NAMES,
    RedteamClient,
    RedteamContext,
    register_redteam_tools,
)
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.rlm import RLM_TOOL_NAMES, register_rlm_tools
from omega_prime.tools.substrate_tools import (
    SUBSTRATE_TOOL_NAMES,
    register_substrate_tools,
)
from omega_prime.tools.systems import (
    SYS_TOOL_NAMES,
    SystemsClient,
    SystemsContext,
    register_systems_tools,
)
from omega_prime.tools.telegram import (
    TELEGRAM_TOOL_NAMES,
    TelegramClient,
    register_telegram_tools,
)
from omega_prime.tools.ultrathink import (
    ULT_TOOL_NAMES,
    UltrathinkClient,
    UltrathinkContext,
    register_ultrathink_tools,
)
from omega_prime.tools.webpack import (
    WEB_TOOL_NAMES,
    WebClient,
    WebContext,
    register_web_tools,
)
from omega_prime.tools.x import X_TOOL_NAMES, XClient, register_x_tools

ROOT = Path(__file__).resolve().parents[2]
ROSTER = ROOT / "omega_prime" / "contracts" / "tool-rosters" / "omega-prime.yaml"
EARNED_DESCRIPTION = "Use when the user asks to save this procedure."


def _load(raw: str) -> dict:
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _skill(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def _stored(name: str, content: str) -> str:
    """On-disk text after a plain body is wrapped for the loader.

    The body is kept as written, including indentation. Only the envelope
    tags are added.
    """
    front, body = content.split("---\n", 2)[1:]
    prefix = f"---\n{front}---\n"
    return prefix + f'\n<skill name="{name}" version="1">\n{body}\n</skill>\n'


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _tree(root: Path) -> list[tuple[str, bytes]]:
    if not root.exists():
        return []
    rows: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append(
                (relative, b"link:" + Path(path).readlink().as_posix().encode())
            )
        elif path.is_file():
            rows.append((relative, path.read_bytes()))
    return rows


def test_conversation_creates_a_skill_and_skill_view_reads_it_back(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    registry = ToolRegistry()
    register_growth_tools(
        registry,
        skills_root=skills,
        memory_dir=tmp_path / "memory",
        session_db=tmp_path / "sessions.db",
    )
    handler = registry._tools["skill_manage"].handler
    content = _skill("ship-check", "Use when shipping.", "run the checks\n")
    model = ScriptedModel(
        [
            _tool_call(
                "skill_manage",
                {"action": "create", "name": "ship-check", "content": content},
            ),
            {"role": "assistant", "content": "saved"},
        ]
    )
    agent = Agent(model=model, tools={"skill_manage": handler}, max_iterations=4)
    assert agent.tools is not None
    assert agent.tools["skill_manage"] is registry._tools["skill_manage"].handler

    result = run_conversation(agent, "please make a skill", system_message="sys")
    skill_md = skills / "ship-check" / "SKILL.md"
    stored = _stored("ship-check", content)
    assert skill_md.read_bytes() == stored.encode("utf-8")
    tool_row = next(
        message for message in result["messages"] if message.get("role") == "tool"
    )
    assert _load(tool_row["content"])["success"] is True

    viewed = _load(skill_view("ship-check", skills_root=skills))
    assert viewed["content"] == stored
    assert viewed["content"].encode("utf-8") == skill_md.read_bytes()
    again = _load(skill_view("ship-check", skills_root=skills))
    assert again["content"] == viewed["content"]
    via_dispatch = registry.dispatch("skill_view", {"name": "ship-check"})
    assert via_dispatch == skill_view("ship-check", skills_root=skills)

    script = (
        "import json\n"
        "from omega_prime.skills_runtime.manager import skill_view\n"
        f"print(skill_view('ship-check', skills_root={str(skills)!r}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert _load(completed.stdout)["content"] == stored
    missing = _load(skill_view("no-such-skill", skills_root=skills))
    assert "error" in missing
    assert "content" not in missing


def test_edit_replaces_and_patch_requires_one_exact_match(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    original = _skill("ship-check", "Use when shipping.", "alpha beta\n")
    created = _load(
        skill_manage("create", "ship-check", content=original, skills_root=skills)
    )
    assert created["success"] is True
    skill_md = skills / "ship-check" / "SKILL.md"
    assert skill_md.read_bytes() == _stored("ship-check", original).encode("utf-8")

    replacement = _skill("ship-check", "d" * 61, "replaced body\n")
    edited = _load(
        skill_manage("edit", "ship-check", content=replacement, skills_root=skills)
    )
    assert edited["success"] is True
    stored_replacement = _stored("ship-check", replacement)
    assert skill_md.read_bytes() == stored_replacement.encode("utf-8")

    patched = _load(
        skill_manage(
            "patch",
            "ship-check",
            old_string="replaced body",
            new_string="patched body",
            skills_root=skills,
        )
    )
    assert patched["success"] is True
    assert skill_md.read_bytes() == stored_replacement.replace(
        "replaced body", "patched body", 1
    ).encode("utf-8")

    before = skill_md.read_bytes()
    missed = _load(
        skill_manage(
            "patch",
            "ship-check",
            old_string="not-in-file",
            new_string="x",
            skills_root=skills,
        )
    )
    assert missed["success"] is False
    assert skill_md.read_bytes() == before
    twice = _load(
        skill_manage(
            "patch", "ship-check", old_string="d", new_string="e", skills_root=skills
        )
    )
    assert twice["success"] is False
    assert skill_md.read_bytes() == before

    categorized = _load(
        skill_manage(
            "create",
            "roll-out",
            content=_skill("roll-out", "Use when rolling out.", "ship it\n"),
            category="ops",
            skills_root=skills,
        )
    )
    assert categorized["success"] is True
    assert (skills / "ops" / "roll-out" / "SKILL.md").is_file()
    occupied = skills / "taken"
    occupied.mkdir()
    (occupied / "notes.txt").write_text("keep", encoding="utf-8")
    refused = _load(
        skill_manage(
            "create",
            "taken",
            content=_skill("taken", "Use when taken.", "body\n"),
            skills_root=skills,
        )
    )
    assert refused["success"] is False
    assert (occupied / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert not (occupied / "SKILL.md").exists()


def test_bad_name_frontmatter_and_long_description_write_nothing(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    valid = _skill("ok-name", "Use when ok.", "body\n")
    before = _tree(skills)

    escaped = _load(
        skill_manage("create", "../escape", content=valid, skills_root=skills)
    )
    assert escaped["success"] is False
    assert _tree(skills) == before
    assert not (tmp_path / "escape").exists()

    unclosed = "---\nname: ok-name\ndescription: Use when ok.\nbody without a closer\n"
    opened = _load(
        skill_manage("create", "ok-name", content=unclosed, skills_root=skills)
    )
    assert opened["success"] is False
    assert _tree(skills) == before

    long_description = _skill("ok-name", "x" * 61, "body\n")
    too_long = _load(
        skill_manage("create", "ok-name", content=long_description, skills_root=skills)
    )
    assert too_long["success"] is False
    assert _tree(skills) == before

    exact = _load(
        skill_manage(
            "create",
            "ok-name",
            content=_skill("ok-name", "y" * 60, "body\n"),
            skills_root=skills,
        )
    )
    assert exact["success"] is True
    assert (skills / "ok-name" / "SKILL.md").is_file()

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = skills / "alias"
    linked.symlink_to(outside, target_is_directory=True)
    before_outside = _tree(outside)
    before_skills = _tree(skills)
    leaked = _load(
        skill_manage(
            "create",
            "hidden",
            content=_skill("hidden", "Use when hidden.", "secret\n"),
            category="alias",
            skills_root=skills,
        )
    )
    assert leaked["success"] is False
    assert _tree(outside) == before_outside
    assert _tree(skills) == before_skills


def test_memory_reloads_from_disk_and_skips_a_trivial_prefetch(tmp_path: Path):
    trivial = (
        "hi",
        "HI!",
        "hey.",
        "hello",
        "yo",
        "sup~",
        "thanks :)",
        "done???",
        "ok",
        "yes.",
        "k",
        "",
        "   ",
        "/help",
        "lgtm",
        "thank you",
    )
    for text in trivial:
        assert is_trivial_prompt(text), text
    for text in ("k8s", "yolo", "note", "hello world", "what is the ship window"):
        assert not is_trivial_prompt(text), text

    directory = tmp_path / "mem"
    first = MemoryStore(directory)
    assert first.add("memory", "the ship window is tuesday")["success"] is True
    assert first.add("memory", "beta fact")["success"] is True
    assert first.add("memory", " the ship window is tuesday ")["message"].startswith(
        "Entry already exists"
    )
    assert first.memory_entries.count("the ship window is tuesday") == 1
    user = first.add("user", "u" * USER_CHAR_LIMIT)
    assert user["success"] is True

    second = MemoryStore(directory)
    second.load_from_disk()
    assert "the ship window is tuesday" in second.memory_entries
    assert "beta fact" in second.memory_entries
    manager = MemoryManager()
    manager.add_provider(BuiltinMemoryProvider(second))
    recalled = manager.prefetch_all("what is the ship window")
    assert "the ship window is tuesday" in recalled
    assert "beta fact" in recalled
    assert "u" * USER_CHAR_LIMIT in recalled
    assert manager.prefetch_all("thanks") == ""

    class Extra(MemoryProvider):
        @property
        def name(self) -> str:
            return "extra"

        def is_available(self) -> bool:
            return True

        def initialize(self, session_id: str, **kwargs) -> None:
            return None

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return "EXTRA BLOCK"

        def get_tool_schemas(self) -> list:
            return []

        def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
            return "{}"

    manager.add_provider(Extra())
    both = manager.prefetch_all("what is the ship window")
    assert "the ship window is tuesday" in both
    assert "EXTRA BLOCK" in both
    assert both.index("the ship window is tuesday") < both.index("EXTRA BLOCK")

    memory_file = directory / "MEMORY.md"
    before = memory_file.read_bytes()
    overflow = first.add("memory", "x" * 3000)
    assert overflow["success"] is False
    assert memory_file.read_bytes() == before
    user_file = directory / "USER.md"
    user_before = user_file.read_bytes()
    assert first.add("user", "z")["success"] is False
    assert user_file.read_bytes() == user_before

    fresh = MemoryStore(tmp_path / "empty-mem")
    assert fresh.add("memory", "y" * 2201)["success"] is False
    assert not (tmp_path / "empty-mem" / "MEMORY.md").exists()
    marked = first.add("memory", "has § inside")
    assert marked["success"] is False
    assert memory_file.read_bytes() == before

    paired = MemoryStore(tmp_path / "pairs")
    assert paired.add("memory", "cat")["success"] is True
    assert paired.add("memory", "the cat sat")["success"] is True
    pair_file = tmp_path / "pairs" / "MEMORY.md"
    pair_before = pair_file.read_bytes()
    ambiguous = MemoryStore(tmp_path / "ambiguous")
    ambiguous.add("memory", "alpha cat")
    ambiguous.add("memory", "beta cat")
    ambiguous_file = tmp_path / "ambiguous" / "MEMORY.md"
    ambiguous_before = ambiguous_file.read_bytes()
    assert ambiguous.replace("memory", "cat", "dog")["success"] is False
    assert ambiguous_file.read_bytes() == ambiguous_before
    assert paired.replace("memory", "cat", "dog")["success"] is True
    reloaded = MemoryStore(tmp_path / "pairs")
    reloaded.load_from_disk()
    assert reloaded.memory_entries == ["dog", "the cat sat"]
    assert pair_file.read_bytes() != pair_before
    assert paired.remove("memory", "the cat sat")["success"] is True
    assert MemoryStore(tmp_path / "pairs").memory_entries == ["dog"]

    added = _load(
        manager.handle_tool_call(
            "memory", {"action": "add", "content": "from the tool"}
        )
    )
    assert added["success"] is True
    assert "from the tool" in MemoryStore(directory).memory_entries


def test_session_search_finds_a_stored_message_and_rejects_an_empty_query(
    tmp_path: Path,
):
    path = tmp_path / "sessions.db"
    first = SessionStore(path)
    first.append("s1", "user", "deploy finished cleanly")
    first.append("s1", "assistant", "noted")
    found = _load(session_search("finished", store=SessionStore(path)))
    assert [message["content"] for message in found["messages"]] == [
        "deploy finished cleanly"
    ]
    assert all("finished" in message["content"] for message in found["messages"])
    absent = _load(session_search("no-such-token", store=SessionStore(path)))
    assert absent["messages"] == []
    assert "deploy finished cleanly" not in json.dumps(absent)
    empty = _load(session_search("", store=SessionStore(path)))
    assert "error" in empty
    assert "messages" not in empty
    assert "deploy finished cleanly" not in json.dumps(empty)
    percent = SessionStore(tmp_path / "percents.db")
    percent.append("s", "user", "100%")
    percent.append("s", "user", "plain")
    matched = _load(session_search("%", store=percent))
    assert [message["content"] for message in matched["messages"]] == ["100%"]


def test_curator_writes_a_skill_only_when_the_turn_earned_one(
    tmp_path: Path, monkeypatch
):
    skills = tmp_path / "skills"
    skills.mkdir()
    calls: list[tuple] = []
    real = skill_manage

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr("omega_prime.agent.curator.skill_manage", spy)
    output = {"success": True, "output": "exit 0"}

    untouched = _tree(skills)
    thanks = review_turn(skills_root=skills, user_text="thanks", tool_results=[output])
    assert thanks == {"created": False, "path": None}
    assert _tree(skills) == untouched
    quiet = review_turn(
        skills_root=skills,
        user_text="please deploy the service",
        tool_results=[output],
    )
    assert quiet["created"] is False
    assert _tree(skills) == untouched
    assert calls == []

    earned = review_turn(
        skills_root=skills,
        user_text="save as a skill name:deploy-check",
        tool_results=[{"success": False, "output": "nope"}, output],
    )
    assert earned["created"] is True
    skill_md = skills / "deploy-check" / "SKILL.md"
    assert Path(earned["path"]) == skill_md
    expected = (
        f"---\nname: deploy-check\ndescription: {EARNED_DESCRIPTION}\n---\n"
        '<skill name="deploy-check" version="1">\nexit 0\n</skill>\n'
    )
    assert skill_md.read_text(encoding="utf-8") == expected
    body = skill_md.read_text(encoding="utf-8").split("---", 2)[2]
    assert body == '\n<skill name="deploy-check" version="1">\nexit 0\n</skill>\n'
    assert calls and calls[-1][0][0] == "create"
    saved = _tree(skills)

    def boom(*_args, **_kwargs):
        raise AssertionError("skill_manage was called")

    monkeypatch.setattr("omega_prime.agent.curator.skill_manage", boom)
    missed = review_turn(
        skills_root=skills,
        user_text="save as a skill name:deploy-check",
        tool_results=[{"success": True, "output": ""}],
    )
    assert missed == {"created": False, "path": None}
    failed = review_turn(
        skills_root=skills,
        user_text="Save As A Skill name:other-check",
        tool_results=[{"success": False, "output": "exit 0"}],
    )
    assert failed["created"] is False
    empty_results = review_turn(
        skills_root=skills,
        user_text="save as a skill name:other-check",
        tool_results=[],
    )
    assert empty_results["created"] is False
    assert _tree(skills) == saved


def test_offered_schemas_include_growth_names_and_omit_an_extra_tool(tmp_path: Path):
    registry = ToolRegistry()
    register_coding_tools(registry, tmp_path)
    registered = register_growth_tools(
        registry,
        skills_root=tmp_path / "skills",
        memory_dir=tmp_path / "memory",
        session_db=tmp_path / "sessions.db",
    )
    assert registered == list(GROWTH_TOOL_NAMES)
    register_delegate_tools(registry, Agent(model=ScriptedModel([]), tools={}))
    register_omega_command_tools(registry, {})
    register_platform_tools(registry, home=tmp_path)
    register_ide_tools(registry, tmp_path)
    register_x_tools(registry, XClient(FakeTransport(), token="fake"))
    register_telegram_tools(
        registry, TelegramClient(make_bot=lambda token: None, token="fake")
    )
    register_discord_tools(
        registry, DiscordClient(make_client=lambda: None, token="fake")
    )
    register_lead_tools(registry, LeadClient(LeadContext(root=tmp_path)))
    register_systems_tools(registry, SystemsClient(SystemsContext(root=tmp_path)))
    register_web_tools(registry, WebClient(WebContext(root=tmp_path)))
    register_mobile_tools(registry, MobileClient(MobileContext(root=tmp_path)))
    register_infra_tools(registry, InfraClient(InfraContext()))
    register_quality_tools(registry, QualityClient(QualityContext(root=tmp_path)))
    register_packs_tools(registry, PacksClient(PacksContext()))
    register_ultrathink_tools(registry, UltrathinkClient(UltrathinkContext()))
    register_substrate_tools(registry, SubstrateClient())
    register_cron_admin_tools(registry, tmp_path)
    register_learning_surface_tools(registry, tmp_path)
    register_durable_surface_tools(registry, tmp_path)
    register_rlm_tools(
        registry,
        OmegaPrimeAgent(
            ScriptedModel([]), registry=ToolRegistry(), session_dir=tmp_path / "rlm"
        ),
        run_child=lambda prompt, model=None, thinking=None: "ok",
    )
    register_harness_tools(registry, tmp_path)
    register_goal_tools(registry, tmp_path)
    register_heartbeat_tools(registry, tmp_path)
    register_autonomous_tools(registry, tmp_path)
    register_messaging_tools(registry, "test-session")
    register_prime_kernel_tools(
        registry, tmp_path, run_child=lambda prompt, model=None, thinking=None: "ok"
    )
    register_redteam_tools(registry, RedteamClient(RedteamContext()))
    registry.register(
        "not_on_roster",
        "Registered but not offered.",
        {"type": "object", "properties": {}, "required": []},
        lambda: {"ok": True},
    )
    roster = roster_names(ROSTER.read_text(encoding="utf-8"))
    assert roster == list(
        CODING_TOOL_NAMES
        + GROWTH_TOOL_NAMES
        + DELEG_TOOL_NAMES
        + OMEGA_COMMAND_TOOL_NAMES
        + PLATFORM_TOOL_NAMES
        + IDE_TOOL_NAMES
        + X_TOOL_NAMES
        + TELEGRAM_TOOL_NAMES
        + DISCORD_TOOL_NAMES
        + LEAD_TOOL_NAMES
        + SYS_TOOL_NAMES
        + WEB_TOOL_NAMES
        + MOBILE_TOOL_NAMES
        + INFRA_TOOL_NAMES
        + QUALITY_TOOL_NAMES
        + PACKS_TOOL_NAMES
        + ULT_TOOL_NAMES
        + SUBSTRATE_TOOL_NAMES
        + CRON_ADMIN_TOOL_NAMES
        + LEARNING_TOOL_NAMES
        + DURABLE_TOOL_NAMES
        + RLM_TOOL_NAMES
        + HARNESS_TOOL_NAMES
        + GOAL_TOOL_NAMES
        + HEARTBEAT_TOOL_NAMES
        + AUTONOMOUS_TOOL_NAMES
        + MESSAGING_TOOL_NAMES
        + KERNEL_TOOL_NAMES
        + REDTEAM_TOOL_NAMES
    )
    offered = offered_schemas(registry, roster)
    names = [item["function"]["name"] for item in offered]
    assert names == roster
    for growth_name in GROWTH_TOOL_NAMES:
        assert growth_name in names
    assert "not_on_roster" not in names
    assert _load(registry.dispatch("not_on_roster", {})) == {"ok": True}
    remembered = _load(
        registry.dispatch("memory", {"action": "add", "content": "via dispatch"})
    )
    assert remembered["success"] is True
    assert "via dispatch" in MemoryStore(tmp_path / "memory").memory_entries


def test_missing_writable_root_still_views_the_shipped_skill(tmp_path: Path) -> None:
    shipped = tmp_path / "shipped"
    (shipped / "desk-gateway").mkdir(parents=True)
    (shipped / "desk-gateway" / "SKILL.md").write_text(
        "shipped body\n", encoding="utf-8"
    )
    registry = ToolRegistry()
    register_growth_tools(
        registry,
        skills_root=tmp_path / "state" / "skills",
        skills_fallback=shipped,
        memory_dir=tmp_path / "memory",
        session_db=tmp_path / "sessions.db",
    )
    viewed = _load(registry.dispatch("skill_view", {"name": "desk-gateway"}))
    assert viewed["success"] is True
    assert "shipped body" in viewed["content"]
    absent = _load(registry.dispatch("skill_view", {"name": "not-shipped"}))
    assert absent["success"] is False
    assert absent["error"] == "Skill 'not-shipped' not found."


def test_create_refuses_a_name_that_exists_only_in_the_shipped_root(
    tmp_path: Path,
) -> None:
    shipped = tmp_path / "shipped"
    (shipped / "desk-gateway").mkdir(parents=True)
    (shipped / "desk-gateway" / "SKILL.md").write_text(
        "shipped body\n", encoding="utf-8"
    )
    writable = tmp_path / "state" / "skills"
    writable.mkdir(parents=True)
    registry = ToolRegistry()
    register_growth_tools(
        registry,
        skills_root=writable,
        skills_fallback=shipped,
        memory_dir=tmp_path / "memory",
        session_db=tmp_path / "sessions.db",
    )
    blocked = _load(
        registry.dispatch(
            "skill_manage",
            {
                "action": "create",
                "name": "desk-gateway",
                "content": _skill("desk-gateway", "Hide the original.", "new\n"),
            },
        )
    )
    assert blocked == {
        "success": False,
        "error": "A skill named 'desk-gateway' already exists.",
    }
    assert not (writable / "desk-gateway").exists()
    created = _load(
        registry.dispatch(
            "skill_manage",
            {
                "action": "create",
                "name": "deploy",
                "content": _skill("deploy", "Use when deploying.", "steps\n"),
            },
        )
    )
    assert created["success"] is True
    assert (writable / "deploy" / "SKILL.md").is_file()
    still = _load(registry.dispatch("skill_view", {"name": "desk-gateway"}))
    assert still["content"] == "shipped body\n"
