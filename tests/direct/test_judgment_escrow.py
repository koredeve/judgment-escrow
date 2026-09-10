import json

AMOUNT = 15 * 10**18

DESCRIPTION = "Build a landing page for my coffee shop"
REQUIREMENTS = "Single page, hero image, menu section, contact form, mobile friendly."
DELIVERABLE = "Landing page with hero image, full menu, working contact form, responsive CSS."

PROMPT_REGEX = r"Arbitrate this freelance dispute"


def _deploy(direct_deploy):
    return direct_deploy("contracts/JudgmentEscrow.py")


def _create_job(direct_vm, contract, alice, job_id="job-1"):
    direct_vm.sender = alice
    direct_vm.value = AMOUNT
    contract.create_job(job_id, DESCRIPTION, REQUIREMENTS)
    direct_vm.value = 0


def _accept_and_submit(direct_vm, contract, bob):
    with direct_vm.prank(bob):
        contract.accept_job("job-1")
        contract.submit_work("job-1", DELIVERABLE)


def test_create_job_stores_state_and_rejects_duplicate_id(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Client funds a job and the stored state is visible via views."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    job = contract.get_job("job-1")
    assert len(job["client"]) > 0
    assert job["worker"] == ""
    assert job["description"] == DESCRIPTION
    assert job["requirements"] == REQUIREMENTS
    assert job["deliverable"] == ""
    assert job["amount_atto"] == AMOUNT
    assert job["status"] == "open"
    assert job["ruling"] == ""
    assert contract.total_jobs() == 1

    direct_vm.sender = direct_alice
    direct_vm.value = AMOUNT
    with direct_vm.expect_revert("Job id already exists"):
        contract.create_job("job-1", DESCRIPTION, REQUIREMENTS)
    direct_vm.value = 0
    assert contract.total_jobs() == 1


def test_create_job_requires_nonzero_value(direct_vm, direct_deploy, direct_alice):
    """Creating a job without escrowed value is rejected."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.create_job("job-zero", DESCRIPTION, REQUIREMENTS)
    assert contract.total_jobs() == 0


def test_full_flow_accept_submit_approve_pays_worker(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Accept, submit, approve: the worker's credit equals the full escrow amount."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    with direct_vm.prank(direct_bob):
        contract.accept_job("job-1")
    job = contract.get_job("job-1")
    assert job["status"] == "accepted"
    assert len(job["worker"]) > 0

    with direct_vm.prank(direct_bob):
        contract.submit_work("job-1", DELIVERABLE)
    job = contract.get_job("job-1")
    assert job["status"] == "submitted"
    assert job["deliverable"] == DELIVERABLE

    direct_vm.sender = direct_alice
    contract.approve_work("job-1")

    assert contract.credit_of(direct_bob) == AMOUNT
    assert contract.credit_of(direct_alice) == 0
    assert contract.get_job("job-1")["status"] == "released"


def test_submission_and_dispute_access_rules(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Only the accepted worker submits; only the parties escalate; unknown ids revert."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    with direct_vm.expect_revert("Unknown job id"):
        contract.accept_job("missing-job")

    with direct_vm.prank(direct_charlie):
        with direct_vm.expect_revert("Only the accepted worker may submit work"):
            contract.submit_work("job-1", DELIVERABLE)

    _accept_and_submit(direct_vm, contract, direct_bob)

    with direct_vm.prank(direct_charlie):
        with direct_vm.expect_revert("Only client or worker may raise a dispute"):
            contract.raise_dispute("job-1")


def test_dispute_requires_submitted_work(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Disputes cannot be raised before the deliverable is submitted."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    with direct_vm.prank(direct_bob):
        contract.accept_job("job-1")

    with direct_vm.expect_revert("Disputes require submitted work"):
        contract.raise_dispute("job-1")

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Disputes require submitted work"):
            contract.raise_dispute("job-1")


def test_approve_before_submission_is_reverted(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """The client cannot approve work that was never submitted."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Work has not been submitted for approval"):
        contract.approve_work("job-1")

    with direct_vm.prank(direct_bob):
        contract.accept_job("job-1")
    with direct_vm.expect_revert("Work has not been submitted for approval"):
        contract.approve_work("job-1")


