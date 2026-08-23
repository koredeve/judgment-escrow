JudgmentEscrow — freelance escrow with AI arbitration for GenLayer
==================================================================

JudgmentEscrow escrows a freelance payment on-chain and lets an AI model act
as the arbiter of last resort. The client funds a job with `value`; the first
worker to accept becomes the assigned worker; the worker submits the
deliverable as text. The client can then approve (paying the worker), or — if
they disagree — either party escalates to AI judgment on whether the
deliverable substantially met the requirements. Funds never move directly to
users: settlement credits an internal balance that each party drains with
`withdraw`.

Architecture
------------

- **User action**: client calls `create_job(job_id, description, requirements)`
  with value; a worker calls `accept_job`, then `submit_work(deliverable)`;
  settlement follows via `approve_work` (client), `raise_dispute` +
  `resolve_dispute` (either party / anyone resolves), or `cancel_open_job`
  while the job is still open.
- **Evidence source**: the job record itself — the requirements text stored at
  funding time and the deliverable text stored at submission, both read from
  contract storage inside the arbitration.
- **Nondet call**: `resolve_dispute` runs
  `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`. The leader prompts the
  model (`REQUIREMENTS: <req>...</req>` vs `DELIVERABLE: <del>...</del>`) for
  JSON `{"for_worker": true/false, "reasoning": "..."}` with
  `response_format="json"`, then defensively parses it via `_parse_llm_json`
  with key aliases (`for_worker` / `worker_wins` / `approved`) and string
  coercion (`"true"` / `"yes"` / `"1"`).
- **Equivalence principle**: exact agreement on the decision field. The
  validator independently reruns `leader_fn` and accepts only when its fresh
  `for_worker` boolean EQUALS the leader's — a binary ruling has no tolerance
  band, so any disagreement rejects the proposal. Leader failures go through
  the canonical `_handle_leader_error` rules: transient errors on both sides
  agree, deterministic errors must match exactly. Leader output is never
  trusted without this independent rerun comparison.
- **Settlement effect**: worker wins → full escrow credited to the worker,
  status `released`; worker loses → full amount credited back to the client,
  status `refunded`; the reasoning is persisted as the job's `ruling`.
  Credits are readable via `credit_of` and cashed out with `withdraw`.
- **Appeal path**: GenLayer Optimistic Democracy provides
  leader-proposes / validator-check natively, including an appeal window in
  which stakers can challenge a settled result before it becomes final.

Quickstart
----------

Requires Python 3.14.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lint the contract
genvm-lint check contracts/JudgmentEscrow.py --json

# Run direct-mode tests (LLM mocked)
pytest tests/direct/ -v
```

Integration tests target StudioNet via `gltest.config.yaml`.

Interface
---------

| Method | Type | Notes |
| --- | --- | --- |
| `create_job(job_id, description, requirements)` | write, payable | Requires value > 0; unique id; sender becomes client; status `open`. |
| `accept_job(job_id)` | write | First-come worker assignment on an `open` job only. |
| `submit_work(job_id, deliverable)` | write | Accepted worker only, after acceptance; status `submitted`. |
| `approve_work(job_id)` | write | Client only, after submission; credits worker in full; status `released`. |
| `raise_dispute(job_id)` | write | Client or worker only, only on a `submitted` job; status `disputed`. |
| `resolve_dispute(job_id)` | write | Runs AI arbitration with exact-agreement validation; pays worker (`released`) or refunds client (`refunded`); stores `ruling`. |
| `cancel_open_job(job_id)` | write | Client only, while still `open`; credits back the client; status `refunded`. |
| `withdraw()` | write | Drains the caller's credit balance via an emit transfer. |
| `get_job(job_id)` | view | Full job record; addresses exposed as strings (worker empty until accepted). |
| `credit_of(who)` | view | Withdrawable balance of an address. |
| `total_jobs()` | view | Number of jobs created. |

StudioNet note: transactions on StudioNet are gasless — holding 0 GEN is fine.
