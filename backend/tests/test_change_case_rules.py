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


class TestDataQualityMetricsAndRankingGate(unittest.TestCase):
    def _row(self, v, s, t):
        return {"features": {"viscosity": v, "solids_pct": s}, "target_value": t}

    def test_metrics_count_exact_duplicates_only(self):
        rows = [self._row(1, 2, 3), self._row(1, 2, 3), self._row(1, 2, 4), self._row(2, 2, 3)]
        dups, const = change_case_rules.compute_data_quality_metrics(rows, ["viscosity", "solids_pct"])
        self.assertEqual(dups, 1)  # only the exact repeat; same features with a different target is not a duplicate
        self.assertEqual(const, ["solids_pct"])

    def test_metrics_no_constant_columns(self):
        rows = [self._row(1, 2, 3), self._row(2, 3, 4)]
        dups, const = change_case_rules.compute_data_quality_metrics(rows, ["viscosity", "solids_pct"])
        self.assertEqual((dups, const), (0, []))

    def test_ranking_gate_blocks_invalid_and_insufficient(self):
        for status in ("invalid", "insufficient"):
            with self.assertRaises(ValidationError):
                change_case_rules.check_data_quality_allows_ranking(status)

    def test_report_gate_blocks_invalid_but_not_insufficient_or_valid(self):
        with self.assertRaises(ValidationError):
            change_case_rules.check_can_generate_report("invalid")
        change_case_rules.check_can_generate_report("insufficient")  # allowed; report states insufficient evidence
        change_case_rules.check_can_generate_report("valid")

    def test_ranking_gate_allows_valid(self):
        change_case_rules.check_data_quality_allows_ranking("valid")  # must not raise


class TestClassifyDomainCoverage(unittest.TestCase):
    """Priority 5. NOTE: the 10% edge margin is a HEURISTIC business
    threshold, not a scientifically derived one -- these tests pin the
    definition, they do not validate the number."""

    WITHIN = "within_historical_domain"
    NEAR = "near_edge_of_domain"
    OUTSIDE = "outside_historical_domain"

    def test_default_margin_is_ten_percent(self):
        self.assertEqual(change_case_rules.DOMAIN_EDGE_MARGIN_PCT, 0.10)

    def test_within_including_exact_bounds(self):
        for v in (0, 5, 10):
            self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10), self.WITHIN)

    def test_near_edge_both_sides_and_exact_margin_boundary(self):
        # range 0..10 -> margin 1.0
        for v in (10.5, 11.0, -0.5, -1.0):
            self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10), self.NEAR)

    def test_outside_beyond_margin_both_sides(self):
        for v in (11.001, 25, -1.001, -50):
            self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10), self.OUTSIDE)

    def test_sensitivity_to_margin_is_explicit_and_adjustable(self):
        # The SAME value classifies differently as the heuristic margin changes --
        # proving the threshold is a tunable business setting, not a fact.
        v = 10.5  # 0.5 beyond max of a 0..10 range (5% of range)
        self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10, edge_margin_pct=0.10), self.NEAR)
        self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10, edge_margin_pct=0.02), self.OUTSIDE)
        self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10, edge_margin_pct=0.0), self.OUTSIDE)
        self.assertEqual(change_case_rules.classify_domain_coverage(v, 0, 10, edge_margin_pct=0.50), self.NEAR)

    def test_zero_width_range_only_the_constant_is_within(self):
        self.assertEqual(change_case_rules.classify_domain_coverage(5, 5, 5), self.WITHIN)
        self.assertEqual(change_case_rules.classify_domain_coverage(5.001, 5, 5), self.OUTSIDE)

    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            change_case_rules.classify_domain_coverage(1, 0, 10, edge_margin_pct=-0.1)
        with self.assertRaises(ValueError):
            change_case_rules.classify_domain_coverage(1, 10, 0)

    def test_compute_historical_ranges(self):
        rows = [{"features": {"a": 1, "b": 10}}, {"features": {"a": 3, "b": 5}}]
        self.assertEqual(change_case_rules.compute_historical_ranges(rows, ["a", "b"]),
                         {"a": (1, 3), "b": (5, 10)})
        with self.assertRaises(ValueError):
            change_case_rules.compute_historical_ranges([], ["a"])

    def test_candidate_status_is_worst_feature(self):
        ranges = {"a": (0, 10), "b": (0, 10), "c": (0, 10)}
        cov = change_case_rules.classify_candidate_domain_coverage({"a": 5, "b": 10.5, "c": 5}, ranges)
        self.assertEqual(cov["status"], self.NEAR)
        cov = change_case_rules.classify_candidate_domain_coverage({"a": 5, "b": 10.5, "c": 99}, ranges)
        self.assertEqual(cov["status"], self.OUTSIDE)
        self.assertEqual(cov["features"]["a"]["status"], self.WITHIN)
        self.assertEqual(cov["features"]["b"]["status"], self.NEAR)
        self.assertEqual(cov["features"]["c"]["status"], self.OUTSIDE)
        cov = change_case_rules.classify_candidate_domain_coverage({"a": 1, "b": 2, "c": 3}, ranges)
        self.assertEqual(cov["status"], self.WITHIN)

    def test_note_never_claims_scientific_derivation(self):
        note = change_case_rules.domain_coverage_note()
        self.assertIn("heuristic", note)
        self.assertIn("not a scientifically validated", note)
        self.assertIn("10%", note)
        self.assertIn("not derived from data or theory", note)


if __name__ == "__main__":
    unittest.main()