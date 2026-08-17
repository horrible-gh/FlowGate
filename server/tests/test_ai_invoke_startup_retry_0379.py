"""0435 T0004: an explicit per-step provider owns startup without a fallback tail."""
from __future__ import annotations

from test_ai_invoke_0187 import _provider, _wait_finished, fake_env  # noqa: F401

from modules.flow_gate.services import ai_invoke_service as svc


def test_continuous_step_override_start_failure_does_not_substitute(
    fake_env, monkeypatch,
):
    """The explicit step override is attempted once; common/default providers never run."""
    fallback = _provider(name="Fable", exec_type="api", kind="openai", pid="aip_fable")
    common = _provider(name="Common", exec_type="api", kind="openai", pid="aip_common")
    individual = _provider(
        name="Individual", exec_type="api", kind="openai", pid="aip_individual"
    )
    unused = _provider(name="Unused", exec_type="api", kind="openai", pid="aip_unused")
    providers = [fallback, common, individual, unused]
    fake_env["chain"]["providers"] = providers
    fake_env["chain"]["registered_count"] = len(providers)

    monkeypatch.setattr(
        svc,
        "_resolve_continuation_hop_override",
        lambda *args, **kwargs: "aip_individual",
    )

    result = svc.start_run(
        project_id="flowgate",
        module="default",
        group_id="flowgate.default.0187",
        doc_ref="flowgate.default.0187.0001-R",
        action_scope="new",
        mode="continuous",
        continuation_target_seq=6,
        continuation_review_mode=False,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\ndo the work\n",
        provider_id="aip_common",
        continuation_provider_overrides={"4": "aip_individual"},
    )
    run = _wait_finished(result["run_id"])

    assert result["provider"]["id"] == "aip_individual"
    assert run["end_reason"] == "all_providers_failed"
    assert run["provider_id"] is None
    assert [item["provider_id"] for item in run["fallback_history"]] == ["aip_individual"]
    assert [item["attempt_no"] for item in run["fallback_history"]] == [1]
    assert all(item["reason"] == "spawn_failed" for item in run["fallback_history"])

    switches = [
        payload
        for event_type, payload in fake_env["events"]
        if event_type == "ai_invoke_provider_switched"
    ]
    assert switches == []