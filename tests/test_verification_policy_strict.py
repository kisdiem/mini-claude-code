from __future__ import annotations

import unittest

from mini_cc.verification_policy import VerificationPolicy


class VerificationPolicyStrictTests(unittest.TestCase):
    def test_rejects_fake_success_commands(self) -> None:
        policy = VerificationPolicy()
        for command in ["echo success", "python -c \"print('ok')\"", "true", "exit 0"]:
            result = policy.evaluate_command(command, "exit_code=0\nstdout:\nok\nstderr:\n")
            self.assertFalse(result.is_real_verification, command)
            self.assertFalse(result.passed, command)

    def test_pytest_zero_tests_is_not_meaningful(self) -> None:
        result = VerificationPolicy().evaluate_command("python -m pytest", "exit_code=0\nstdout:\ncollected 0 items\nno tests ran\nstderr:\n")
        self.assertFalse(result.passed)
        self.assertFalse(result.has_meaningful_checks)

    def test_empty_build_success_is_weak_warning(self) -> None:
        result = VerificationPolicy().evaluate_command("npm run build", "exit_code=0\nstdout:\n\nstderr:\n")
        self.assertFalse(result.passed)
        self.assertIn("weak verification evidence", " ".join(result.warnings))


if __name__ == "__main__":
    unittest.main()
