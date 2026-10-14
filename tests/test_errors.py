"""Fehlercodes, Exit-Codes und HTTP-Status wie Tabelle SPEC 8.1."""

import unittest

from devc import errors

TABLE = {
    "E_INTERNAL": (1, 500),
    "E_JOB_FAILED": (1, 500),
    "E_USAGE": (2, 400),
    "E_ENGINE": (3, 502),
    "E_NOT_FOUND": (4, 404),
    "E_CONFLICT": (5, 409),
    "E_VALIDATION": (5, 422),
    "E_MISSING_SECRET": (5, 422),
    "E_SECURITY": (6, 403),
}


class ErrorsTest(unittest.TestCase):
    def test_codes_match_spec(self):
        codes = dict(errors.CODES)
        self.assertEqual(codes.pop("E_AUTH")[1], 401)
        self.assertEqual(codes, TABLE)

    def test_error_object(self):
        error = errors.NotFoundError("Container fehlt: x")
        self.assertEqual(error.to_dict(), {"error": "E_NOT_FOUND", "message": "Container fehlt: x", "details": {}})
        self.assertEqual(str(error), "Container fehlt: x")

    def test_validation_details(self):
        error = errors.ValidationError([errors.Finding("L5", "addons", "Addon abap benötigt node")])
        self.assertEqual(
            error.to_dict(),
            {"error": "E_VALIDATION", "message": "Addon abap benötigt node", "details": {"check": "L5", "path": "addons"}},
        )

    def test_validation_prefers_errors_over_warnings(self):
        findings = [
            errors.Finding("W1", "secrets", "Store-Eintrag fehlt", "warning"),
            errors.Finding("L9", "id", "id fehlt", line=3),
        ]
        error = errors.ValidationError(findings)
        self.assertEqual(error.details["check"], "L9")
        self.assertEqual(error.details["line"], 3)
        self.assertEqual(len(error.details["findings"]), 2)
        self.assertTrue(errors.has_errors(findings))
        self.assertFalse(errors.has_errors(findings[:1]))

    def test_missing_secret_and_job(self):
        error = errors.MissingSecretError(["abap.sap_password"])
        self.assertEqual(error.details, {"slots": ["abap.sap_password"]})
        self.assertEqual(errors.JobFailedError("install.sh Exit 1", job="j-4f1a09c2b7de").details, {"job": "j-4f1a09c2b7de"})

    def test_finding_line(self):
        self.assertEqual(errors.Finding("L5", "addons", "x").as_line(), "L5 addons: x")
        self.assertEqual(errors.Finding("L1", "", "x").as_line(), "L1: x")


if __name__ == "__main__":
    unittest.main()
