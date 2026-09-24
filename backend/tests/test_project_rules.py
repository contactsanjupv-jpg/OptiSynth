"""
Run with: python3 -m unittest backend.tests.test_project_rules -v
"""
import unittest

from backend.app.services.project_rules import check_structural_edit_allowed
from backend.app.schemas.errors import ValidationError


class TestStructuralEditRule(unittest.TestCase):
    def test_allows_non_structural_fields_regardless_of_experiment_count(self):
        for count in (0, 1, 50):
            check_structural_edit_allowed({"name": "New Name"}, count)
            check_structural_edit_allowed({"target_value": 25.0}, count)
            check_structural_edit_allowed({"name": "x", "objective": "y", "target_value": 1.0}, count)

    def test_allows_structural_fields_when_no_experiments_exist(self):
        check_structural_edit_allowed({"feature_columns": ["a", "b"]}, 0)
        check_structural_edit_allowed({"constraints": []}, 0)
        check_structural_edit_allowed({"feature_columns": ["a"], "constraints": []}, 0)

    def test_blocks_feature_columns_change_when_experiments_exist(self):
        with self.assertRaises(ValidationError) as ctx:
            check_structural_edit_allowed({"feature_columns": ["a", "b"]}, 5)
        self.assertEqual(ctx.exception.field, "feature_columns")

    def test_blocks_constraints_change_when_experiments_exist(self):
        with self.assertRaises(ValidationError) as ctx:
            check_structural_edit_allowed({"constraints": []}, 1)
        self.assertEqual(ctx.exception.field, "constraints")

    def test_blocks_mixed_update_containing_a_structural_field(self):
        with self.assertRaises(ValidationError):
            check_structural_edit_allowed({"name": "New Name", "feature_columns": ["a"]}, 10)

    def test_error_message_never_exposes_internal_details(self):
        with self.assertRaises(ValidationError) as ctx:
            check_structural_edit_allowed({"constraints": []}, 3)
        msg = ctx.exception.message
        self.assertNotIn("Traceback", msg)
        self.assertIn("new project", msg.lower())


if __name__ == "__main__":
    unittest.main()