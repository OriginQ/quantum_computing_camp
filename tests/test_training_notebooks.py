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

    def test_qft_appendix_matches_positive_fourier_transform_and_inverse(self):
        from pyqpanda3.quantum_info import StateVector

        namespace = execute(notebook(1, "06_quantum_fourier_transform"))
        rng = np.random.default_rng(20261018)
        for n in range(1, 5):
            with self.subTest(n=n):
                size = 2**n
                indices = np.arange(size)
                expected = np.exp(2j * np.pi * np.outer(indices, indices) / size) / np.sqrt(size)
                circuit = namespace["qft_circuit"](n)
                actual = namespace["circuit_matrix"](circuit, n)
                np.testing.assert_allclose(actual, expected, atol=1e-12)
                reversed_indices = [int(format(k, f"0{n}b")[::-1], 2) for k in indices]
                without_swaps = namespace["circuit_matrix"](namespace["qft_circuit"](n, swaps=False), n)
                np.testing.assert_allclose(without_swaps, expected[reversed_indices], atol=1e-12)
                vector = rng.normal(size=size) + 1j * rng.normal(size=size)
                vector /= np.linalg.norm(vector)
                output = StateVector(vector.tolist()).evolve(circuit)
                np.testing.assert_allclose(output.ndarray(), np.fft.ifft(vector) * np.sqrt(size), atol=1e-12)
                np.testing.assert_allclose(output.evolve(circuit.dagger()).ndarray(), vector, atol=1e-12)
        np.testing.assert_allclose(namespace["periodic_probabilities"],
                                   [0.25, 0, 0.25, 0, 0.25, 0, 0.25, 0], atol=1e-12)

    def test_grover_appendix_sign_target_order_and_multiple_solutions(self):
        from pyqpanda3.core import QProg
        from pyqpanda3.quantum_info import StateVector

        namespace = execute(notebook(2, "05_grover_search"))
        matrix = namespace["grover_program_matrix"]
        oracle = namespace["build_oracle"]
        diffusion = namespace["build_diffusion"]
        first_peak = namespace["grover_first_peak"]
        for n in (2, 3):
            size = 2**n
            qubits = list(range(n))
            initial = np.ones(size, dtype=complex) / np.sqrt(size)
            expected_diffusion = np.eye(size) - 2 * np.outer(initial, initial.conj())
            np.testing.assert_allclose(matrix(diffusion(qubits), n), expected_diffusion, atol=1e-12)
            for target in range(size):
                with self.subTest(n=n, target=target):
                    expected = np.eye(size, dtype=complex)
                    expected[target, target] = -1
                    np.testing.assert_allclose(matrix(oracle(qubits, format(target, f"0{n}b")), n), expected, atol=1e-12)
        qubits = [0, 1, 2]
        for targets in ([1], [1, 6], [0, 2, 5], [0, 1, 2, 3]):
            with self.subTest(targets=targets):
                program = QProg()
                for target in targets:
                    program << oracle(qubits, format(target, "03b"))
                program << diffusion(qubits)
                state = StateVector((np.ones(8, dtype=complex) / np.sqrt(8)).tolist())
                theta = np.arcsin(np.sqrt(len(targets) / 8))
                for r in range(7):
                    probability = np.sum(np.abs(state.ndarray()[targets])**2)
                    self.assertAlmostEqual(float(probability), np.sin((2*r+1)*theta)**2)
                    state = state.evolve(program.to_circuit())
        for size, solutions, rounds in ((8, 1, 2), (4, 1, 1), (8, 2, 1), (8, 4, 0), (8, 8, 0)):
            self.assertEqual(first_peak(size, solutions), rounds)
        for size, solutions in ((8, 0), (8, 9), (0, 0)):
            with self.assertRaises(ValueError):
                first_peak(size, solutions)
        np.testing.assert_allclose(namespace["grover_theory"][:4],
                                   [1/8, 25/32, 121/128, 169/512], atol=1e-12)

    def test_qft_derivation_table_renders_with_ket_in_header(self):
        from nbconvert import HTMLExporter

        path = ROOT / "notebooks/day1/06_quantum_fourier_transform.ipynb"
        nb = nbformat.read(path, as_version=4)
        cells = [cell for cell in nb.cells if cell.cell_type == "markdown" and "A.3 从求和到张量积" in cell.source]
        self.assertEqual(len(cells), 1)
        html, _ = HTMLExporter().from_notebook_node(nbformat.v4.new_notebook(cells=cells))
        # A literal | in the ket would be interpreted as an extra table column.
        self.assertIn("<table>", html)
        self.assertEqual(html.count("<tr>"), 6)
        self.assertIn("CR(0, 2, pi/4)", html)

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