def test_double_approve_is_reverted(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A released job cannot be approved twice."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)
    _accept_and_submit(direct_vm, contract, direct_bob)

    direct_vm.sender = direct_alice
    contract.approve_work("job-1")
    with direct_vm.expect_revert("Work has not been submitted for approval"):
        contract.approve_work("job-1")

    assert contract.credit_of(direct_bob) == AMOUNT


def test_dispute_for_worker_true_pays_worker(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """AI rules the deliverable meets requirements: the worker is paid in full."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)
    _accept_and_submit(direct_vm, contract, direct_bob)

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"for_worker": True, "reasoning": "deliverable covers all requirements"}),
    )

    with direct_vm.prank(direct_bob):
        contract.raise_dispute("job-1")
    contract.resolve_dispute("job-1")

    assert contract.credit_of(direct_bob) == AMOUNT
    assert contract.credit_of(direct_alice) == 0
    job = contract.get_job("job-1")
    assert job["status"] == "released"
    assert job["ruling"] == "deliverable covers all requirements"


def test_dispute_for_worker_false_refunds_client(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """AI rules against the worker (alias key): the client is refunded in full."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)
    _accept_and_submit(direct_vm, contract, direct_bob)

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"worker_wins": False, "reasoning": "menu section missing"}),
    )

    direct_vm.sender = direct_alice
    contract.raise_dispute("job-1")
    contract.resolve_dispute("job-1")

    assert contract.credit_of(direct_alice) == AMOUNT
    assert contract.credit_of(direct_bob) == 0
    job = contract.get_job("job-1")
    assert job["status"] == "refunded"
    assert job["ruling"] == "menu section missing"


