"""Check standalone local lessons and genuine saved Notebook outputs."""

import contextlib
import base64
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import nbformat
import numpy as np
import pyqpanda3.qcloud
from pyqpanda3.core import CPUQVM, QProg, X, measure


ROOT = Path(__file__).resolve().parents[1]


TOPICS = {
    1: ("01_circuit_construction", "02_local_simulation", "03_quantum_cloud",
        "04_teleportation", "05_state_encoding", "06_quantum_fourier_transform"),
    2: ("01_hamiltonian", "02_variational_circuits", "03_noise_simulation",
        "04_quantum_adder", "05_grover_search"),
}


def notebook(day, topic):
    return json.loads((ROOT / f"notebooks/day{day}/{topic}.ipynb").read_text(encoding="utf8"))["cells"]


def execute(cells):
    namespace = {}
    # The explicit boundary is remote quantum work, never the local simulator.
    with patch.object(pyqpanda3.qcloud, "QCloudService", side_effect=AssertionError("Unexpected cloud access")), contextlib.redirect_stdout(io.StringIO()):
        for index, cell in enumerate(cells):
            if cell["cell_type"] == "code" and "visualization" not in cell["metadata"].get("tags", []):
                exec(compile("".join(cell["source"]), f"notebook-cell-{index}", "exec"), namespace)
    return namespace


