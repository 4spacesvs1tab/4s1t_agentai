"""
Subphase 2E tests — data_read + python_execute skills.

data_read tests run entirely offline using temp files.
python_execute tests require Docker — skipped when Docker is not available
or when SKILLS_TEST_DOCKER=0 (default offline-safe).

Run all:
    pytest src/tests/test_skills_data.py -v

Run with Docker:
    SKILLS_TEST_DOCKER=1 pytest src/tests/test_skills_data.py -v
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_docker_enabled = os.environ.get("SKILLS_TEST_DOCKER", "0") == "1"
skip_no_docker = pytest.mark.skipif(
    not _docker_enabled,
    reason="Set SKILLS_TEST_DOCKER=1 to run Docker-dependent tests",
)


def _docker_available() -> bool:
    """Check that 'docker info' succeeds (daemon is running)."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_handler(skill_name: str, params: dict, extra_env: dict | None = None) -> dict:
    handler = _SKILLS_DIR / skill_name / "handler.py"
    env = {**os.environ, **(extra_env or {})}
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "input.json"
        out = Path(tmpdir) / "output.json"
        inp.write_text(json.dumps({
            "skill_name": skill_name,
            "parameters": params,
            "calling_agent_type": "data_agent",
        }))
        subprocess.run(
            [sys.executable, str(handler), str(inp), str(out)],
            env=env,
            capture_output=True,
        )
        return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# data_read — CSV
# ---------------------------------------------------------------------------

