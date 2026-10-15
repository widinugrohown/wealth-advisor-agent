"""The output contract is the deliverable: test it before anything else."""
from advisor.contracts import AdvisoryOutput, Evidence, Finding, OUTPUT_KEYS


def test_output_has_every_required_key():
    out = AdvisoryOutput(run_id="r1", client_id="c001", generated_at="2026-10-15T07:00:00+08:00")
    assert out.missing_keys() == []
    assert set(out.to_dict()) == set(OUTPUT_KEYS)


def test_finding_serialises_nested_evidence():
    finding = Finding(finding_id="F1", type="anomaly", severity="high", rules=["R1"],
                      evidence=Evidence(tx_ids=["t0001"], amount_idr=48000000,
                                        baseline_idr=3200000, value=8.4),
                      explanation="15x above baseline", confidence=0.9)
    payload = finding.__dict__
    assert payload["evidence"].tx_ids == ["t0001"]
