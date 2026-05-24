import tomllib
from pathlib import Path

SEED_ROOT = Path("codex") / "automations"
CODEX_CONFIG_PATH = Path("codex") / "config.toml"


EXPECTED_TEMPLATE_IDS = {
    "studyos-coordinator-thread",
    "studyos-github-triage",
    "studyos-group-channel-digest",
    "studyos-implementation-candidates",
    "studyos-issue-refinement",
    "studyos-pr-review-nudge",
    "studyos-weekly-digest",
}


def _automation_paths() -> list[Path]:
    return sorted(SEED_ROOT.glob("*/automation.toml"))


def test_static_automations_exist_for_each_seed() -> None:
    assert {path.parent.name for path in _automation_paths()} == EXPECTED_TEMPLATE_IDS

    for automation_id in EXPECTED_TEMPLATE_IDS:
        assert (SEED_ROOT / automation_id / "memory.md").exists()


def test_static_automations_are_valid_paused_toml() -> None:
    for path in _automation_paths():
        data = tomllib.loads(path.read_text(encoding="utf-8"))

        assert data["id"] == path.parent.name
        assert data["status"] == "PAUSED"
        assert data["kind"] in {"cron", "heartbeat"}
        assert data["model"] == "gpt-5.5"
        assert data["reasoning_effort"] == "medium"
        assert data["prompt"].strip()
        assert data["rrule"].strip()
        if data["kind"] == "heartbeat":
            assert data["target_thread_id"] == "REPLACE_WITH_CODEX_THREAD_ID"
        else:
            assert data["execution_environment"] == "local"
            assert data["cwds"]


def test_automations_encode_human_gate_and_digest_schedule() -> None:
    triage = tomllib.loads(
        (SEED_ROOT / "studyos-github-triage" / "automation.toml").read_text(encoding="utf-8")
    )
    weekly = tomllib.loads(
        (SEED_ROOT / "studyos-weekly-digest" / "automation.toml").read_text(encoding="utf-8")
    )
    group_digest = tomllib.loads(
        (SEED_ROOT / "studyos-group-channel-digest" / "automation.toml").read_text(
            encoding="utf-8"
        )
    )
    group_digest_prompt = " ".join(group_digest["prompt"].split())

    assert "Do not start implementation" in triage["prompt"]
    assert "human-gated" in triage["prompt"]
    assert weekly["rrule"] == "RRULE:FREQ=WEEKLY;BYDAY=TH;BYHOUR=16;BYMINUTE=0"
    assert group_digest["rrule"] == "RRULE:FREQ=DAILY;BYHOUR=17;BYMINUTE=0"
    assert group_digest["config"]["guild_id"] == "1501971751247024228"
    assert group_digest["config"]["destination_channel_name_candidates"] == ["updates"]
    assert group_digest["config"]["group_channel_name_globs"] == ["group-*"]
    assert group_digest["config"]["ongoing_engagement_window_minutes"] == 90
    assert group_digest["config"]["deferred_proposal_min_delay_minutes"] == 60
    assert group_digest["config"]["deferred_summary_proposals"] == {}
    assert "Post to #updates only after" in group_digest["prompt"]
    assert "ongoing engagement" in group_digest["prompt"]
    assert "between the hold-off time and the next regular cron fire" in group_digest_prompt


def test_codex_config_seed_sets_medium_reasoning() -> None:
    data = tomllib.loads(CODEX_CONFIG_PATH.read_text(encoding="utf-8"))

    assert data["model"] == "gpt-5.5"
    assert data["model_reasoning_effort"] == "medium"
