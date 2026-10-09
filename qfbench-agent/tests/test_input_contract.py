import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent.contracts import instruction_contract
from agent.task_reader import read_task, describe_files
from agent.workspace import validate_outputs, output_tree_bytes, OUTPUT_LIMIT, REPORT_RESERVE


class InputContractTests(unittest.TestCase):
    def task(self, directory, instruction):
        root = Path(directory) / "input"
        root.mkdir()
        (root / "instruction.md").write_text(instruction, encoding="utf-8")
        (root / "card.toml").write_text('[agent]\ntimeout_sec=20\n[contamination]\ncanary_guid="secret-canary"\n', encoding="utf-8")
        return root

    def test_required_files_independent_of_model_and_input_references(self):
        text = "Read input.csv. Write `result.csv` with columns exactly `id`, `value` in this order.\nSave /app/output/summary.json with top-level keys `total`.\nOptional: create debug.csv.\nDo not write reward.json."
        contract = instruction_contract(text)
        self.assertEqual([item["path"] for item in contract["required_artifacts"]], ["result.csv", "summary.json"])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "result.csv").write_text("id,value\na,1\n")
            with self.assertRaisesRegex(ValueError, "omitted"):
                validate_outputs(out, ["result.csv"], contract)
            (out / "summary.json").write_text('{"wrong": 1}')
            with self.assertRaisesRegex(ValueError, "keys"):
                validate_outputs(out, ["result.csv", "summary.json"], contract)

    def test_explicit_column_order_extra_and_finite(self):
        contract = instruction_contract("Write result.csv with columns exactly `id`, `value` in this order; all numeric values must be finite.")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for content, error in [("id,value,extra\na,1,0\n", "extra"), ("value,id\n1,a\n", "order"), ("id,value\na,NaN\n", "finite")]:
                (out / "result.csv").write_text(content)
                with self.subTest(content=content), self.assertRaisesRegex(ValueError, error):
                    validate_outputs(out, ["result.csv"], contract)
            (out / "result.csv").write_text("id,value\na,1.000\n")
            self.assertEqual(validate_outputs(out, ["result.csv"], contract), ["result.csv"])

    def test_units_rounding_remain_explicit_unchecked_text(self):
        contract = instruction_contract("Write result.json.\nValues use basis point units, rounded to 3 decimals; preserve input row order.")
        self.assertTrue(contract["unchecked_statements"])
        self.assertNotIn("precision", contract["required_artifacts"][0])

    def test_schema_words_are_not_columns_and_nested_outputs_keep_scope(self):
        text = "## Output\nWrite result.csv (no index column), sorted by `date`. Exactly two columns:\n1. `date`\n2. `net_return`\n### 1. `summary.json`\n## Input\nRead prices.csv."
        contract = instruction_contract(text)
        self.assertEqual([item["path"] for item in contract["required_artifacts"]], ["result.csv", "summary.json"])
        self.assertEqual(contract["required_artifacts"][0]["columns"], [])
        text = "Write result.csv with columns `id`, `value`. Use `float64` for values.\nWrite result.csv; all numeric values must be finite.\nWrite result.csv."
        item = instruction_contract(text)["required_artifacts"][0]
        self.assertEqual(item["columns"], ["id", "value"])
        self.assertTrue(item["finite"])

    def test_output_section_input_references_are_not_deliverables(self):
        text = "## Output\nWrite result.csv using prices.csv as the input.\nUse the rolling window configured in params.json.\nTime to expiry uses ACT/365 between spot.json.quote_ts and expiry.\n### 1. `summary.json`\n"
        contract = instruction_contract(text)
        self.assertEqual([item["path"] for item in contract["required_artifacts"]], ["result.csv", "summary.json"])
        # The same input/output basename remains legal if explicitly requested.
        self.assertEqual(instruction_contract("Read prices.csv. Write prices.csv.")["required_artifacts"][0]["path"], "prices.csv")

    def test_numbered_file_headings_and_save_outputs_section(self):
        text = ("## Input Files\n### File 1: `params.json`\n"
                "## Output\n### File 1: `fft_results.json`\n"
                "Same structure as `params.json`.\n"
                "### File 2: `mc_results.json`\n"
                "## Notes\n### File 3: `example.json`\n"
                "### Step 4: Save Outputs\n#### 1. `summary.json`\n"
                "### Step 5: Review inputs\n#### 1. `returns.csv`\n")
        contract = instruction_contract(text)
        self.assertEqual([item["path"] for item in contract["required_artifacts"]],
                         ["fft_results.json", "mc_results.json", "summary.json"])

    def test_partial_column_list_does_not_become_exact_and_explicit_keys_can(self):
        contract = instruction_contract("Write r.csv with columns exactly `id`, value.")
        self.assertFalse(contract["required_artifacts"][0]["exact_columns"])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "r.csv").write_text("id,value\na,1\n")
            validate_outputs(out, ["r.csv"], contract)
            contract = instruction_contract("Write r.json with top-level keys exactly `total`, `count`.")
            (out / "r.json").write_text('{"total":1,"count":1,"extra":0}')
            with self.assertRaisesRegex(ValueError, "extra top-level"):
                validate_outputs(out, ["r.json"], contract)

    def test_json_nonfinite_and_csv_malformed_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for data in ('{"a": NaN}', '{"a": Infinity}', '{"a": 1e999}', '{"a":'):
                (out / "r.json").write_text(data)
                with self.subTest(data=data), self.assertRaises(ValueError):
                    validate_outputs(out, ["r.json"])
            (out / "r.csv").write_text("a,b\n1\n")
            with self.assertRaisesRegex(ValueError, "field count"):
                validate_outputs(out, ["r.csv"])

    def test_complete_tree_limit_counts_undeclared_and_internal_files(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / ".agent").mkdir()
            with (out / ".agent" / "large").open("wb") as handle:
                handle.truncate(OUTPUT_LIMIT)
            self.assertEqual(output_tree_bytes(out), OUTPUT_LIMIT)
            (out / "r.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                output_tree_bytes(out)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with (out / "r.txt").open("wb") as handle:
                handle.truncate(OUTPUT_LIMIT - REPORT_RESERVE + 1)
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                validate_outputs(out, ["r.txt"])

    def test_structured_csv_json_samples_and_canary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.task(temp, "Read data.csv and config.json. Write result.json.")
            (root / "data.csv").write_text("id,value\na,1\nb,3\n")
            (root / "config.json").write_text('{"secret-canary": {"items": [1,2]}}')
            (root / "checks").mkdir()
            (root / "checks" / "secret.json").write_text('{"hidden": true}')
            items = describe_files(read_task(root))
            data = next(item for item in items if item["path"] == "data.csv")["inspection"]
            self.assertEqual(data["columns"], ["id", "value"])
            self.assertEqual(data["sample_numeric"][0]["max"], 3)
            self.assertNotIn("secret-canary", json.dumps(items))
            self.assertNotIn("hidden", json.dumps(items))
            self.assertIn("not full-file", data["stats_scope"])

    def test_complete_supplementary_markdown_keeps_late_formula_and_redacts_canary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.task(temp, "Read formulas.md for the exact test formulas. Write result.json.")
            formulas = ("# Formulas\n" + "Use only the observations in the rolling window.\n" * 50
                        + "Conditional coverage: LR_cc = LR_uc + LR_ind; use chi-square with 2 degrees of freedom.\n"
                        + "secret-canary\n")
            self.assertGreater(formulas.index("Conditional coverage"), 2000)
            (root / "formulas.md").write_text(formulas, encoding="utf-8")
            inspection = next(item["inspection"] for item in describe_files(read_task(root))
                              if item["path"] == "formulas.md")
            self.assertEqual(inspection["preview"], formulas.replace("secret-canary", "[REDACTED]"))
            self.assertTrue(inspection["preview_complete"])

    def test_large_markdown_keeps_incomplete_prefix_within_context_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.task(temp, "Read the Markdown guidance files. Write result.json.")
            for index in range(24):
                (root / f"guidance-{index:02d}.md").write_text("# Guidance\n" + "bounded text " * 8000,
                                                             encoding="utf-8")
            inspections = [item["inspection"] for item in describe_files(read_task(root))
                           if "inspection" in item]
            previews = [item for item in inspections if "preview" in item]
            self.assertGreater(len(previews), 1)
            self.assertTrue(all(item["preview"].startswith("# Guidance") for item in previews))
            self.assertTrue(all(item["preview_complete"] is False for item in previews))
            self.assertTrue(all(len(json.dumps(item, ensure_ascii=False)) <= 6000 for item in inspections))
            self.assertLessEqual(sum(len(json.dumps(item, ensure_ascii=False)) for item in previews), 32000)
            self.assertLess(len(previews), 24)

    def test_malformed_and_oversized_inputs_do_not_abort_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.task(temp, "Read large.json bad.json bad.parquet. Write result.json.")
            (root / "large.json").write_text('["' + 'x' * 100000 + '"]')
            (root / "bad.json").write_text('{broken')
            (root / "bad.parquet").write_bytes(b'x')
            items = {item["path"]: item for item in describe_files(read_task(root))}
            self.assertIn("bounded", items["large.json"]["inspection"]["inspection"])
            self.assertEqual(items["bad.json"]["inspection"]["status"], "unavailable")
            self.assertEqual(items["bad.parquet"]["inspection"]["status"], "unavailable")
            self.assertLess(len(json.dumps(items)), 10000)

    def test_xlsx_schema_and_zip_bomb_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.task(temp, "Read data.xlsx and bomb.xlsx. Write result.json.")
            with zipfile.ZipFile(root / "data.xlsx", "w") as package:
                package.writestr("xl/workbook.xml", '<workbook xmlns="urn:x"><sheets><sheet name="Data"/></sheets></workbook>')
                package.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="urn:x"><sheetData><row><c t="inlineStr"><is><t>price</t></is></c><c><v>3</v></c></row></sheetData></worksheet>')
            with zipfile.ZipFile(root / "bomb.xlsx", "w", compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr("xl/workbook.xml", 'x' * (2 * 1024 * 1024 + 1))
            items = {item["path"]: item for item in describe_files(read_task(root))}
            self.assertEqual(items["data.xlsx"]["inspection"]["sheet_names"], ["Data"])
            self.assertIn("expanded", items["bomb.xlsx"]["inspection"]["inspection"])

    def test_parquet_footer_schema_when_available(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("PyArrow verified in the Linux image")
        with tempfile.TemporaryDirectory() as temp:
            root = self.task(temp, "Read data.parquet. Write result.json.")
            for suffix in (".parquet", ".pqt", ".pq"):
                pq.write_table(pa.table({"id": [1, 2], "value": [3., 4.]}), root / ("data" + suffix))
            items = {item["path"]: item for item in describe_files(read_task(root))}
            for suffix in (".parquet", ".pqt", ".pq"):
                self.assertEqual(items["data" + suffix]["inspection"]["rows"], 2)
                self.assertIn("value", items["data" + suffix]["inspection"]["schema"])


if __name__ == "__main__":
    unittest.main()
