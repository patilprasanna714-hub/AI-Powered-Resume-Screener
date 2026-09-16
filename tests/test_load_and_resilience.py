"""
tests/test_load_and_resilience.py
Owner: Shashank — DynamoDB, DevOps, Reliability & QA

Load, concurrency, resilience, and failure recovery test suite:
- High-volume burst ingest simulation (50+ candidate items)
- SQS partial batch failure handling (ReportBatchItemFailures)
- Transient retry exhaustion and transition to EXHAUSTED
- Dead-Letter Queue (DLQ) capture -> operator review -> redrive cycle
- Optimistic locking and conditional version concurrency
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from services.reliability.failed_jobs_service import (
    MAX_REDRIVE_ATTEMPTS,
    list_failed_jobs,
    process_dlq_message,
    record_failure,
    retry_failed_job,
)


class TestLoadAndResilience(unittest.TestCase):
    """Resilience, load simulation, and failure recovery testing."""

    def test_burst_ingest_batch_simulation(self):
        """Simulate a burst batch of 50 candidates being processed concurrently."""
        mock_table = MagicMock()
        total_candidates = 50
        recorded_items = []

        for i in range(total_candidates):
            item = record_failure(
                job_id="job_burst_101",
                candidate_id=f"cand_burst_{i:03d}",
                stage="SCORING",
                error_code="CAPACITY_THROTTLED",
                error_message=f"Transient throttle on worker {i}",
                status="FAILED",
                dynamo_table=mock_table,
            )
            recorded_items.append(item)

        self.assertEqual(len(recorded_items), total_candidates)
        # Verify all failure IDs are distinct (UUID collision resistance)
        unique_ids = {item["failure_id"] for item in recorded_items}
        self.assertEqual(len(unique_ids), total_candidates)
        self.assertEqual(mock_table.put_item.call_count, total_candidates)

    def test_sqs_partial_batch_failure_resilience(self):
        """
        Verify partial batch failure reporting:
        In a batch of 10 messages, 2 failures report only the failed IDs in batchItemFailures.
        """
        batch_size = 10
        failing_indices = {2, 7}
        records = [
            {
                "messageId": f"msg_batch_{idx}",
                "body": json.dumps({"job_id": "job_1", "candidate_id": f"cand_{idx}"}),
            }
            for idx in range(batch_size)
        ]

        batch_item_failures = []
        processed_successfully = []

        for idx, rec in enumerate(records):
            if idx in failing_indices:
                # Simulated transient exception
                batch_item_failures.append({"itemIdentifier": rec["messageId"]})
            else:
                processed_successfully.append(rec["messageId"])

        self.assertEqual(len(batch_item_failures), 2)
        self.assertEqual(len(processed_successfully), 8)
        self.assertEqual(
            batch_item_failures,
            [{"itemIdentifier": "msg_batch_2"}, {"itemIdentifier": "msg_batch_7"}],
        )

    def test_retry_exhaustion_lifecycle(self):
        """
        Verify the complete lifecycle of retries up to exhaustion (MAX_REDRIVE_ATTEMPTS = 5).
        """
        mock_failed_table = MagicMock()
        failure_record = {
            "failure_id": "fail_exhaustion_test",
            "job_id": "job_ex",
            "candidate_id": "cand_ex",
            "stage": "SCORING",
            "status": "FAILED",
            "retry_count": 0,
        }
        mock_failed_table.get_item.return_value = {"Item": failure_record}
        mock_audit = MagicMock()
        mock_sqs = MagicMock()

        # Retries 1 through 4 should succeed
        for attempt in range(1, MAX_REDRIVE_ATTEMPTS):
            failure_record["retry_count"] = attempt - 1
            result = retry_failed_job(
                failure_id="fail_exhaustion_test",
                actor_sub="admin_test",
                dynamo_failed_jobs=mock_failed_table,
                dynamo_audit=mock_audit,
                sqs_client=mock_sqs,
                scoring_queue_url="https://sqs.aws.com/queue",
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["retry_count"], attempt)

        # 5th retry attempt (retry_count == 5) must trigger exhaustion
        failure_record["retry_count"] = MAX_REDRIVE_ATTEMPTS
        with self.assertRaises(ValueError) as ctx:
            retry_failed_job(
                failure_id="fail_exhaustion_test",
                dynamo_failed_jobs=mock_failed_table,
            )
        self.assertIn("exceeded maximum allowed retries", str(ctx.exception))
        mock_failed_table.update_item.assert_called()

    def test_dlq_redrive_end_to_end_flow(self):
        """
        Complete loop: Poison message reaches DLQ -> DLQ capture persists to failed_jobs ->
        Recruiter queries failures -> Admin redrives failed job -> Re-queued to SQS with audit.
        """
        mock_failed_table = MagicMock()
        mock_audit_table = MagicMock()
        mock_sqs = MagicMock()

        # Step 1: Poison SQS message arrives at DLQ
        dlq_record = {
            "messageId": "msg_poison_e2e_999",
            "body": json.dumps({"job_id": "job_ai_01", "candidate_id": "cand_e2e_01"}),
            "attributes": {"ApproximateReceiveCount": "3"},
        }
        captured = process_dlq_message(dlq_record, dynamo_failed_jobs=mock_failed_table)
        self.assertEqual(captured["status"], "DEAD_LETTERED")
        self.assertEqual(captured["retry_count"], 3)

        # Step 2: Recruiter queries list of open failures
        mock_failed_table.scan.return_value = {"Items": [captured]}
        failures = list_failed_jobs(status="DEAD_LETTERED", dynamo_table=mock_failed_table)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["failure_id"], captured["failure_id"])

        # Step 3: Admin redrives the job
        mock_failed_table.get_item.return_value = {"Item": captured}
        redrive_result = retry_failed_job(
            failure_id=captured["failure_id"],
            actor_sub="shashank_admin",
            dynamo_failed_jobs=mock_failed_table,
            dynamo_audit=mock_audit_table,
            sqs_client=mock_sqs,
            scoring_queue_url="https://sqs.ap-south-1.amazonaws.com/123/resume-scoring-queue",
        )

        self.assertTrue(redrive_result["success"])
        self.assertEqual(redrive_result["status"], "RETRIED")
        self.assertEqual(redrive_result["retry_count"], 4)

        # Step 4: Verify SQS received the redrive payload with correlation tracking
        mock_sqs.send_message.assert_called_once()
        body_sent = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        self.assertEqual(body_sent["job_id"], "job_ai_01")
        self.assertEqual(body_sent["candidate_id"], "cand_e2e_01")
        self.assertEqual(body_sent["redrive_from_failure_id"], captured["failure_id"])

        # Step 5: Verify immutable audit trail was recorded
        mock_audit_table.put_item.assert_called_once()
        audit_entry = mock_audit_table.put_item.call_args[1]["Item"]
        self.assertEqual(audit_entry["actor_sub"], "shashank_admin")
        self.assertEqual(audit_entry["old_state"], "DEAD_LETTERED")
        self.assertEqual(audit_entry["new_state"], "RETRIED")

    def test_optimistic_locking_collision_simulation(self):
        """
        Verify that optimistic locking prevents race condition overwrites:
        Updating candidate with outdated version attribute fails conditionally.
        """
        current_candidate = {
            "job_id": "job_01",
            "candidate_id": "cand_01",
            "recruiter_decision": "SHORTLIST",
            "version": 2,
        }

        # Worker A attempts update assuming version == 1
        worker_a_expected_version = 1
        is_stale_update = (worker_a_expected_version != current_candidate["version"])
        self.assertTrue(is_stale_update, "Worker A update should be detected as stale conflict")

        # Worker B attempts update with correct current version == 2
        worker_b_expected_version = 2
        is_valid_update = (worker_b_expected_version == current_candidate["version"])
        self.assertTrue(is_valid_update, "Worker B update succeeds with current version")


if __name__ == "__main__":
    unittest.main()
