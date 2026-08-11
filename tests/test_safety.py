from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from organizer import extractors
from organizer.extractors import ExtractionLimitError, _validate_office_archive


class ArchiveLimitTests(unittest.TestCase):
    def test_rejects_oversized_office_member(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "large.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", b"x" * 33)
        previous_limit = extractors.MAX_OFFICE_MEMBER_BYTES
        self.addCleanup(setattr, extractors, "MAX_OFFICE_MEMBER_BYTES", previous_limit)
        extractors.MAX_OFFICE_MEMBER_BYTES = 32
        with self.assertRaises(ExtractionLimitError):
            _validate_office_archive(path)


if __name__ == "__main__":
    unittest.main()
