from warden.agent.models import FileEdit, FixStrategy, Remediation
from warden.agent.verifier import Verifier


def test_blocked_remediation_is_not_executed():
    remediation = Remediation(
        strategy=FixStrategy.UPDATE_REFERENCES,
        blocked_reason="coverage insufficient",
    )
    result = Verifier().verify(remediation)

    assert result.attempts == []
    assert result.passed is False


def test_empty_remediation_is_not_executed():
    result = Verifier().verify(Remediation(strategy=FixStrategy.UPDATE_REFERENCES))

    assert result.attempts == []
    assert result.passed is False


def test_result_reports_failure_when_no_attempt_passed():
    """`passed` must be False on an empty attempt list — an unexecuted
    remediation has not been verified, and defaulting to True would let
    unverified code reach a PR."""
    from warden.agent.models import VerificationAttempt, VerificationResult

    failed = VerificationResult(
        attempts=[
            VerificationAttempt(attempt=1, command="dbt build", exit_code=1, output_tail="error")
        ]
    )
    assert failed.passed is False

    passed = VerificationResult(
        attempts=[
            VerificationAttempt(attempt=1, command="dbt build", exit_code=0, output_tail="ok")
        ]
    )
    assert passed.passed is True