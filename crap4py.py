#!/usr/bin/env python3
"""
crap4py — CRAP metric calculator for Java and TypeScript projects.

Based on crap4java (https://github.com/unclebob/crap4java)
and crap4clj (https://github.com/unclebob/crap4clj).

Formula: CRAP = CC² × (1 - coverage)³ + CC

Zero dependencies — AST parsers are built-in. Just run:
    python3 crap4py.py /path/to/project --lang java
    python3 crap4py.py /path/to/project --lang typescript
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


# ─── Data ────────────────────────────────────────────────────────────────

@dataclass
class MethodMetric:
    file: str
    method: str
    line: int
    end_line: int = 0
    cc: int = 1
    coverage: float = 0.0
    crap: float = 0.0

    @property
    def risk(self) -> str:
        if self.crap <= 5:
            return "LOW"
        elif self.crap <= 30:
            return "MODERATE"
        return "HIGH"


# ─── Java AST CC Counter (uses JDK compiler API, no pip deps) ────────────

def count_cc_java(source: str, file_path: str = "") -> List[dict]:
    """Count cyclomatic complexity for Java methods using JDK javac AST.

    Uses com.sun.source.tree API available in JDK 11+.
    No external dependencies needed.
    """
    results = []

    # Write source to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.java', delete=False) as f:
        # Extract class name or use default
        class_match = re.search(r'(?:public\s+)?(?:abstract\s+)?class\s+(\w+)', source)
        class_name = class_match.group(1) if class_match else "TempClass"
        f.write(source)
        f.flush()
        tmp_path = f.name

    try:
        # Use javac to parse and analyze
        # We'll use the JDK Tree API via a small Java program
        java_code = f"""
import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.tools.*;
import java.io.*;
import java.util.*;

public class CCAnalyzer {{
    static int decisionPoints = 0;

    public static void main(String[] args) throws Exception {{
        String src = new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(args[0])));
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {{
            System.err.println("No JDK compiler available");
            System.exit(1);
        }}

        StandardJavaFileManager fm = compiler.getStandardFileManager(null, null, null);
        JavaFileObject jfo = new SimpleJavaFileObject(
            java.net.URI.create("string:///TempClass.java"), JavaFileObject.Kind.SOURCE
        ) {{
            @Override public CharSequence getCharContent(boolean ignoreEncodingErrors) {{ return src; }}
        }};

        JavacTask task = (JavacTask) compiler.getTask(null, fm, null,
            List.of("-proc:none"), null, List.of(jfo));
        Iterable<? extends CompilationUnitTree> units = task.parse();
        Trees trees = Trees.instance(task);

