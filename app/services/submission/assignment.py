"""Evaluator assignment and divide-equally helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class AssignmentMixin:
    def assign_evaluator(
        self,
        submission_id: str,
        evaluator_id: str | None,
        assigned_by: str,
    ) -> dict[str, Any]:
        """Assign one approved evaluator to a submission, or clear the assignment."""
        # User lookup stays outside the transaction (different collection / service).
        evaluator: dict[str, Any] | None = None
        if evaluator_id is not None and str(evaluator_id).strip():
            evaluator = self._require_active_evaluator(evaluator_id.strip())

        now = datetime.utcnow().isoformat()

        def _txn(transaction):
            submission = self.firebase.txn_get(
                transaction, self.collection, submission_id
            )
            if not submission:
                raise ValueError("Submission not found")

            if evaluator is None:
                update = {
                    "assigned_evaluator_id": None,
                    "assigned_evaluator_name": None,
                    "assigned_at": None,
                    "assigned_by": None,
                    "review_status": "none",
                    "submitted_for_review_at": None,
                    "submitted_for_review_by": None,
                    "updated_at": now,
                }
            else:
                update = {
                    "assigned_evaluator_id": evaluator["id"],
                    "assigned_evaluator_name": evaluator["name"],
                    "assigned_at": now,
                    "assigned_by": assigned_by,
                    # Reassignment clears in-flight review submission.
                    "review_status": "none",
                    "submitted_for_review_at": None,
                    "submitted_for_review_by": None,
                    "updated_at": now,
                }

            self.firebase.txn_update(
                transaction, self.collection, submission_id, update
            )
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
                raise ValueError(
                    f"Submission {submission_id} does not belong to this hackathon"
                )
            submissions.append({"id": submission_id, **submission})

        evaluators = self._resolve_active_evaluators(evaluator_ids)
        if not evaluators:
            raise ValueError("No active (approved) evaluators available to assign")

        random.shuffle(submissions)
        random.shuffle(evaluators)

        now = datetime.utcnow().isoformat()
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

    def _resolve_active_evaluators(
        self, evaluator_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        approved = self.user_service.get_evaluators(approval_status="approved")
        by_id = {item["id"]: item for item in approved if item.get("id")}

        if evaluator_ids is None:
            return [
                {
                    "id": item["id"],
                    "name": item.get("name") or item.get("email") or item["id"],
                }
                for item in approved
                if item.get("id")
            ]

        resolved: list[dict[str, Any]] = []
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
        return resolved