class LocalLessonTests(unittest.TestCase):
    def test_three_bell_constructions_measure_two_entangled_qubits(self):
        cells = notebook(1, "01_circuit_construction")
        namespace = {}
        constructions = 0
        for cell in cells:
            if cell["cell_type"] != "code" or "visualization" in cell["metadata"].get("tags", []):
                continue
            source = "".join(cell["source"])
            with contextlib.redirect_stdout(io.StringIO()):
                exec(source, namespace)
            if "bell_state_prog =" in source:
                constructions += 1
                machine = CPUQVM()
                machine.run(namespace["bell_state_prog"], 1024)
                counts = machine.result().get_counts()
                self.assertEqual(set(counts), {"00", "11"})
                self.assertEqual(sum(counts.values()), 1024)
                self.assertTrue(0.35 < counts["00"] / 1024 < 0.65)
        self.assertEqual(constructions, 3)

    def test_bit_order_uses_classical_mapping_not_measurement_argument_order(self):
        cases = ((0, [0, 1, 2], [0, 1, 2], "001"),
                 (2, [0, 1, 2], [0, 1, 2], "100"),
                 (0, [0, 1, 2], [2, 1, 0], "100"),
                 (0, [2, 0, 1], [2, 0, 1], "001"))
        for excited, qubits, classical, expected in cases:
            with self.subTest(case=(excited, qubits, classical)):
                prog = QProg(3) << X(excited) << measure(qubits, classical)
                machine = CPUQVM()
                machine.run(prog, 32)
                self.assertEqual(machine.result().get_counts(), {expected: 32})
        note = "\n".join("".join(cell["source"]) for cell in notebook(1, "04_teleportation") if cell["cell_type"] == "markdown")
        self.assertIn("c_2c_1c_0", note)
        self.assertIn("q_2q_1q_0", note)

    def test_noisy_bell_reports_even_parity_not_fidelity(self):
        cells = notebook(2, "03_noise_simulation")
        namespace = execute(cells)
        self.assertEqual(sum(namespace["counts"].values()), 100000)
        self.assertTrue(0 <= namespace["p00"] + namespace["p11"] <= 1)
        self.assertTrue(any("Even-parity probability" in "".join(cell["source"]) for cell in cells))
        # Z-basis probabilities cannot distinguish opposite relative phases.
        plus = np.array([1, 0, 0, 1]) / np.sqrt(2)
        minus = np.array([1, 0, 0, -1]) / np.sqrt(2)
        self.assertAlmostEqual(float(np.abs(minus[[0, 3]]) ** 2 @ np.ones(2)), 1)
        self.assertAlmostEqual(float(abs(np.vdot(plus, minus)) ** 2), 0)

    def test_default_adder_runs_twelve_qubits_and_returns_eight(self):
        cells = notebook(2, "04_quantum_adder")
        namespace = execute(cells)
        self.assertEqual((namespace["a"], namespace["b"]), (3, 5))
        self.assertEqual(4 * max(namespace["a"].bit_length(), namespace["b"].bit_length()), 12)
        self.assertEqual(namespace["result"], {"1000": 100})
        for a, b in ((1, 1), (7, 7), (0, 5)):
            with self.subTest(a=a, b=b):
                machine = CPUQVM()
                machine.run(namespace["adder_circuit"](a, b), 16)
                counts = machine.result().get_counts()
                self.assertEqual({int(state, 2) for state in counts}, {a + b})
                self.assertEqual(sum(counts.values()), 16)

    def test_each_topic_runs_independently_without_cloud_access(self):
        for day, topics in TOPICS.items():
            for topic in topics:
                with self.subTest(day=day, topic=topic):
                    namespace = execute(notebook(day, topic))
                    self.assertFalse(namespace.get("RUN_ON_QCLOUD", False))
                    if topic == "04_teleportation":
                        self.assertEqual(namespace["cnt_0"] + namespace["cnt_1"], namespace["shots"])
                        self.assertAlmostEqual(namespace["cnt_0"] / namespace["shots"], 0.25, delta=0.04)
                    elif topic == "05_grover_search":
                        self.assertGreater(namespace["local_counts"].get(namespace["dst"], 0), 800)

    def test_saved_notebooks_have_complete_execution_and_embedded_images(self):
        expected = {ROOT / f"notebooks/day{day}/{topic}.ipynb" for day, topics in TOPICS.items() for topic in topics}
        self.assertEqual(set((ROOT / "notebooks").glob("day*/*.ipynb")), expected)
        self.assertEqual(list(ROOT.glob("*.ipynb")), [])
        for path in expected:
            with self.subTest(path=path.name):
                nb = nbformat.read(path, as_version=4)
                nbformat.validate(nb)
                code = [cell for cell in nb.cells if cell.cell_type == "code"]
                self.assertEqual([cell.execution_count for cell in code], list(range(1, len(code) + 1)))
                images = []
                for cell in code:
                    for output in cell.outputs:
                        self.assertNotEqual(output.output_type, "error")
                        if "image/png" in output.get("data", {}):
                            images.append(base64.b64decode(output.data["image/png"]))
                self.assertGreaterEqual(len(images), 1)
                self.assertTrue(all(image.startswith(b"\x89PNG\r\n\x1a\n") for image in images))

    def test_readme_links_every_topic_and_navigation_resolves(self):
        import re
        readme = (ROOT / "README.md").read_text(encoding="utf8")
        self.assertIn("## 4. 按主题学习 Notebook", readme)
        for day, topics in TOPICS.items():
            for topic in topics:
                self.assertIn(f"notebooks/day{day}/{topic}.ipynb", readme)
                for cell in notebook(day, topic):
                    if cell["cell_type"] != "markdown":
                        continue
                    for attachment in re.findall(r'attachment:([^\s)"<>]+)', "".join(cell["source"])):
                        self.assertIn(attachment, cell.get("attachments", {}))

    def test_markdown_attachments_render_to_inline_images(self):
        from html.parser import HTMLParser
        from nbconvert import HTMLExporter

        class ImageSources(HTMLParser):
            def __init__(self):
                super().__init__()
                self.sources = []

            def handle_starttag(self, tag, attrs):
                if tag == "img":
                    self.sources.append(dict(attrs).get("src", ""))

        exporter = HTMLExporter()
        for day, topics in TOPICS.items():
            for topic in topics:
                path = ROOT / f"notebooks/day{day}/{topic}.ipynb"
                cells = [cell for cell in nbformat.read(path, as_version=4).cells if cell.get("attachments")]
                if not cells:
                    continue
                with self.subTest(day=day, topic=topic):
                    nb = nbformat.v4.new_notebook(cells=cells)
                    html, _ = exporter.from_notebook_node(nb)
                    parsed = ImageSources()
                    parsed.feed(html)
                    self.assertEqual(len(parsed.sources), sum(len(cell["attachments"]) for cell in cells))
                    self.assertTrue(all(source.startswith("data:image/") for source in parsed.sources))


if __name__ == "__main__":
    unittest.main()
