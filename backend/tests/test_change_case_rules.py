"""
Run with: python3 -m unittest backend.tests.test_change_case_rules -v
"""
import unittest
from datetime import datetime, timedelta, timezone

import backend.app.services.change_case_rules as change_case_rules
from backend.app.services.change_case_rules import (
    MIN_HISTORICAL_ROWS_FOR_PREDICTION,
    MAX_CANDIDATES_PER_CHANGE_CASE,
    check_sufficient_data_for_prediction,
    check_status_transition_allowed,
    check_outcome_not_already_recorded,
    check_entitled_for_diagnostic,
    check_can_add_candidate,
    check_can_trigger_ranking,
    is_change_case_expired,
)
from backend.app.schemas.errors import ValidationError


class TestSufficientDataRule(unittest.TestCase):
    def test_allows_at_or_above_minimum(self):
        check_sufficient_data_for_prediction(MIN_HISTORICAL_ROWS_FOR_PREDICTION)
        check_sufficient_data_for_prediction(MIN_HISTORICAL_ROWS_FOR_PREDICTION + 50)

    def test_blocks_below_minimum(self):
        with self.assertRaises(ValidationError) as ctx:
            check_sufficient_data_for_prediction(MIN_HISTORICAL_ROWS_FOR_PREDICTION - 1)
        self.assertEqual(ctx.exception.field, "historical_row_count")

    def test_blocks_zero_rows(self):
        with self.assertRaises(ValidationError):
            check_sufficient_data_for_prediction(0)


class TestStatusTransitionRule(unittest.TestCase):
    def test_draft_can_move_to_active(self):
        check_status_transition_allowed("draft", "active")

    def test_active_can_move_to_completed(self):
        check_status_transition_allowed("active", "completed")

    def test_draft_cannot_skip_to_completed(self):
        with self.assertRaises(ValidationError) as ctx:
            check_status_transition_allowed("draft", "completed")
        self.assertEqual(ctx.exception.field, "status")

    def test_completed_is_terminal(self):
        with self.assertRaises(ValidationError):
            check_status_transition_allowed("completed", "active")
        with self.assertRaises(ValidationError):
            check_status_transition_allowed("completed", "draft")

    def test_unknown_status_rejected(self):
        with self.assertRaises(ValidationError):
            check_status_transition_allowed("not_a_real_status", "active")


class TestOutcomeAppendOnlyRule(unittest.TestCase):
    def test_allows_first_outcome(self):
        check_outcome_not_already_recorded(0)

    def test_blocks_second_outcome_for_same_candidate(self):
        with self.assertRaises(ValidationError) as ctx:
            check_outcome_not_already_recorded(1)
        self.assertEqual(ctx.exception.field, "candidate_id")

    def test_blocks_regardless_of_how_many_already_exist(self):
        for count in (1, 2, 10):
            with self.assertRaises(ValidationError):
                check_outcome_not_already_recorded(count)


class TestEntitlementRule(unittest.TestCase):

    def test_active_paid_plan_is_entitled(self):
        check_entitled_for_diagnostic(plan="pilot", subscription_status="active")
        check_entitled_for_diagnostic(plan="enterprise", subscription_status="active")

    def test_trial_plan_is_not_entitled_even_if_active(self):
        with self.assertRaises(ValidationError) as ctx:
            check_entitled_for_diagnostic(plan="trial", subscription_status="active")
        self.assertEqual(ctx.exception.field, "plan")

    def test_inactive_subscription_blocks_regardless_of_plan(self):
        with self.assertRaises(ValidationError) as ctx:
            check_entitled_for_diagnostic(plan="enterprise", subscription_status="past_due")
        self.assertEqual(ctx.exception.field, "subscription_status")

    def test_canceled_subscription_blocks(self):
        with self.assertRaises(ValidationError):
            check_entitled_for_diagnostic(plan="pilot", subscription_status="canceled")


class TestExpiryRule(unittest.TestCase):
    def test_future_expiry_is_not_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        self.assertFalse(is_change_case_expired(future))

    def test_past_expiry_is_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(is_change_case_expired(past))

    def test_handles_naive_iso_string_as_utc(self):
        # No tzinfo in the string at all -- must not raise, must be
        # treated as UTC, matching team_rules.py's proven-correct pattern.
        naive_future = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None).isoformat()
        self.assertFalse(is_change_case_expired(naive_future))

    def test_accepts_injected_now_for_deterministic_testing(self):
        expiry = "2026-06-01T00:00:00+00:00"
        before = datetime(2026, 5, 1, tzinfo=timezone.utc)
        after = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.assertFalse(is_change_case_expired(expiry, now=before))
        self.assertTrue(is_change_case_expired(expiry, now=after))


class TestCanAddCandidateRule(unittest.TestCase):
    def test_allows_adding_to_draft(self):
        check_can_add_candidate("draft", existing_candidate_count=0)

    def test_allows_adding_to_active(self):
        check_can_add_candidate("active", existing_candidate_count=2)

    def test_blocks_adding_to_completed(self):
        with self.assertRaises(ValidationError) as ctx:
            check_can_add_candidate("completed", existing_candidate_count=1)
        self.assertEqual(ctx.exception.field, "status")

    def test_blocks_at_max_candidates(self):
        with self.assertRaises(ValidationError) as ctx:
            check_can_add_candidate("draft", existing_candidate_count=MAX_CANDIDATES_PER_CHANGE_CASE)
        self.assertEqual(ctx.exception.field, "candidates")

    def test_allows_one_below_max(self):
        check_can_add_candidate("draft", existing_candidate_count=MAX_CANDIDATES_PER_CHANGE_CASE - 1)


class TestCanTriggerRankingRule(unittest.TestCase):
    def test_allows_with_sufficient_data_and_a_candidate(self):
        check_can_trigger_ranking(historical_row_count=MIN_HISTORICAL_ROWS_FOR_PREDICTION, candidate_count=1)

    def test_blocks_with_insufficient_data_even_with_candidates(self):
        with self.assertRaises(ValidationError) as ctx:
            check_can_trigger_ranking(historical_row_count=1, candidate_count=3)
        self.assertEqual(ctx.exception.field, "historical_row_count")

    def test_blocks_with_zero_candidates_even_with_enough_data(self):
        with self.assertRaises(ValidationError) as ctx:
            check_can_trigger_ranking(historical_row_count=50, candidate_count=0)
        self.assertEqual(ctx.exception.field, "candidates")


class TestClassifyDataQuality(unittest.TestCase):
    def test_valid_case(self):
        result = change_case_rules.classify_data_quality(
            historical_row_count=20, duplicate_count=0, constant_columns=[]
        )
        self.assertEqual(result, "valid")

    def test_insufficient_after_duplicates_removed(self):
        # 10 raw rows, 5 duplicates -> only 5 genuinely distinct, below the minimum
        result = change_case_rules.classify_data_quality(
            historical_row_count=10, duplicate_count=5, constant_columns=[]
        )
        self.assertEqual(result, "insufficient")

    def test_invalid_with_constant_column(self):
        result = change_case_rules.classify_data_quality(
            historical_row_count=20, duplicate_count=0, constant_columns=["cure_temp_c"]
        )
        self.assertEqual(result, "invalid")

    def test_constant_column_takes_priority_over_row_count(self):
        # Even with plenty of rows, a constant column is still invalid --
        # more data doesn't fix a column with zero variance.
        result = change_case_rules.classify_data_quality(
            historical_row_count=100, duplicate_count=0, constant_columns=["viscosity"]
        )
        self.assertEqual(result, "invalid")


if __name__ == "__main__":
    unittest.main()