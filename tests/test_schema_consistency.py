from pathlib import Path
import re
import unittest
import comic_pipeline as cp

class TestSchemaConsistency(unittest.TestCase):
    def test_all_schema_version_references_match_constant(self):
        root = Path(__file__).resolve().parents[1]
        schema_const = cp.SCHEMA_VERSION

        pattern = re.compile(r'(?:schema_version[ =:]+|schema\s+v|Schema\s+v)(\d+)', re.IGNORECASE)
        checked_count = 0

        # Scan all markdown documentation and template files
        for p in root.rglob('*.md'):
            if any(part in ('.git', '__pycache__', '.pytest_cache', '.venv', 'validation') for part in p.parts):
                continue
            text = p.read_text(encoding='utf-8')
            for match in pattern.finditer(text):
                ver = int(match.group(1))
                checked_count += 1
                self.assertEqual(
                    ver, schema_const,
                    f"Inconsistent schema version found in {p.relative_to(root)}: {ver} != {schema_const}"
                )

        self.assertGreater(checked_count, 0, "Should have found schema version references to verify")

    def test_canonical_template_files_exist_and_accessible(self):
        # Verify that all templates rendered by pipeline exist in canonical assets/templates/
        book_templates = [
            'kanban_block.md', 'overview.md', 'structure.md', 'worldview.md',
            'characters.md', 'art_direction.md', 'progress.md', 'readme.md'
        ]
        volume_templates = [
            'info.md', 'status.md', 'structure.md', 'commands.md',
            'notes.md', 'deliverables.md', 'status_block.md', 'readme.md'
        ]
        for t in book_templates:
            p = cp.find_template_file('book_docs', t)
            self.assertTrue(p.is_file(), f"Book template missing: {t}")

        for t in volume_templates:
            p = cp.find_template_file('volume_docs', t)
            self.assertTrue(p.is_file(), f"Volume template missing: {t}")
