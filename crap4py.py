#!/usr/bin/env python3
"""
crap4py — CRAP metric calculator for Java and TypeScript projects.

Based on crap4java (https://github.com/unclebob/crap4java)
and crap4clj (https://github.com/unclebob/crap4clj).

Formula: CRAP = CC² × (1 - coverage)³ + CC

Where:
  CC = cyclomatic complexity (decision points + 1)
  coverage = method coverage fraction from test coverage data

Usage:
    python3 crap4py.py /path/to/project --lang java
    python3 crap4py.py /path/to/project --lang typescript --coverage-file coverage/lcov.info
    python3 crap4py.py /path/to/project --lang java --threshold 30
    python3 crap4py.py /path/to/project --lang java --run-tests
    python3 crap4py.py /path/to/project --lang typescript --json > report.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── Data ────────────────────────────────────────────────────────────────

@dataclass
class MethodMetric:
    file: str
    method: str
    line: int
    end_line: int = 0
    cc: int = 1          # cyclomatic complexity
    coverage: float = 0.0  # 0.0 – 1.0
    crap: float = 0.0

    @property
    def risk(self) -> str:
        if self.crap <= 5:
            return "LOW"
        elif self.crap <= 30:
            return "MODERATE"
        return "HIGH"


# ─── Java CC Counter (javalang AST) ─────────────────────────────────────

def count_cc_java_ast(source: str, file_path: str = "") -> List[dict]:
    """Count cyclomatic complexity for Java methods using javalang AST."""
    try:
        import javalang
    except ImportError:
        return count_cc_java_regex(source, file_path)

    results = []
    try:
        tree = javalang.parse.parse(source)
    except Exception:
        return results

    for path_items, node in tree.filter(javalang.tree.MethodDeclaration):
        if node.body is None:
            continue
        name = node.name
        line = node.position.line if node.position else 0
        end_line = node.position.line if node.position else 0

        # Count CC by walking the method body
        cc = 1  # base complexity
        cc += _walk_cc_java(node.body)

        class_name = ""
        for p in path_items:
            if isinstance(p, javalang.tree.ClassDeclaration):
                class_name = p.name

        results.append({
            "file": file_path,
            "class": class_name,
            "method": name,
            "line": line,
            "end_line": end_line,
            "cc": cc,
        })
    return results


def _walk_cc_java(node) -> int:
    """Walk Java AST node counting decision points."""
    if node is None:
        return 0
    import javalang.tree as jt

    count = 0

    if isinstance(node, jt.IfStatement):
        count += 1
    elif isinstance(node, jt.ForStatement):
        count += 1
    elif isinstance(node, jt.WhileStatement):
        count += 1
    elif isinstance(node, jt.DoStatement):
        count += 1
    elif isinstance(node, jt.SwitchStatement):
        count += len(getattr(node, 'cases', []))
    elif isinstance(node, jt.CatchClause):
        count += 1
    elif isinstance(node, jt.ConditionalExpression):
        count += 1

    # Recurse into children
    for child in node.children:
        if isinstance(child, list):
            for item in child:
                if hasattr(item, 'children'):
                    count += _walk_cc_java(item)
        elif hasattr(child, 'children'):
            count += _walk_cc_java(child)

    return count


def count_cc_java_regex(source: str, file_path: str = "") -> List[dict]:
    """Fallback: count CC using regex (no javalang)."""
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

    for i, line in enumerate(lines):
        stripped = line.strip()

        method_match = re.match(
            r'(?:public|private|protected|static|abstract|synchronized|final|\s)*'
            r'(?:[\w<>\[\],\s]+?\s+)?(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s.]+)?\s*\{',
            stripped
        )
        if method_match:
            in_method = True
            method_name = method_match.group(1)
            method_line = i + 1
            brace_count = 1
            method_body_lines = []
            continue

        if in_method:
            brace_count += stripped.count('{') - stripped.count('}')
            method_body_lines.append(stripped)

            if brace_count <= 0:
                body = '\n'.join(method_body_lines)
                cc = _count_cc_body_generic(body, 'java')

                results.append({
                    "class": class_name,
                    "method": method_name,
                    "line": method_line,
                    "end_line": i + 1,
                    "cc": cc,
                })
                in_method = False

    return results


# ─── TypeScript CC Counter (tree-sitter) ────────────────────────────────

def count_cc_ts_tree_sitter(source: str, file_path: str = "") -> List[dict]:
    """Count CC for TypeScript/JavaScript using tree-sitter."""
    try:
        import tree_sitter
        import tree_sitter_typescript
        import tree_sitter_javascript
    except ImportError:
        return count_cc_ts_regex(source, file_path)

    results = []

    # Determine language
    is_ts = file_path.endswith(('.ts', '.tsx')) if file_path else False
    try:
        if is_ts:
            lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        else:
            lang = tree_sitter.Language(tree_sitter_javascript.language())
    except Exception:
        return count_cc_ts_regex(source, file_path)

    parser = tree_sitter.Parser(lang)
    tree = parser.parse(bytes(source, 'utf-8'))

    # Query for function/method definitions
    if is_ts:
        query = lang.query("""
            (function_declaration name: (identifier) @func)
            (method_definition name: (property_identifier) @method)
            (arrow_function) @arrow
        """)
    else:
        query = lang.query("""
            (function_declaration name: (identifier) @func)
            (method_definition name: (property_identifier) @method)
            (arrow_function) @arrow
        """)

    captures = query.captures(tree.root_node)

    for node, tag in captures:
        name = ""
        if tag in ('func', 'method'):
            # Get the name node
            for child in node.children:
                if child.type in ('identifier', 'property_identifier'):
                    name = source[child.start_byte:child.end_byte]
                    break
        elif tag == 'arrow':
            # Try to get variable name
            parent = node.parent
            if parent and parent.type == 'variable_declarator':
                for child in parent.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            else:
                name = f"arrow_{node.start_point[0] + 1}"

        if not name:
            name = f"anon_{node.start_point[0] + 1}"

        # Get function body
        body_node = None
        for child in node.children:
            if child.type == 'statement_block':
                body_node = child
                break

        if body_node is None:
            # Arrow function with expression body
            cc = 1
        else:
            body_text = source[body_node.start_byte:body_node.end_byte]
            cc = _count_cc_body_generic(body_text, 'typescript')

        results.append({
            "file": file_path,
            "method": name,
            "line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "cc": cc,
        })

    return results


def count_cc_ts_regex(source: str, file_path: str = "") -> List[dict]:
    """Count CC for TypeScript/JavaScript using regex."""
    results = []
    lines = source.split('\n')

    patterns = [
        (re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('), 'function'),
        (re.compile(r'^\s*(?:public|private|protected|static|readonly|\s)*(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\w+)?\s*(?:=>|{)'), 'method'),
        (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>'), 'arrow'),
        (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function'), 'arrow_func'),
    ]

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*') or not stripped:
            continue

        for pattern, kind in patterns:
            m = pattern.match(stripped)
            if m:
                method_name = m.group(1)
                if method_name[0].isupper() and kind != 'function':
                    continue

                body_start = i
                if '{' in stripped:
                    brace_count = stripped.count('{') - stripped.count('}')
                elif '=>' in stripped and '{' not in stripped:
                    brace_count = 0
                else:
                    brace_count = 0

                j = i + 1
                while j < len(lines) and brace_count > 0:
                    brace_count += lines[j].count('{') - lines[j].count('}')
                    j += 1

                if '=>' in stripped and '{' not in stripped:
                    j = i + 1

                body_lines = lines[i:min(j + 1, len(lines))]
                body = '\n'.join(body_lines)
                cc = _count_cc_body_generic(body, 'typescript')
                cc = max(cc, 1)

                results.append({
                    "method": method_name,
                    "line": i + 1,
                    "end_line": min(j + 1, len(lines)),
                    "cc": cc,
                })
                break

    return results


def _count_cc_body_generic(body: str, lang: str = 'java') -> int:
    """Count decision points in a function body (language-agnostic)."""
    # Remove strings and comments
    body = re.sub(r'["\'`].*?["\'`]', '""', body)
    body = re.sub(r'//.*', '', body)
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.DOTALL)

    cc = 1  # base complexity

    # Common decision points
    cc += len(re.findall(r'\bif\s*\(', body))
    cc += len(re.findall(r'\bfor\s*\(', body))
    cc += len(re.findall(r'\bwhile\s*\(', body))
    cc += len(re.findall(r'\bcatch\s*\(', body))
    cc += len(re.findall(r'\bswitch\s*\(', body))
    cc += len(re.findall(r'\bcase\s+', body))
    cc += len(re.findall(r'\?\s*[^:]+\s*:', body))  # ternary
    cc += len(re.findall(r'&&', body))
    cc += len(re.findall(r'\|\|', body))

    if lang == 'java':
        cc += len(re.findall(r'\bdo\s*\{', body))
    elif lang == 'typescript':
        cc += len(re.findall(r'\?\?', body))  # null coalescing
        cc += len(re.findall(r'\?\.', body))  # optional chaining

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

    # Normalize
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
    """Run Java tests with JaCoCo coverage. Returns True if successful."""
    project = Path(project_path)

    # Try Maven
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

    # Try Gradle
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
    """Run TypeScript tests with coverage. Returns True if successful."""
    project = Path(project_path)

    # Check package.json for test scripts
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
        # Find Java files, excluding test directories
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
                    run_tests: bool = False, use_tree_sitter: bool = True) -> List[MethodMetric]:
    """Analyze project for CRAP metrics."""
    project = Path(project_path)

    # Find source files
    source_files = find_source_files(project_path, lang)
    if not source_files:
        print(f"No {lang} source files found in {project_path}")
        return []

    print(f"Found {len(source_files)} {lang} source files")

    # Find coverage file
    if not coverage_file:
        coverage_file = find_coverage_file(project_path, lang)
        if coverage_file:
            print(f"Auto-detected coverage: {coverage_file}")

    # Run tests if requested and no coverage
    if run_tests and not coverage_file:
        if lang == 'java':
            if run_java_tests_with_coverage(project_path):
                coverage_file = find_coverage_file(project_path, lang)
        elif lang in ('typescript', 'ts'):
            if run_ts_tests_with_coverage(project_path):
                coverage_file = find_coverage_file(project_path, lang)

    # Parse coverage data
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

    # Analyze each file
    metrics = []
    for src_file in source_files:
        try:
            source = src_file.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        rel_path = str(src_file.relative_to(project))

        # Count CC
        if lang == 'java':
            methods = count_cc_java_ast(source, rel_path)
        else:
            if use_tree_sitter:
                methods = count_cc_ts_tree_sitter(source, rel_path)
            else:
                methods = count_cc_ts_regex(source, rel_path)

        for m in methods:
            method_key = f"{m.get('class', '')}.{m['method']}" if m.get('class') else m['method']
            cov = coverage_data.get(method_key, 0.0)

            # Try alternative keys for coverage matching
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
  %(prog)s /path/to/project --lang java --changed-only
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
    parser.add_argument('--no-tree-sitter', action='store_true',
                        help='Use regex instead of tree-sitter for TypeScript')
    args = parser.parse_args()

    project_path = os.path.abspath(args.project)
    if not os.path.isdir(project_path):
        print(f"Error: {project_path} is not a directory")
        sys.exit(1)

    # Analyze
    metrics = analyze_project(
        project_path, args.lang, args.coverage_file,
        args.run_tests, not args.no_tree_sitter
    )

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

    # Output
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

    # Exit code
    high_risk_count = sum(1 for m in metrics if m.risk == "HIGH" and m.coverage > 0)
    if high_risk_count > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == '__main__':
    main()