        for (CompilationUnitTree unit : units) {{
            new TreeScanner<Void, Void>() {{
                @Override public Void visitClass(ClassTree node, Void v) {{
                    String className = node.getSimpleName().toString();
                    for (Tree member : node.getMembers()) {{
                        if (member instanceof MethodTree) {{
                            MethodTree mt = (MethodTree) member;
                            if (mt.getBody() == null) continue;
                            decisionPoints = 0;
                            scan(mt.getBody(), v);
                            int cc = decisionPoints + 1;
                            long start = trees.getSourcePositions().getStartPosition(unit, mt);
                            int line = (int) unit.getLineMap().getLineNumber(start);
                            System.out.println(className + "." + mt.getName() + "\\t" + line + "\\t" + cc);
                        }}
                    }}
                    return null;
                }}

                @Override public Void visitIf(IfTree node, Void v) {{ decisionPoints++; return super.visitIf(node, v); }}
                @Override public Void visitForLoop(ForLoopTree node, Void v) {{ decisionPoints++; return super.visitForLoop(node, v); }}
                @Override public Void visitEnhancedForLoop(EnhancedForLoopTree node, Void v) {{ decisionPoints++; return super.visitEnhancedForLoop(node, v); }}
                @Override public Void visitWhileLoop(WhileLoopTree node, Void v) {{ decisionPoints++; return super.visitWhileLoop(node, v); }}
                @Override public Void visitDoWhileLoop(DoWhileLoopTree node, Void v) {{ decisionPoints++; return super.visitDoWhileLoop(node, v); }}
                @Override public Void visitCatch(CatchTree node, Void v) {{ decisionPoints++; return super.visitCatch(node, v); }}
                @Override public Void visitConditionalExpression(ConditionalExpressionTree node, Void v) {{ decisionPoints++; return super.visitConditionalExpression(node, v); }}
                @Override public Void visitCase(CaseTree node, Void v) {{ decisionPoints++; return super.visitCase(node, v); }}
                @Override public Void visitBinary(BinaryTree node, Void v) {{
                    if (node.getKind() == Tree.Kind.CONDITIONAL_AND || node.getKind() == Tree.Kind.CONDITIONAL_OR) {{
                        decisionPoints++;
                    }}
                    return super.visitBinary(node, v);
                }}
            }}.scan(unit, null);
        }}
    }}
}}
"""
        # Write analyzer to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.java', delete=False) as af:
            af.write(java_code)
            af.flush()
            analyzer_path = af.name

        try:
            # Compile the analyzer
            compile_result = subprocess.run(
                ['javac', analyzer_path],
                capture_output=True, text=True, timeout=30
            )
            if compile_result.returncode != 0:
                # JDK not available, fallback to regex
                return count_cc_java_regex(source, file_path)

            # Run the analyzer
            class_dir = os.path.dirname(analyzer_path)
            run_result = subprocess.run(
                ['java', '-cp', class_dir, 'CCAnalyzer', tmp_path],
                capture_output=True, text=True, timeout=30
            )
            if run_result.returncode != 0:
                return count_cc_java_regex(source, file_path)

            # Parse output: "ClassName.methodName\tline\tcc"
            for line in run_result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split('\t')
                if len(parts) == 3:
                    method_full = parts[0]
                    line_no = int(parts[1])
                    cc = int(parts[2])
                    # Split class.method
                    dot_idx = method_full.rfind('.')
                    if dot_idx > 0:
                        cls = method_full[:dot_idx]
                        method = method_full[dot_idx + 1:]
                    else:
                        cls = ""
                        method = method_full
                    results.append({
                        "file": file_path,
                        "class": cls,
                        "method": method,
                        "line": line_no,
                        "end_line": line_no,
                        "cc": cc,
                    })
        finally:
            os.unlink(analyzer_path)
            # Clean up .class file
            class_file = analyzer_path.replace('.java', '.class')
            if os.path.exists(class_file):
                os.unlink(class_file)
    except Exception:
        return count_cc_java_regex(source, file_path)
    finally:
        os.unlink(tmp_path)

    return results


def count_cc_java_regex(source: str, file_path: str = "") -> List[dict]:
    """Fallback: count CC using regex (no JDK needed)."""
    results = []
    class_name = Path(file_path).stem if file_path else ""

    class_match = re.search(r'(?:public|private|protected)?\s*(?:abstract)?\s*class\s+(\w+)', source)
    if class_match:
        class_name = class_match.group(1)

    lines = source.split('\n')
    in_method = False
    method_name = ""
    method_line = 0
    brace_count = 0
    method_body_lines = []

    java_keywords = {'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default',
                     'try', 'catch', 'finally', 'throw', 'throws', 'return', 'break',
                     'continue', 'new', 'this', 'super', 'class', 'interface', 'enum',
                     'import', 'package', 'public', 'private', 'protected', 'static',
                     'abstract', 'final', 'synchronized', 'native', 'strictfp',
                     'transient', 'volatile', 'extends', 'implements', 'instanceof',
                     'true', 'false', 'null', 'var', 'record', 'sealed', 'permits',
                     'yield', 'assert', 'const', 'goto'}

    for i, line in enumerate(lines):
        stripped = line.strip()

        method_match = re.match(
            r'(?:public|private|protected|static|abstract|synchronized|final|\s)*'
            r'(?:[\w<>\[\],\s]+?\s+)?(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s.]+)?\s*\{',
            stripped
        )
        if method_match:
            candidate = method_match.group(1)
            if candidate in java_keywords:
                if in_method:
                    brace_count += stripped.count('{') - stripped.count('}')
                    method_body_lines.append(stripped)
                continue
            in_method = True
            method_name = candidate
            method_line = i + 1
            brace_count = 1
            method_body_lines = []
            continue

        if in_method:
            brace_count += stripped.count('{') - stripped.count('}')
            method_body_lines.append(stripped)

            if brace_count <= 0:
                body = '\n'.join(method_body_lines)
                cc = _count_cc_body(body, 'java')
                results.append({
                    "class": class_name,
                    "method": method_name,
                    "line": method_line,
                    "end_line": i + 1,
                    "cc": cc,
                })
                in_method = False

    return results


# ─── TypeScript AST CC Counter (built-in tokenizer) ─────────────────────

def count_cc_ts(source: str, file_path: str = "") -> List[dict]:
    """Count cyclomatic complexity for TypeScript/JavaScript.

    Uses a built-in brace-matching parser. No external dependencies.
    """
    results = []
    lines = source.split('\n')

    # Find function/method definitions
    patterns = [
        (re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('), 'function'),
        (re.compile(r'^\s*(?:public|private|protected|static|readonly|\s)*(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\w+)?\s*(?:=>|{)'), 'method'),
        (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>'), 'arrow'),
        (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function'), 'arrow_func'),
    ]

    js_keywords = {'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default',
                   'try', 'catch', 'finally', 'throw', 'return', 'break', 'continue',
                   'new', 'this', 'super', 'class', 'interface', 'enum', 'import',
                   'export', 'from', 'const', 'let', 'var', 'function', 'async',
                   'await', 'yield', 'typeof', 'instanceof', 'in', 'of', 'delete',
                   'void', 'with', 'debugger', 'true', 'false', 'null', 'undefined',
                   'static', 'get', 'set', 'constructor'}

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*') or not stripped:
            continue

        for pattern, kind in patterns:
            m = pattern.match(stripped)
            if m:
                method_name = m.group(1)
                if method_name in js_keywords:
                    break
                if method_name[0].isupper() and kind != 'function':
                    continue

                # Find body boundaries
                body_start = i
                brace_count = 0
                found_open = False

                for j in range(i, len(lines)):
                    for ch in lines[j]:
                        if ch == '{':
                            brace_count += 1
                            found_open = True
                        elif ch == '}':
                            brace_count -= 1
                    if found_open and brace_count <= 0:
                        break

                body_lines = lines[i:j + 1] if j < len(lines) else lines[i:]
                body = '\n'.join(body_lines)
                cc = _count_cc_body(body, 'typescript')
                cc = max(cc, 1)

                results.append({
                    "file": file_path,
                    "method": method_name,
                    "line": i + 1,
                    "end_line": min(j + 1, len(lines)),
                    "cc": cc,
                })
                break

    return results


# ─── Generic CC body counter ─────────────────────────────────────────────

def _count_cc_body(body: str, lang: str = 'java') -> int:
    """Count decision points in a function body."""
    # Remove strings and comments
    cleaned = re.sub(r'["\'`].*?["\'`]', '""', body)
    cleaned = re.sub(r'//.*', '', cleaned)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)

    cc = 1  # base complexity

    # Common decision points
    cc += len(re.findall(r'\bif\s*\(', cleaned))
    cc += len(re.findall(r'\bfor\s*\(', cleaned))
    cc += len(re.findall(r'\bwhile\s*\(', cleaned))
    cc += len(re.findall(r'\bcatch\s*\(', cleaned))
    cc += len(re.findall(r'\bswitch\s*\(', cleaned))
    cc += len(re.findall(r'\bcase\s+', cleaned))
    cc += len(re.findall(r'\?\s*[^:]+\s*:', cleaned))  # ternary
    cc += len(re.findall(r'&&', cleaned))
    cc += len(re.findall(r'\|\|', cleaned))

    if lang == 'java':
        cc += len(re.findall(r'\bdo\s*\{', cleaned))
    elif lang == 'typescript':
        cc += len(re.findall(r'\?\?', cleaned))  # null coalescing
        cc += len(re.findall(r'\?\.', cleaned))  # optional chaining

    return max(cc, 1)


# ─── Coverage Parsers ───────────────────────────────────────────────────

def parse_jacoco_xml(xml_path: str) -> Dict[str, float]:
    """Parse JaCoCo XML and return per-method coverage."""
    import xml.etree.ElementTree as ET
    tree = ET.parse(xml_path)
    root = tree.getroot()
    coverage = {}

    for pkg in root.findall('.//package'):
        for cls in pkg.findall('.//class'):
            class_name = cls.get('name', '').replace('/', '.')
            for method in cls.findall('.//method'):
                method_name = method.get('name', '')
                if method_name in ('<init>', '<clinit>'):
                    continue
                for counter in method.findall('counter'):
                    if counter.get('type') == 'INSTRUCTION':
                        covered = int(counter.get('covered', 0))
                        missed = int(counter.get('missed', 0))
                        total = covered + missed
                        if total > 0:
                            key = f"{class_name}.{method_name}"
                            coverage[key] = covered / total
                            break

    return coverage


def parse_lcov_info(lcov_path: str) -> Dict[str, float]:
    """Parse LCOV info file and return per-function coverage."""
    coverage = {}
    with open(lcov_path) as f:
        content = f.read()

    blocks = content.split('end_of_record')
    for block in blocks:
        if not block.strip():
            continue
        fn_match = re.search(r'FN:(\d+),(.+)', block)
        fnh_match = re.search(r'FNDA:(\d+),(.+)', block)
        if fn_match and fnh_match:
            fn_name = fn_match.group(2)
            hits = int(fnh_match.group(1))
            coverage[fn_name] = hits

    if coverage:
        max_hits = max(coverage.values()) if coverage.values() else 1
        if max_hits > 0:
            return {k: min(1.0, v / max(max_hits, 10)) for k, v in coverage.items()}
    return coverage


def parse_istanbul_json(json_path: str) -> Dict[str, float]:
    """Parse Istanbul/NYC coverage JSON."""
    with open(json_path) as f:
        data = json.load(f)

    coverage = {}
    for file_path, file_data in data.items():
        statement_map = file_data.get('statementMap', {})
        statement_counts = file_data.get('s', {})
        fn_map = file_data.get('fnMap', {})

        for fn_id, fn_info in fn_map.items():
            fn_name = fn_info.get('name', '')
            decl = fn_info.get('decl', {})
            start_line = decl.get('start', {}).get('line', 0)
            end_line = decl.get('end', {}).get('line', 0)

            covered = 0
            total = 0
            for stmt_id, stmt_info in statement_map.items():
                stmt_start = stmt_info.get('start', {}).get('line', 0)
                if start_line <= stmt_start <= end_line:
                    count = statement_counts.get(stmt_id, 0)
                    total += 1
                    if count > 0:
                        covered += 1

            if total > 0:
                key = f"{fn_name}:{start_line}"
                coverage[key] = covered / total

    return coverage


# ─── CRAP Calculator ────────────────────────────────────────────────────

def calculate_crap(cc: int, coverage: float) -> float:
    """Calculate CRAP score: CRAP = CC² × (1 - coverage)³ + CC"""
    if coverage >= 1.0:
        return float(cc)
    if coverage <= 0.0:
        return float(cc ** 2 + cc)
    return float(cc ** 2 * (1 - coverage) ** 3 + cc)


# ─── Test Runners ────────────────────────────────────────────────────────

def run_java_tests_with_coverage(project_path: str) -> bool:
    """Run Java tests with JaCoCo coverage."""
    project = Path(project_path)

    if (project / 'pom.xml').exists():
        print("Running Maven tests with JaCoCo...")
        try:
            result = subprocess.run(
                ['mvn', '-q', 'org.jacoco:jacoco-maven-plugin:0.8.12:prepare-agent',
                 'test', 'org.jacoco:jacoco-maven-plugin:0.8.12:report'],
                cwd=str(project), capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                return True
            print(f"Maven failed: {result.stderr[:200]}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"Maven error: {e}")

    if (project / 'build.gradle').exists() or (project / 'build.gradle.kts').exists():
        print("Running Gradle tests with JaCoCo...")
        try:
            result = subprocess.run(
                ['./gradlew', 'test', 'jacocoTestReport'],
                cwd=str(project), capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                return True
            print(f"Gradle failed: {result.stderr[:200]}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"Gradle error: {e}")

    return False


def run_ts_tests_with_coverage(project_path: str) -> bool:
    """Run TypeScript tests with coverage."""
    project = Path(project_path)
    pkg_json = project / 'package.json'
    if pkg_json.exists():
        with open(pkg_json) as f:
            pkg = json.load(f)

        scripts = pkg.get('scripts', {})
        test_script = None
        for name in ['test:coverage', 'coverage', 'test:cov', 'test']:
            if name in scripts:
                test_script = name
                break

        if test_script:
            print(f"Running 'npm run {test_script}'...")
            try:
                result = subprocess.run(
                    ['npm', 'run', test_script],
                    cwd=str(project), capture_output=True, text=True, timeout=300
                )
                if result.returncode == 0:
                    return True
                print(f"npm test failed: {result.stderr[:200]}")
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"npm error: {e}")

    return False


# ─── Project Analysis ───────────────────────────────────────────────────

def find_source_files(project_path: str, lang: str) -> List[Path]:
    """Find source files for the given language."""
    project = Path(project_path)

    if lang == 'java':
        files = []
        for f in project.rglob('*.java'):
            parts = f.relative_to(project).parts
            if any(p in ('test', 'tests', 'target', 'build', '.gradle') for p in parts):
                continue
            files.append(f)
        return files

    elif lang in ('typescript', 'ts'):
        extensions = ['*.ts', '*.tsx', '*.js', '*.jsx']
        files = []
        for ext in extensions:
            for f in project.rglob(ext):
                parts = f.relative_to(project).parts
                if any(p in ('node_modules', 'dist', 'build', 'coverage', '.next',
                             '__tests__', 'test', 'tests', 'spec', 'specs') for p in parts):
                    continue
                if f.name.endswith('.d.ts') or f.name.endswith('.min.js'):
                    continue
                files.append(f)
        return files

    return []


def find_coverage_file(project_path: str, lang: str) -> Optional[str]:
    """Auto-detect coverage file in project."""
    project = Path(project_path)

    if lang == 'java':
        candidates = [
            'target/site/jacoco/jacoco.xml',
            'build/reports/jacoco/test/jacocoTestReport.xml',
        ]
    else:
        candidates = [
            'coverage/coverage-final.json',
            'coverage/lcov.info',
            'coverage/lcov-report/lcov.info',
            '.nyc_output/coverage.json',
        ]

    for candidate in candidates:
        path = project / candidate
        if path.exists():
            return str(path)

    return None


def analyze_project(project_path: str, lang: str, coverage_file: Optional[str] = None,
                    run_tests: bool = False) -> List[MethodMetric]:
    """Analyze project for CRAP metrics."""
    project = Path(project_path)

    source_files = find_source_files(project_path, lang)
    if not source_files:
        print(f"No {lang} source files found in {project_path}")
        return []

    print(f"Found {len(source_files)} {lang} source files")

    if not coverage_file:
        coverage_file = find_coverage_file(project_path, lang)
        if coverage_file:
            print(f"Auto-detected coverage: {coverage_file}")

    if run_tests and not coverage_file:
        if lang == 'java':
            if run_java_tests_with_coverage(project_path):
                coverage_file = find_coverage_file(project_path, lang)
        elif lang in ('typescript', 'ts'):
            if run_ts_tests_with_coverage(project_path):
                coverage_file = find_coverage_file(project_path, lang)

    coverage_data = {}
    if coverage_file:
        cov_path = Path(coverage_file)
        print(f"Parsing coverage: {cov_path.name}")
        if cov_path.name == 'jacoco.xml':
            coverage_data = parse_jacoco_xml(str(cov_path))
        elif cov_path.suffix == '.info':
            coverage_data = parse_lcov_info(str(cov_path))
        elif cov_path.suffix == '.json':
            coverage_data = parse_istanbul_json(str(cov_path))
        print(f"Coverage data for {len(coverage_data)} methods")

    metrics = []
    for src_file in source_files:
        try:
            source = src_file.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        rel_path = str(src_file.relative_to(project))

        if lang == 'java':
            methods = count_cc_java(source, rel_path)
        else:
            methods = count_cc_ts(source, rel_path)

        for m in methods:
            method_key = f"{m.get('class', '')}.{m['method']}" if m.get('class') else m['method']
            cov = coverage_data.get(method_key, 0.0)

            if cov == 0.0:
                for cov_key, cov_val in coverage_data.items():
                    if m['method'] in cov_key or cov_key in m['method']:
                        cov = cov_val if isinstance(cov_val, float) else min(1.0, cov_val / 10.0)
                        break

            crap = calculate_crap(m['cc'], cov)
            metrics.append(MethodMetric(
                file=rel_path,
                method=method_key,
                line=m['line'],
                end_line=m.get('end_line', m['line']),
                cc=m['cc'],
                coverage=cov,
                crap=crap,
            ))

    return metrics


# ─── Report ──────────────────────────────────────────────────────────────

def print_report(metrics: List[MethodMetric], threshold: float = 30.0, top_n: int = 0):
    """Print CRAP report sorted by score descending."""
    if not metrics:
        print("No methods found.")
        return

    metrics.sort(key=lambda m: m.crap, reverse=True)
    if top_n:
        metrics = metrics[:top_n]

    print()
    print("=" * 110)
    print("CRAP Report — sorted by CRAP score (descending)")
    print("Formula: CRAP = CC² × (1 - coverage)³ + CC")
    print("=" * 110)
    print(f"{'File':<40} {'Method':<35} {'CC':>4} {'Cov%':>7} {'CRAP':>10} {'Risk':<10}")
    print("-" * 110)

    total_crap = 0
    high_risk = 0
    na_count = 0

    for m in metrics:
        cov_str = f"{m.coverage * 100:.1f}%" if m.coverage > 0 else "N/A"
        crap_str = f"{m.crap:.1f}" if m.coverage > 0 else f"{m.crap:.1f}*"

        file_display = m.file if len(m.file) <= 39 else "..." + m.file[-36:]
        method_display = m.method if len(m.method) <= 34 else m.method[:31] + "..."

        risk_marker = ""
        if m.risk == "HIGH" and m.coverage > 0:
            risk_marker = " ⚠"
            high_risk += 1
        elif m.coverage == 0:
            na_count += 1

        print(f"{file_display:<40} {method_display:<35} {m.cc:>4} {cov_str:>7} {crap_str:>10} {m.risk:<10}{risk_marker}")
        total_crap += m.crap

    print("-" * 110)
    print(f"Total methods: {len(metrics)} | HIGH risk: {high_risk} | No coverage: {na_count}")
    print(f"Average CRAP: {total_crap / len(metrics):.1f}")
    print()

    if high_risk > 0:
        print(f"⚠  {high_risk} methods with CRAP > {threshold} need attention!")
    if na_count > 0:
        print(f"ℹ  {na_count} methods have no coverage data (run with --run-tests)")
    print()


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='crap4py — CRAP metric calculator for Java and TypeScript',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/project --lang java
  %(prog)s /path/to/project --lang java --coverage-file target/site/jacoco/jacoco.xml
  %(prog)s /path/to/project --lang java --run-tests
  %(prog)s /path/to/project --lang typescript --coverage-file coverage/coverage-final.json
  %(prog)s /path/to/project --lang java --top 20 --threshold 15
  %(prog)s /path/to/project --lang typescript --json > report.json
        """
    )
    parser.add_argument('project', help='Path to project root')
    parser.add_argument('--lang', '-l', choices=['java', 'typescript', 'ts'], default='java',
                        help='Language to analyze (default: java)')
    parser.add_argument('--coverage-file', '-c', help='Path to coverage report file')
    parser.add_argument('--threshold', '-t', type=float, default=30.0,
                        help='CRAP threshold for warnings (default: 30)')
    parser.add_argument('--top', '-n', type=int, default=0,
                        help='Show only top N methods (default: all)')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--run-tests', '-r', action='store_true',
                        help='Run tests with coverage if no coverage data found')
    parser.add_argument('--changed-only', action='store_true',
                        help='Analyze only git-changed files')
    args = parser.parse_args()

    project_path = os.path.abspath(args.project)
    if not os.path.isdir(project_path):
        print(f"Error: {project_path} is not a directory")
        sys.exit(1)

    metrics = analyze_project(project_path, args.lang, args.coverage_file, args.run_tests)

    if args.changed_only:
        try:
            result = subprocess.run(
                ['git', 'diff', '--name-only', 'HEAD~1..HEAD'],
                cwd=project_path, capture_output=True, text=True
            )
            changed = set(result.stdout.strip().split('\n'))
            metrics = [m for m in metrics if any(c in m.file for c in changed)]
        except Exception:
            pass

    if args.json:
        output = [
            {
                "file": m.file,
                "method": m.method,
                "line": m.line,
                "end_line": m.end_line,
                "cc": m.cc,
                "coverage": round(m.coverage, 4),
                "crap": round(m.crap, 2),
                "risk": m.risk,
            }
            for m in sorted(metrics, key=lambda x: x.crap, reverse=True)
        ]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print_report(metrics, args.threshold, args.top)

    high_risk_count = sum(1 for m in metrics if m.risk == "HIGH" and m.coverage > 0)
    if high_risk_count > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == '__main__':
    main()
