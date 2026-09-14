"""
Unit tests for textract_processor.py (Virajith - S3 Ingestion & Textract)
"""

import io
import json
import unittest
import zipfile
from unittest.mock import MagicMock

from textract_processor import (
    parse_s3_resume_key,
    validate_file_metadata,
    extract_text_from_textract_blocks,
    extract_text_from_docx,
    extract_text_from_document,
    build_extracted_text_payload,
    is_probable_section_header,
)


class TestTextractProcessor(unittest.TestCase):

    def test_parse_s3_resume_key_standard(self):
        key = "jobs/job_2026_001/resumes/cand_0001/source.pdf"
        parsed = parse_s3_resume_key(key)
        self.assertEqual(parsed["job_id"], "job_2026_001")
        self.assertEqual(parsed["candidate_id"], "cand_0001")
        self.assertEqual(parsed["filename"], "source.pdf")
        self.assertEqual(parsed["extension"], ".pdf")
        self.assertTrue(parsed["valid_structure"])

    def test_parse_s3_resume_key_docx(self):
        key = "/jobs/job_devops/resumes/cand_8899/applicant_cv.docx"
        parsed = parse_s3_resume_key(key)
        self.assertEqual(parsed["job_id"], "job_devops")
        self.assertEqual(parsed["candidate_id"], "cand_8899")
        self.assertEqual(parsed["filename"], "applicant_cv.docx")
        self.assertEqual(parsed["extension"], ".docx")

    def test_parse_s3_resume_key_invalid_inputs(self):
        with self.assertRaises(ValueError):
            parse_s3_resume_key("")

    def test_validate_file_metadata_valid(self):
        valid, err = validate_file_metadata(1024 * 500, "resume.pdf", "application/pdf")
        self.assertTrue(valid)
        self.assertIsNone(err)

        valid_docx, _ = validate_file_metadata(1024 * 200, "jobs/job_1/resumes/c1/cv.docx")
        self.assertTrue(valid_docx)

    def test_validate_file_metadata_invalid_size(self):
        # 0 bytes
        valid, err = validate_file_metadata(0, "resume.pdf")
        self.assertFalse(valid)
        self.assertIn("empty", err)

        # > 10MB
        valid, err = validate_file_metadata(15 * 1024 * 1024, "resume.pdf")
        self.assertFalse(valid)
        self.assertIn("exceeds maximum allowed limit", err)

    def test_validate_file_metadata_invalid_extension(self):
        valid, err = validate_file_metadata(1024, "malicious.exe")
        self.assertFalse(valid)
        self.assertIn("Unsupported file extension", err)

    def test_is_probable_section_header(self):
        self.assertTrue(is_probable_section_header("PROFESSIONAL EXPERIENCE"))
        self.assertTrue(is_probable_section_header("EDUCATION"))
        self.assertTrue(is_probable_section_header("Technical Skills"))
        self.assertTrue(is_probable_section_header("WORK HISTORY"))
        self.assertFalse(is_probable_section_header("Software Engineer at Google from 2020 to 2023"))

    def test_extract_text_from_textract_blocks_preserves_sections(self):
        blocks = [
            {
                "BlockType": "LINE",
                "Text": "John Doe",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Top": 0.05, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "john.doe@example.com | (555) 123-4567",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Top": 0.08, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "TECHNICAL SKILLS",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Top": 0.18, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "Python, AWS, DynamoDB, Lambda, Terraform",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Top": 0.22, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "PROFESSIONAL EXPERIENCE",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Top": 0.35, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "Cloud Solutions Architect - Apex Systems",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Top": 0.39, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "EDUCATION",
                "Page": 2,
                "Geometry": {"BoundingBox": {"Top": 0.10, "Left": 0.1, "Height": 0.02}},
            },
            {
                "BlockType": "LINE",
                "Text": "B.S. in Computer Science - State University",
                "Page": 2,
                "Geometry": {"BoundingBox": {"Top": 0.14, "Left": 0.1, "Height": 0.02}},
            },
        ]

        extracted = extract_text_from_textract_blocks(blocks)
        self.assertIn("John Doe", extracted)
        self.assertIn("TECHNICAL SKILLS", extracted)
        self.assertIn("PROFESSIONAL EXPERIENCE", extracted)
        self.assertIn("EDUCATION", extracted)
        # Check that section headers have double newlines before them
        self.assertIn("\n\nTECHNICAL SKILLS", extracted)
        self.assertIn("\n\nPROFESSIONAL EXPERIENCE", extracted)

    def test_extract_text_from_docx(self):
        # Create synthetic docx in memory
        docx_buffer = io.BytesIO()
        with zipfile.ZipFile(docx_buffer, "w") as zf:
            document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Alice Smith</w:t></w:r></w:p>
                <w:p><w:r><w:t>alice@example.com</w:t></w:r></w:p>
                <w:p><w:r><w:t>EXPERIENCE</w:t></w:r></w:p>
                <w:p><w:r><w:t>Software Engineer at Tech Corp</w:t></w:r></w:p>
              </w:body>
            </w:document>"""
            zf.writestr("word/document.xml", document_xml)

        docx_bytes = docx_buffer.getvalue()
        extracted = extract_text_from_docx(docx_bytes)
        self.assertIn("Alice Smith", extracted)
        self.assertIn("alice@example.com", extracted)
        self.assertIn("EXPERIENCE", extracted)
        self.assertIn("Software Engineer at Tech Corp", extracted)

    def test_build_extracted_text_payload(self):
        payload = build_extracted_text_payload(
            job_id="job_001",
            candidate_id="cand_001",
            text="Resume line 1\nResume line 2",
            source_s3_key="jobs/job_001/resumes/cand_001/source.pdf",
            correlation_id="corr_test",
        )
        self.assertEqual(payload["job_id"], "job_001")
        self.assertEqual(payload["candidate_id"], "cand_001")
        self.assertEqual(payload["correlation_id"], "corr_test")
        self.assertEqual(payload["line_count"], 2)
        self.assertEqual(payload["character_count"], len("Resume line 1\nResume line 2"))
        self.assertIn("extracted_at", payload)


if __name__ == "__main__":
    unittest.main()
