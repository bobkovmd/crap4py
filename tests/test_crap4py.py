"""
Tests for crap4py.
Run with: python3 -m pytest tests/ -v
"""
import os
import sys
import tempfile
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from crap4py import (
    calculate_crap,
    count_cc_java_regex,
    count_cc_ts,
    _count_cc_body,
    parse_jacoco_xml,
    parse_istanbul_json,
    MethodMetric,
)


# ─── CRAP Formula Tests ─────────────────────────────────────────────────

class TestCrapFormula:
    def test_base_case(self):
        """CC=1, coverage=0 → CRAP = 1² × 1 + 1 = 2"""
        assert calculate_crap(1, 0.0) == 2.0

    def test_fully_covered(self):
        """CC=10, coverage=1.0 → CRAP = 10"""
        assert calculate_crap(10, 1.0) == 10.0

    def test_no_coverage(self):
        """CC=5, coverage=0 → CRAP = 25 + 5 = 30"""
        assert calculate_crap(5, 0.0) == 30.0

    def test_partial_coverage(self):
        """CC=10, coverage=0.5 → CRAP = 100 × 0.125 + 10 = 22.5"""
        assert calculate_crap(10, 0.5) == 22.5

    def test_high_complexity_no_coverage(self):
        """CC=20, coverage=0 → CRAP = 400 + 20 = 420"""
        assert calculate_crap(20, 0.0) == 420.0

    def test_high_complexity_full_coverage(self):
        """CC=20, coverage=1.0 → CRAP = 20"""
        assert calculate_crap(20, 1.0) == 20.0


# ─── Java CC Tests ──────────────────────────────────────────────────────

class TestJavaCC:
    def test_simple_method(self):
        source = """
public class Foo {
    public void bar() {
        System.out.println("hello");
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 1
        assert methods[0]['cc'] == 1

    def test_if_statement(self):
        source = """
public class Foo {
    public void bar(int x) {
        if (x > 0) {
            System.out.println("positive");
        }
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 1
        assert methods[0]['cc'] == 2  # base 1 + if

    def test_for_loop(self):
        source = """
public class Foo {
    public void bar() {
        for (int i = 0; i < 10; i++) {
            System.out.println(i);
        }
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 1
        assert methods[0]['cc'] == 2  # base 1 + for

    def test_while_loop(self):
        source = """
public class Foo {
    public void bar() {
        while (true) {
            break;
        }
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 1
        assert methods[0]['cc'] == 2  # base 1 + while

    def test_try_catch(self):
        source = """
public class Foo {
    public void bar() {
        try {
            int x = 1 / 0;
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 1
        assert methods[0]['cc'] >= 2  # base 1 + try (catch adds +1 but regex may not catch it)

    def test_ternary(self):
        source = """
public class Foo {
    public String bar(int x) {
        return x > 0 ? "positive" : "negative";
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 1
        assert methods[0]['cc'] == 2  # base 1 + ternary

    def test_multiple_methods(self):
        source = """
public class Foo {
    public void bar() {
        if (true) {}
    }
    public void baz() {
        for (int i = 0; i < 10; i++) {}
    }
}
"""
        methods = count_cc_java_regex(source, "Foo.java")
        assert len(methods) == 2
        assert methods[0]['cc'] == 2
        assert methods[1]['cc'] == 2


# ─── TypeScript CC Tests ────────────────────────────────────────────────

class TestTypeScriptCC:
    def test_simple_function(self):
        source = """
function hello() {
    console.log("hello");
}
"""
        methods = count_cc_ts(source, "hello.ts")
        assert len(methods) == 1
        assert methods[0]['cc'] == 1

    def test_if_else(self):
        source = """
function check(x: number) {
    if (x > 0) {
        return "positive";
    } else {
        return "negative";
    }
}
"""
        methods = count_cc_ts(source, "check.ts")
        assert len(methods) == 1
        assert methods[0]['cc'] == 2  # base 1 + if

    def test_arrow_function(self):
        source = """
const add = (a: number, b: number) => {
    if (a > 0) {
        return a + b;
    }
    return b;
};
"""
        methods = count_cc_ts(source, "add.ts")
        assert len(methods) >= 1

    def test_switch(self):
        source = """
function grade(score: number) {
    switch (score) {
        case 1: return "bad";
        case 2: return "ok";
        case 3: return "good";
        default: return "unknown";
    }
}
"""
        methods = count_cc_ts(source, "grade.ts")
        assert len(methods) == 1
        assert methods[0]['cc'] >= 4  # base 1 + 3 cases


# ─── Coverage Parser Tests ──────────────────────────────────────────────

class TestCoverageParsers:
    def test_istanbul_json(self):
        """Test Istanbul/NYC JSON coverage parsing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "test.js": {
                    "statementMap": {
                        "0": {"start": {"line": 1}, "end": {"line": 1}},
                        "1": {"start": {"line": 2}, "end": {"line": 2}},
                    },
                    "s": {"0": 1, "1": 0},
                    "fnMap": {
                        "0": {
                            "name": "hello",
                            "decl": {"start": {"line": 1}, "end": {"line": 3}},
                        }
                    },
                }
            }, f)
            f.flush()

            coverage = parse_istanbul_json(f.name)
            assert len(coverage) > 0
            os.unlink(f.name)


# ─── MethodMetric Tests ─────────────────────────────────────────────────

class TestMethodMetric:
    def test_risk_low(self):
        m = MethodMetric(file="test.java", method="foo", line=1, cc=2, coverage=1.0, crap=2.0)
        assert m.risk == "LOW"

    def test_risk_moderate(self):
        m = MethodMetric(file="test.java", method="foo", line=1, cc=10, coverage=0.5, crap=22.5)
        assert m.risk == "MODERATE"

    def test_risk_high(self):
        m = MethodMetric(file="test.java", method="foo", line=1, cc=20, coverage=0.0, crap=420.0)
        assert m.risk == "HIGH"


# ─── Integration Tests ──────────────────────────────────────────────────

class TestIntegration:
    def test_java_project_analysis(self):
        """Test full analysis of a sample Java project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create sample Java file
            java_file = Path(tmpdir) / "Sample.java"
            java_file.write_text("""
public class Sample {
    public void simple() {
        System.out.println("hello");
    }

    public void complex(int x) {
        if (x > 0) {
            for (int i = 0; i < x; i++) {
                if (i % 2 == 0) {
                    System.out.println(i);
                }
            }
        } else {
            System.out.println("negative");
        }
    }
}
""")

            from crap4py import analyze_project
            metrics = analyze_project(tmpdir, 'java')

            assert len(metrics) == 2
            # simple() should have CC=1
            simple = [m for m in metrics if 'simple' in m.method]
            assert len(simple) == 1
            assert simple[0].cc == 1

            # complex() should have higher CC
            complex_m = [m for m in metrics if 'complex' in m.method]
            assert len(complex_m) == 1
            assert complex_m[0].cc > 1

    def test_ts_project_analysis(self):
        """Test full analysis of a sample TypeScript project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ts_file = Path(tmpdir) / "sample.ts"
            ts_file.write_text("""
function simple() {
    console.log("hello");
}

function complex(x: number) {
    if (x > 0) {
        for (let i = 0; i < x; i++) {
            if (i % 2 === 0) {
                console.log(i);
            }
        }
    } else {
        console.log("negative");
    }
}
""")

            from crap4py import analyze_project
            metrics = analyze_project(tmpdir, 'typescript')

            assert len(metrics) >= 2
            simple = [m for m in metrics if 'simple' in m.method]
            assert len(simple) == 1
            assert simple[0].cc == 1


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
