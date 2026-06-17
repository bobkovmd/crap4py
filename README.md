# crap4py

CRAP metric calculator for Java and TypeScript projects.

Based on [crap4java](https://github.com/unclebob/crap4java) and [crap4clj](https://github.com/unclebob/crap4clj).

## Formula

```
CRAP = CC² × (1 - coverage)³ + CC
```

Where:
- **CC** = cyclomatic complexity (decision points + 1)
- **coverage** = method coverage fraction from test coverage data

## Risk Levels

| Score | Risk | Recommendation |
|-------|------|----------------|
| 1–5 | LOW | Clean code |
| 5–30 | MODERATE | Refactor or add tests |
| 30+ | HIGH | Complex and under-tested — prioritize |

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Java project
python3 crap4py.py /path/to/project --lang java

# Java with coverage
python3 crap4py.py /path/to/project --lang java --coverage-file target/site/jacoco/jacoco.xml

# Java with auto test run
python3 crap4py.py /path/to/project --lang java --run-tests

# TypeScript project
python3 crap4py.py /path/to/project --lang typescript

# TypeScript with coverage
python3 crap4py.py /path/to/project --lang typescript --coverage-file coverage/coverage-final.json

# JSON output
python3 crap4py.py /path/to/project --lang java --json > report.json

# Top 20 worst methods
python3 crap4py.py /path/to/project --lang java --top 20

# Only changed files
python3 crap4py.py /path/to/project --lang java --changed-only
```

## Coverage Formats

| Language | Format | File |
|----------|--------|------|
| Java | JaCoCo XML | `target/site/jacoco/jacoco.xml` |
| Java | JaCoCo (Gradle) | `build/reports/jacoco/test/jacocoTestReport.xml` |
| TypeScript | Istanbul/NYC JSON | `coverage/coverage-final.json` |
| TypeScript | LCOV | `coverage/lcov.info` |

## CLI Options

```
positional arguments:
  project               Path to project root

optional arguments:
  --lang {java,typescript,ts}, -l {java,typescript,ts}
                        Language to analyze (default: java)
  --coverage-file COVERAGE_FILE, -c COVERAGE_FILE
                        Path to coverage report file
  --threshold THRESHOLD, -t THRESHOLD
                        CRAP threshold for warnings (default: 30)
  --top TOP, -n TOP     Show only top N methods (default: all)
  --json                Output as JSON
  --run-tests, -r       Run tests with coverage if no coverage data found
  --changed-only        Analyze only git-changed files
  --no-tree-sitter     Use regex instead of tree-sitter for TypeScript
```

## Exit Codes

- `0` — success, no high-risk methods
- `1` — invalid CLI usage
- `2` — CRAP threshold exceeded (methods with CRAP > threshold)

## Running Tests

```bash
pip install -r requirements-test.txt
python3 -m pytest tests/ -v
```

## License

MIT
