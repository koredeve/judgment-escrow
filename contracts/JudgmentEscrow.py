# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_OPEN = "open"
STATUS_ACCEPTED = "accepted"
STATUS_SUBMITTED = "submitted"
STATUS_RELEASED = "released"
STATUS_REFUNDED = "refunded"
STATUS_DISPUTED = "disputed"


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _coerce_bool(raw) -> bool:
	if isinstance(raw, bool):
		return raw
	s = str(raw).strip().lower()
	if s in ("true", "1", "yes"):
		return True
	if s in ("false", "0", "no"):
		return False
	raise gl.vm.UserError(f"{ERROR_LLM} non-boolean for_worker field in LLM output")


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


@allow_storage
@dataclass
class Job:
	client: Address
	worker: str
	description: str
	requirements: str
	deliverable: str
	amount_atto: u256
	status: str
	ruling: str


class JudgmentEscrow(gl.Contract):
	jobs: TreeMap[str, Job]
	job_ids: DynArray[str]
	credits: TreeMap[Address, u256]

	def __init__(self) -> None:
		pass

	def _get_job(self, job_id: str) -> Job:
		job = self.jobs.get(job_id)
		if job is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown job id")
		return job

	def _credit(self, who: Address, amount: u256) -> None:
		self.credits[who] = self.credits.get(who, u256(0)) + amount

	@gl.public.write.payable
	def create_job(self, job_id: str, description: str, requirements: str) -> None:
		if gl.message.value == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Send value with the call")
		clean_id = str(job_id).strip()
		clean_desc = str(description).strip()
		clean_req = str(requirements).strip()
		if not clean_id or not clean_desc or not clean_req:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Job id, description, and requirements must not be empty")
		if clean_id in self.jobs:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Job id already exists")
		self.jobs[clean_id] = Job(
			client=gl.message.sender_address,
			worker="",
			description=clean_desc,
			requirements=clean_req,
			deliverable="",
			amount_atto=u256(gl.message.value),
			status=STATUS_OPEN,
			ruling="",
		)
		self.job_ids.append(clean_id)

	@gl.public.write
	def accept_job(self, job_id: str) -> None:
		job = self._get_job(job_id)
		if job.status != STATUS_OPEN:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Job is not open for acceptance")
		if gl.message.sender_address == job.client:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Client cannot accept their own job")
		job.worker = str(gl.message.sender_address)
		job.status = STATUS_ACCEPTED

	@gl.public.write
	def submit_work(self, job_id: str, deliverable: str) -> None:
		job = self._get_job(job_id)
		if str(gl.message.sender_address) != job.worker:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Only the accepted worker may submit work"
			)
		if job.status != STATUS_ACCEPTED:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Work can only be submitted after acceptance"
			)
		clean_deliv = str(deliverable).strip()
		if not clean_deliv:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Deliverable cannot be empty")
		job.deliverable = clean_deliv
		job.status = STATUS_SUBMITTED

	@gl.public.write
	def approve_work(self, job_id: str) -> None:
		job = self._get_job(job_id)
		if gl.message.sender_address != job.client:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the client may approve work")
		if job.status != STATUS_SUBMITTED:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Work has not been submitted for approval"
			)
		self._credit(Address(job.worker), job.amount_atto)
		job.status = STATUS_RELEASED

	@gl.public.write
	def raise_dispute(self, job_id: str) -> None:
		job = self._get_job(job_id)
		sender = gl.message.sender_address
		if sender != job.client and str(sender) != job.worker:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Only client or worker may raise a dispute"
			)
		if job.status != STATUS_SUBMITTED:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Disputes require submitted work")
		job.status = STATUS_DISPUTED

	@gl.public.write
	def resolve_dispute(self, job_id: str) -> None:
		job = self._get_job(job_id)
		if job.status != STATUS_DISPUTED:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Job is not in dispute")
		requirements_text = str(job.requirements)
		deliverable_text = str(job.deliverable)

		def leader_fn() -> dict:
			prompt = (
				"Arbitrate this freelance dispute.\n"
				f"REQUIREMENTS: <req>{requirements_text}</req>\n"
				f"DELIVERABLE: <del>{deliverable_text}</del>\n"
				"Did the deliverable substantially meet the requirements? "
				'Reply JSON {"for_worker": true/false, "reasoning": "..."}'
			)
			analysis = gl.nondet.exec_prompt(prompt, response_format="json")
			parsed = _parse_llm_json(analysis)
			raw = None
			for key in ("for_worker", "worker_wins", "approved"):
				if key in parsed:
					raw = parsed[key]
					break
			if raw is None:
				raise gl.vm.UserError(
					f"{ERROR_LLM} missing for_worker field in LLM output"
				)
			for_worker = _coerce_bool(raw)
			reasoning = parsed.get("reasoning", "")
			return {"for_worker": bool(for_worker), "reasoning": str(reasoning)}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			return bool(leader_data.get("for_worker")) == bool(fresh.get("for_worker"))

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		if bool(result["for_worker"]):
			self._credit(Address(job.worker), job.amount_atto)
			job.status = STATUS_RELEASED
		else:
			self._credit(job.client, job.amount_atto)
			job.status = STATUS_REFUNDED
		job.ruling = str(result["reasoning"])

	@gl.public.write
	def cancel_open_job(self, job_id: str) -> None:
		job = self._get_job(job_id)
		if gl.message.sender_address != job.client:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the client may cancel a job")
		if job.status != STATUS_OPEN:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only open jobs can be cancelled")
		self._credit(job.client, job.amount_atto)
		job.status = STATUS_REFUNDED

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(Address(who)).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_job(self, job_id: str) -> dict:
		job = self._get_job(job_id)
		return {
			"client": str(job.client),
			"worker": job.worker,
			"description": job.description,
			"requirements": job.requirements,
			"deliverable": job.deliverable,
			"amount_atto": job.amount_atto,
			"status": job.status,
			"ruling": job.ruling,
		}

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_jobs(self) -> u256:
		return u256(len(self.job_ids))