def test_cancel_open_job_refunds_client(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """The client can cancel an open job and reclaim the escrow; others cannot."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only the client may cancel a job"):
            contract.cancel_open_job("job-1")

    direct_vm.sender = direct_alice
    contract.cancel_open_job("job-1")

    assert contract.credit_of(direct_alice) == AMOUNT
    assert contract.credit_of(direct_bob) == 0
    assert contract.get_job("job-1")["status"] == "refunded"

    with direct_vm.expect_revert("Only open jobs can be cancelled"):
        contract.cancel_open_job("job-1")


def test_malformed_llm_output_raises_user_error(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Unparseable LLM output surfaces as an [LLM_ERROR] UserError and leaves the dispute open."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)
    _accept_and_submit(direct_vm, contract, direct_bob)

    direct_vm.mock_llm(PROMPT_REGEX, "Sorry, I cannot help with that.")

    with direct_vm.prank(direct_bob):
        contract.raise_dispute("job-1")
    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.resolve_dispute("job-1")

    assert contract.get_job("job-1")["status"] == "disputed"
    assert contract.credit_of(direct_bob) == 0
    assert contract.credit_of(direct_alice) == 0


def test_client_cannot_accept_own_job(direct_vm, direct_deploy, direct_alice):
    """The job client cannot accept their own job as a worker."""
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Client cannot accept their own job"):
        contract.accept_job("job-1")


def test_empty_job_inputs_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Empty description, requirements, or deliverable strings are rejected."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = AMOUNT

    with direct_vm.expect_revert("must not be empty"):
        contract.create_job("job-bad", "  ", "requirements")

    contract.create_job("job-good", "desc", "reqs")
    direct_vm.value = 0

    with direct_vm.prank(direct_bob):
        contract.accept_job("job-good")
        with direct_vm.expect_revert("Deliverable cannot be empty"):
            contract.submit_work("job-good", "   ")


def test_delivery_duration_bounds(direct_vm, direct_deploy, direct_alice):
    """Delivery duration must be between 1 hour and 90 days."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = AMOUNT

    # Too short (< 3600s)
    with direct_vm.expect_revert("Delivery duration must be between"):
        contract.create_job("job-short", DESCRIPTION, REQUIREMENTS, 1800)

    # Too long (> 90 days)
    with direct_vm.expect_revert("Delivery duration must be between"):
        contract.create_job("job-long", DESCRIPTION, REQUIREMENTS, 91 * 24 * 3600)

    # Valid custom duration (3 days)
    contract.create_job("job-valid", DESCRIPTION, REQUIREMENTS, 3 * 24 * 3600)
    job = contract.get_job("job-valid")
    assert job["delivery_duration"] == 3 * 24 * 3600
    assert job["deadline"] == 0


def test_accept_sets_deadline(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Accepting a job sets the active deadline to current time + delivery duration."""
    from unittest.mock import patch
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    now = 1700000000.0
    with patch("time.time", return_value=now):
        with direct_vm.prank(direct_bob):
            contract.accept_job("job-1")

    job = contract.get_job("job-1")
    assert job["status"] == "accepted"
    assert job["deadline"] == int(now) + 7 * 24 * 3600


def test_abandoned_job_premature_refund_rejected(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Client cannot claim refund for abandoned job before deadline expires."""
    from unittest.mock import patch
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    now = 1700000000.0
    with patch("time.time", return_value=now):
        with direct_vm.prank(direct_bob):
            contract.accept_job("job-1")

    # Try refunding while deadline is still in future
    with patch("time.time", return_value=now + 1000):
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("Delivery deadline has not expired yet"):
            contract.refund_abandoned_job("job-1")

        with direct_vm.expect_revert("Delivery deadline has not expired yet"):
            contract.reassign_abandoned_job("job-1")


def test_abandoned_job_refund_after_deadline_pays_client(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Client can safely refund an abandoned job after deadline passes; non-clients cannot."""
    from unittest.mock import patch
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    now = 1700000000.0
    with patch("time.time", return_value=now):
        with direct_vm.prank(direct_bob):
            contract.accept_job("job-1")

    # Time advances past deadline (7 days + 1 second)
    expired_time = now + 7 * 24 * 3600 + 1

    # Non-client cannot refund
    with patch("time.time", return_value=expired_time):
        with direct_vm.prank(direct_charlie):
            with direct_vm.expect_revert("Only the client may claim refund"):
                contract.refund_abandoned_job("job-1")

        # Worker cannot submit after deadline
        with direct_vm.prank(direct_bob):
            with direct_vm.expect_revert("Delivery deadline has passed"):
                contract.submit_work("job-1", DELIVERABLE)

        # Client safely claims abandoned refund
        direct_vm.sender = direct_alice
        contract.refund_abandoned_job("job-1")

    assert contract.credit_of(direct_alice) == AMOUNT
    assert contract.credit_of(direct_bob) == 0
    job = contract.get_job("job-1")
    assert job["status"] == "refunded"
    assert "abandoned" in job["ruling"]


def test_abandoned_job_reassignment_allows_new_worker(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Client can reassign an abandoned job back to open state so a new worker can accept."""
    from unittest.mock import patch
    contract = _deploy(direct_deploy)
    _create_job(direct_vm, contract, direct_alice)

    now = 1700000000.0
    with patch("time.time", return_value=now):
        with direct_vm.prank(direct_bob):
            contract.accept_job("job-1")

    expired_time = now + 7 * 24 * 3600 + 1
    with patch("time.time", return_value=expired_time):
        direct_vm.sender = direct_alice
        contract.reassign_abandoned_job("job-1")

    job = contract.get_job("job-1")
    assert job["status"] == "open"
    assert job["worker"] == ""
    assert job["deadline"] == 0

    # New worker Charlie accepts and submits
    now_reassigned = expired_time + 100
    with patch("time.time", return_value=now_reassigned):
        with direct_vm.prank(direct_charlie):
            contract.accept_job("job-1")
            contract.submit_work("job-1", DELIVERABLE)

    direct_vm.sender = direct_alice
    contract.approve_work("job-1")
    assert contract.credit_of(direct_charlie) == AMOUNT