class TestDataReadCSV:
    def test_reads_csv_columns_and_rows(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        csv_file = data_dir / "sales.csv"
        csv_file.write_text("product,quantity,price\napple,10,1.5\nbanana,5,0.75\n")

        result = _run_handler(
            "data_read",
            {"path": "sales.csv", "sample_rows": 5},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        assert result["success"] is True, result.get("error")
        r = result["result"]
        assert r["file_format"] == "csv"
        assert r["columns"] == ["product", "quantity", "price"]
        assert r["row_count"] == 2
        assert len(r["sample"]) == 2
        assert r["sample"][0]["product"] == "apple"

    def test_dtype_inference(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        csv_file = data_dir / "nums.csv"
        csv_file.write_text("name,count,ratio\nalpha,42,3.14\nbeta,7,2.71\n")

        result = _run_handler(
            "data_read",
            {"path": "nums.csv"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        r = result["result"]
        assert r["dtypes"]["count"] == "integer"
        assert r["dtypes"]["ratio"] == "float"
        assert r["dtypes"]["name"] == "string"

    def test_sample_rows_capped(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        csv_file = data_dir / "big.csv"
        # 50 rows
        with open(csv_file, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "val"])
            for i in range(50):
                w.writerow([i, i * 2])

        result = _run_handler(
            "data_read",
            {"path": "big.csv", "sample_rows": 5},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        r = result["result"]
        assert r["row_count"] == 50
        assert len(r["sample"]) == 5

    def test_path_traversal_denied(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        result = _run_handler(
            "data_read",
            {"path": "../../etc/passwd"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        assert result["success"] is False
        assert "traversal" in result["error"].lower() or "denied" in result["error"].lower()

    def test_missing_file_returns_error(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        result = _run_handler(
            "data_read",
            {"path": "nonexistent.csv"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_unsupported_format_error(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "file.parquet").write_bytes(b"PAR1")

        result = _run_handler(
            "data_read",
            {"path": "file.parquet"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        assert result["success"] is False
        assert "unsupported" in result["error"].lower()


# ---------------------------------------------------------------------------
# data_read — JSON
# ---------------------------------------------------------------------------

class TestDataReadJSON:
    def test_reads_json_array(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "records.json").write_text(
            json.dumps([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
        )

        result = _run_handler(
            "data_read",
            {"path": "records.json"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        assert result["success"] is True
        r = result["result"]
        assert r["file_format"] == "json"
        assert r["row_count"] == 2
        assert "id" in r["columns"]
        assert "name" in r["columns"]

    def test_reads_jsonl(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lines = [
            json.dumps({"x": 1, "y": "a"}),
            json.dumps({"x": 2, "y": "b"}),
            json.dumps({"x": 3, "y": "c"}),
        ]
        (data_dir / "events.jsonl").write_text("\n".join(lines) + "\n")

        result = _run_handler(
            "data_read",
            {"path": "events.jsonl"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        r = result["result"]
        assert r["file_format"] == "jsonl"
        assert r["row_count"] == 3
        assert r["columns"] == ["x", "y"]


# ---------------------------------------------------------------------------
# data_read — Excel
# ---------------------------------------------------------------------------

class TestDataReadExcel:
    def test_reads_excel_file(self, tmp_path):
        pytest.importorskip("openpyxl")
        import openpyxl

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["city", "population"])
        ws.append(["Warsaw", 1800000])
        ws.append(["Krakow", 800000])
        wb.save(data_dir / "cities.xlsx")

        result = _run_handler(
            "data_read",
            {"path": "cities.xlsx"},
            extra_env={"FILE_READ_BASE_DIR": str(data_dir)},
        )
        assert result["success"] is True, result.get("error")
        r = result["result"]
        assert r["file_format"] == "excel"
        assert r["columns"] == ["city", "population"]
        assert r["row_count"] == 2


# ---------------------------------------------------------------------------
# python_execute — meta validation (no Docker required)
# ---------------------------------------------------------------------------

class TestPythonExecuteMeta:
    def test_meta_requires_approval(self):
        meta_path = _SKILLS_DIR / "python_execute" / "meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["requires_approval"] is True

    def test_meta_execution_mode_docker(self):
        meta_path = _SKILLS_DIR / "python_execute" / "meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["execution_mode"] == "docker"

    def test_meta_network_not_allowed(self):
        meta_path = _SKILLS_DIR / "python_execute" / "meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["network_allowed"] is False

    def test_handler_rejects_empty_code(self, tmp_path):
        result = _run_handler("python_execute", {"code": ""})
        assert result["success"] is False
        assert "code" in result["error"].lower()


# ---------------------------------------------------------------------------
# python_execute — Docker integration tests
# ---------------------------------------------------------------------------

@skip_no_docker
class TestPythonExecuteDocker:
    @classmethod
    def setup_class(cls):
        if not _docker_available():
            pytest.skip("Docker daemon is not running")

    def test_hello_world(self):
        result = _run_handler("python_execute", {"code": 'print("hello from docker")'})
        assert result["success"] is True
        assert "hello from docker" in result["result"]["stdout"]
        assert result["result"]["exit_code"] == 0

    def test_pandas_available(self):
        code = (
            "import pandas as pd\n"
            "df = pd.DataFrame({'a': [1, 2, 3]})\n"
            "print(df.to_string())\n"
        )
        result = _run_handler("python_execute", {"code": code})
        assert result["success"] is True
        assert result["result"]["exit_code"] == 0
        assert "a" in result["result"]["stdout"]

    def test_write_output_file(self):
        code = (
            "with open('/output/result.txt', 'w') as f:\n"
            "    f.write('42\\n')\n"
        )
        result = _run_handler("python_execute", {"code": code})
        assert result["success"] is True
        assert "result.txt" in result["result"]["output_files"]

    def test_no_network_access(self):
        code = (
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('http://example.com', timeout=3)\n"
            "    print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print(f'BLOCKED: {e}')\n"
        )
        result = _run_handler("python_execute", {"code": code})
        assert result["success"] is True
        assert "CONNECTED" not in result["result"]["stdout"]

    def test_syntax_error_captured_in_stderr(self):
        code = "def foo(:\n    pass\n"
        result = _run_handler("python_execute", {"code": code})
        assert result["success"] is True  # handler succeeded
        assert result["result"]["exit_code"] != 0
        assert result["result"]["stderr"] != ""

    def test_path_traversal_in_data_files_denied(self):
        result = _run_handler(
            "python_execute",
            {"code": "print('ok')", "data_files": ["../../etc/passwd"]},
        )
        assert result["success"] is False
        assert "traversal" in result["error"].lower() or "denied" in result["error"].lower()
