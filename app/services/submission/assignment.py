"""Evaluator assignment and divide-equally helpers."""

from __future__ import annotations

from typing import Any

from app.exceptions import BadRequestError
from app.utils.time import now_ist_iso

_SUBMITTED_REVIEW_STATUSES = frozenset({"pending_review", "changes_requested", "approved"})
_REASON_REQUIRED = "Give a reason for changing the evaluator."


def _assigned_id(submission: dict[str, Any]) -> str | None:
    raw = submission.get("assigned_evaluator_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _evaluation_was_submitted(submission: dict[str, Any]) -> bool:
    """True when the current evaluator has a score or the review has left 'none'."""
    if submission.get("final_score") is not None:
        return True
    if submission.get("evaluator_score") is not None:
        return True
    review = str(submission.get("review_status") or "none").strip()
    return review in _SUBMITTED_REVIEW_STATUSES


class AssignmentMixin:
    def assign_evaluator(
        self,
        submission_id: str,
        evaluator_id: str | None,
        assigned_by: str,
        *,
        assigned_by_name: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Assign one approved evaluator to a submission, or clear the assignment.

        A first assignment and clearing the assignee need no reason. A
        person-to-person change does. The previous score stays on the
        submission; the review workflow resets so the new evaluator can submit.
        """
        target_id = (evaluator_id or "").strip() or None
        cleaned_reason = (reason or "").strip() or None
        if cleaned_reason is not None and len(cleaned_reason) > 500:
            raise BadRequestError("Reason must be 500 characters or fewer.")

        # User lookup stays outside the transaction (different collection / service).
        evaluator: dict[str, Any] | None = None
        if target_id is not None:
            evaluator = self._require_active_evaluator(target_id)
            existing = self.firebase.get_document(self.collection, submission_id)
            hackathon_id = existing.get("hackathon_id") if isinstance(existing, dict) else None
            self._assert_evaluator_on_hackathon(hackathon_id, evaluator["id"] if evaluator else "")

        now = now_ist_iso()
        actor_name = (assigned_by_name or "").strip() or assigned_by

        def _txn(transaction):
            submission = self.firebase.txn_get(transaction, self.collection, submission_id)
            if not submission:
                raise ValueError("Submission not found")

            previous_id = _assigned_id(submission)
            next_id = evaluator["id"] if evaluator else None
            if previous_id == next_id:
                return {"id": submission_id, **submission}

            person_change = bool(previous_id and next_id)
            if person_change and not cleaned_reason:
                raise BadRequestError(_REASON_REQUIRED, code="REASON_REQUIRED")

            history = list(submission.get("assignment_history") or [])
            history.append(
                {
                    "changed_at": now,
                    "changed_by_id": assigned_by,
                    "changed_by_name": actor_name,
                    "from_evaluator_id": previous_id,
                    "from_evaluator_name": (
                        (submission.get("assigned_evaluator_name") or "").strip() or None
                        if previous_id
                        else None
                    ),
                    "to_evaluator_id": next_id,
                    "to_evaluator_name": evaluator["name"] if evaluator else None,
                    "reason": cleaned_reason if person_change else None,
                    "evaluation_submitted": bool(previous_id)
                    and _evaluation_was_submitted(submission),
                }
            )

            if evaluator is None:
                update = {
                    "assigned_evaluator_id": None,
                    "assigned_evaluator_name": None,
                    "assigned_at": None,
                    "assigned_by": None,
                    "review_status": "none",
                    "submitted_for_review_at": None,
                    "submitted_for_review_by": None,
                    "assignment_history": history,
                    "updated_at": now,
                }
            else:
                update = {
                    "assigned_evaluator_id": evaluator["id"],
                    "assigned_evaluator_name": evaluator["name"],
                    "assigned_at": now,
                    "assigned_by": assigned_by,
                    # The previous scorecard and final_score stay. Review resets
                    # so the new evaluator can submit their own evaluation.
                    "review_status": "none",
                    "submitted_for_review_at": None,
                    "submitted_for_review_by": None,
                    "assignment_history": history,
                    "updated_at": now,
                }

            self.firebase.txn_update(transaction, self.collection, submission_id, update)
            return {"id": submission_id, **submission, **update}

        return self.firebase.run_transaction(_txn)

    def divide_equally_among_evaluators(
        self,
        hackathon_id: str,
        submission_ids: list[str],
        assigned_by: str,
        evaluator_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Shuffle the selected submissions and assign them round-robin to active
        evaluators so the load is roughly equal.
        """
        import random

        hackathon = self.hackathon_service.get_hackathon(hackathon_id)
        if not hackathon:
            raise ValueError("Hackathon not found")

        unique_ids = list(dict.fromkeys(sid.strip() for sid in submission_ids if sid.strip()))
        if not unique_ids:
            raise ValueError("At least one submission id is required")

        submissions: list[dict[str, Any]] = []
        for submission_id in unique_ids:
            submission = self.firebase.get_document(self.collection, submission_id)
            if not submission:
                raise ValueError(f"Submission not found: {submission_id}")
            if submission.get("hackathon_id") != hackathon_id:
                raise ValueError(f"Submission {submission_id} does not belong to this hackathon")
            submissions.append({"id": submission_id, **submission})

        allowed_ids = self._hackathon_evaluator_ids(hackathon)
        evaluators = self._resolve_active_evaluators(evaluator_ids, allowed_ids=allowed_ids)
        if not evaluators:
            if allowed_ids is not None:
                raise ValueError("No evaluators assigned to this hackathon")
            raise ValueError("No active (approved) evaluators available to assign")

        random.shuffle(submissions)
        random.shuffle(evaluators)

        now = now_ist_iso()
        operations: list[dict[str, Any]] = []
        planned: list[dict[str, Any]] = []
        for index, submission in enumerate(submissions):
            evaluator = evaluators[index % len(evaluators)]
            update = {
                "assigned_evaluator_id": evaluator["id"],
                "assigned_evaluator_name": evaluator["name"],
                "assigned_at": now,
                "assigned_by": assigned_by,
                "updated_at": now,
            }
            operations.append(
                {
                    "type": "update",
                    "collection": self.collection,
                    "document_id": submission["id"],
                    "data": update,
                }
            )
            planned.append({"id": submission["id"], **submission, **update})

        if operations:
            self.firebase.batch_write(operations)

        return planned

    def _require_active_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        user = self.user_service.get_user(evaluator_id)
        if not user or user.get("role") != "evaluator":
            raise ValueError("Evaluator not found")
        if user.get("approval_status") != "approved":
            raise ValueError("Evaluator is not active (must be approved)")
        return {
            "id": evaluator_id,
            "name": user.get("name") or user.get("email") or evaluator_id,
        }

    def _hackathon_evaluator_ids(self, hackathon: dict[str, Any]) -> set[str] | None:
        """
        Roster for this hackathon.

        ``None`` means the hackathon has no saved roster (created before this
        setting), so any approved evaluator may still be assigned.
        """
        if not isinstance(hackathon, dict) or "evaluator_ids" not in hackathon:
            return None
        return {str(item) for item in (hackathon.get("evaluator_ids") or []) if item}

    def _assert_evaluator_on_hackathon(self, hackathon_id: str | None, evaluator_id: str) -> None:
        if not isinstance(hackathon_id, str) or not hackathon_id:
            return
        hackathon = self.hackathon_service.get_hackathon(hackathon_id)
        allowed_ids = self._hackathon_evaluator_ids(hackathon)
        if allowed_ids is None:
            return
        if evaluator_id not in allowed_ids:
            raise ValueError("Evaluator is not assigned to this hackathon")

    def _resolve_active_evaluators(
        self,
        evaluator_ids: list[str] | None,
        allowed_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        approved = self.user_service.get_evaluators(approval_status="approved")
        by_id = {item["id"]: item for item in approved if item.get("id")}

        if evaluator_ids is None:
            resolved = [
                {
                    "id": item["id"],
                    "name": item.get("name") or item.get("email") or item["id"],
                }
                for item in approved
                if item.get("id")
            ]
        else:
            resolved = []
            seen: set[str] = set()
            for raw in evaluator_ids:
                eid = (raw or "").strip()
                if not eid or eid in seen:
                    continue
                if eid not in by_id:
                    raise ValueError(f"Evaluator is not active/approved: {eid}")
                item = by_id[eid]
                resolved.append(
                    {
                        "id": eid,
                        "name": item.get("name") or item.get("email") or eid,
                    }
                )
                seen.add(eid)
            if allowed_ids is not None:
                outside = [item["id"] for item in resolved if item["id"] not in allowed_ids]
                if outside:
                    raise ValueError("Evaluator is not assigned to this hackathon")

        if allowed_ids is not None:
            resolved = [item for item in resolved if item["id"] in allowed_ids]
        return resolved
