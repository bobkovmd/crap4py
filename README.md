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

## Quick Start

```bash
# Clone
git clone https://github.com/bobkovmd/crap4py.git
cd crap4py

# Java project — just run it
python3 crap4py.py /path/to/java/project --lang java

# TypeScript project — just run it
python3 crap4py.py /path/to/ts/project --lang typescript

# With coverage data
python3 crap4py.py . --lang java --coverage-file target/site/jacoco/jacoco.xml
python3 crap4py.py . --lang typescript --coverage-file coverage/coverage-final.json

# Auto-run tests and generate coverage
python3 crap4py.py . --lang java --run-tests

# JSON output
python3 crap4py.py . --lang java --json

# Top 20 worst methods
python3 crap4py.py . --lang java --top 20
```

## Zero Dependencies

crap4py works out of the box — no `pip install` needed.

- **Java**: uses JDK compiler AST (`com.sun.source.tree`) for accurate CC counting. Falls back to regex if JDK not available.
- **TypeScript**: uses built-in brace-matching parser. No tree-sitter needed.

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
```

## Exit Codes

- `0` — success, no high-risk methods
- `1` — invalid CLI usage
- `2` — CRAP threshold exceeded

## Running Tests

```bash
pip install pytest
python3 -m pytest tests/ -v
```

## License

MIT
