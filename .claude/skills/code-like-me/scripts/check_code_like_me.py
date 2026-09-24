"""
check_code_like_me.py — verify that Python files follow the code_like_me style.

usage:
    python check_code_like_me.py <file.py | folder> [more paths...]

It checks the mechanical rules (naming, type annotations, contract docstring
sections, step comments, progress logging). Judgement rules — one role per
function, accuracy of the call graph, quality of comments — still need a human
(or Claude) to review.

Exit code: 0 when there are no errors (warnings allowed), 1 otherwise.

This is a standalone developer tool: it reports to the console instead of
run_log.txt, which is declared with the marker below.
"""
# code_like_me: no-run-log

import ast
import os
import re
import sys
from dataclasses import dataclass

# the six docstring headings, in the exact order they must appear
REQUIRED_DOCSTRING_SECTION_HEADINGS = (
    "called by:",
    "output goes to:",
    "input contract:",
    "operation:",
    "flowchart:",
    "output contract:",
)

# names that say nothing about what they hold
VAGUE_VARIABLE_NAMES = {
    "_", "tmp", "temp", "data", "res", "result", "ret", "val", "value", "obj", "item",
    "info", "stuff", "thing", "foo", "bar", "baz", "arr", "lst", "dct", "dic", "str_", "num",
}

# leading verbs that usually hide more than one responsibility
VAGUE_FUNCTION_VERBS = {"process", "handle", "manage", "do", "run_stuff", "perform", "execute_all"}

# return annotations that are not specific enough
NON_SPECIFIC_RETURN_ANNOTATIONS = {"Any", "list", "dict", "tuple", "set", "object", "typing.Any", "List", "Dict", "Tuple", "Set"}

# shortest variable name we accept as "descriptive"
MINIMUM_DESCRIPTIVE_NAME_LENGTH = 4

# module-level marker that disables the progress-logging check for standalone tools
NO_RUN_LOG_MARKER_TEXT = "# code_like_me: no-run-log"

# name of the central progress function every function should call
CENTRAL_PROGRESS_FUNCTION_NAME = "record_progress_step"

# patterns for the naming conventions
SNAKE_CASE_PATTERN = re.compile(r"^_{0,2}[a-z][a-z0-9]*(_[a-z0-9]+)*$")
UPPER_SNAKE_CASE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")
CLASS_NAME_PATTERN = re.compile(r"^_?[A-Z][a-zA-Z0-9]*Class$")
PLACEHOLDER_NAME_PATTERN = re.compile(r"^(var|arg|param|x|y|z|n|i|j|k)\d*$")
FLOWCHART_STEP_PATTERN = re.compile(r"^\s*(\d+)\.", re.MULTILINE)
STEP_COMMENT_PATTERN = re.compile(r"#\s*Step\s+(\d+)", re.IGNORECASE)


@dataclass
class StyleFindingClass:
    '''
    called by:
    * check_code_like_me.py - every check_* function (constructed there)

    output goes to:
    * check_code_like_me.py - print_style_findings_report()

    input contract:
    file_path : str -> containing the path of the checked file
    line_number : int -> containing the 1-based line of the problem
    severity_name : str -> containing "ERROR" or "WARNING"
    finding_message : str -> containing a human-readable description of the problem

    operation: hold one style problem found in a file

    flowchart:
    1. store the four fields
    2. finish

    output contract:
    StyleFindingClass -> an immutable-by-convention record of one finding
    '''

    file_path: str
    line_number: int
    severity_name: str
    finding_message: str


def collect_python_file_paths(input_paths: list[str]) -> list[str]:
    '''
    called by:
    * check_code_like_me.py - run_style_check_command()

    output goes to:
    * check_code_like_me.py - run_style_check_command()

    input contract:
    input_paths : list[str] -> containing file and/or folder paths given on the command line

    operation: expand the given paths into a sorted list of Python files

    flowchart:
    1. for each path, if it is a .py file, then keep it
    2. else if it is a folder, then walk it and keep every .py file (skipping hidden folders and __pycache__)
    3. finish: return the sorted, de-duplicated list

    output contract:
    python_file_paths : list[str] -> containing unique .py file paths, sorted alphabetically
    '''

    # collect into a set so a file passed twice is only checked once
    unique_python_file_paths: set[str] = set()

    for given_input_path in input_paths:
        # Step 1: a single Python file is taken as-is
        if os.path.isfile(given_input_path) and given_input_path.endswith(".py"):
            unique_python_file_paths.add(given_input_path)
        # Step 2: folders are walked recursively
        elif os.path.isdir(given_input_path):
            for walked_folder_path, child_folder_names, child_file_names in os.walk(given_input_path):
                # prune hidden and cache folders in place so os.walk skips them
                child_folder_names[:] = [
                    child_folder_name for child_folder_name in child_folder_names
                    if not child_folder_name.startswith(".") and child_folder_name != "__pycache__"
                ]
                for child_file_name in child_file_names:
                    if child_file_name.endswith(".py"):
                        unique_python_file_paths.add(os.path.join(walked_folder_path, child_file_name))

    # Step 3: stable ordering makes reports easy to compare between runs
    python_file_paths = sorted(unique_python_file_paths)
    return python_file_paths


def check_class_names(syntax_tree: ast.Module, file_path: str) -> list[StyleFindingClass]:
    '''
    called by:
    * check_code_like_me.py - check_single_python_file()

    output goes to:
    * check_code_like_me.py - check_single_python_file()

    input contract:
    syntax_tree : ast.Module -> containing the parsed file
    file_path : str -> containing the path used in findings

    operation: report every class whose name is not PascalCase ending in "Class"

    flowchart:
    1. visit every class definition
    2. if its name does not match the pattern, then record an ERROR
    3. finish: return the findings

    output contract:
    class_name_findings : list[StyleFindingClass] -> containing zero or more ERROR findings
    '''

    class_name_findings: list[StyleFindingClass] = []

    # Step 1: walk the whole tree so nested classes are included
    for syntax_node in ast.walk(syntax_tree):
        # Step 2: only class definitions matter here
        if isinstance(syntax_node, ast.ClassDef) and not CLASS_NAME_PATTERN.match(syntax_node.name):
            class_name_findings.append(StyleFindingClass(
                file_path, syntax_node.lineno, "ERROR",
                f"class '{syntax_node.name}' must be PascalCase and end with 'Class' (e.g. '{syntax_node.name.strip('_').title().replace('_', '')}Class')",
            ))

    # Step 3: hand back what was found
    return class_name_findings


def check_variable_names(syntax_tree: ast.Module, file_path: str) -> list[StyleFindingClass]:
    '''
    called by:
    * check_code_like_me.py - check_single_python_file()

    output goes to:
    * check_code_like_me.py - check_single_python_file()

    input contract:
    syntax_tree : ast.Module -> containing the parsed file
    file_path : str -> containing the path used in findings

    operation: report variable, parameter, loop and exception names that are short, vague or wrongly cased

    flowchart:
    1. gather every bound name with its line: assignments, parameters, loop targets, comprehension targets, "except ... as" names
    2. if a name is vague, a placeholder (var1, x) or shorter than the minimum, then record an ERROR
    3. else if it is neither snake_case nor UPPER_SNAKE_CASE, then record an ERROR
    4. finish: return the findings

    output contract:
    variable_name_findings : list[StyleFindingClass] -> containing zero or more ERROR findings, one per offending name occurrence
    '''

    # Step 1: collect (name, line) pairs for every place a name gets bound
    bound_name_occurrences: list[tuple[str, int]] = []
    for syntax_node in ast.walk(syntax_tree):
        # assignment targets, loop targets and comprehension targets all appear as Name in Store context
        if isinstance(syntax_node, ast.Name) and isinstance(syntax_node.ctx, ast.Store):
            bound_name_occurrences.append((syntax_node.id, syntax_node.lineno))
        # function parameters (self/cls are Python conventions, so they are allowed)
        elif isinstance(syntax_node, ast.arg) and syntax_node.arg not in ("self", "cls"):
            bound_name_occurrences.append((syntax_node.arg, syntax_node.lineno))
        # names bound by "except SomeError as name"
        elif isinstance(syntax_node, ast.ExceptHandler) and syntax_node.name:
            bound_name_occurrences.append((syntax_node.name, syntax_node.lineno))

    variable_name_findings: list[StyleFindingClass] = []
    for bound_name, bound_line_number in bound_name_occurrences:
        # strip leading underscores for length/vagueness tests only
        name_without_leading_underscores = bound_name.lstrip("_")
        # Step 2: descriptiveness checks
        if (
            bound_name in VAGUE_VARIABLE_NAMES
            or name_without_leading_underscores.lower() in VAGUE_VARIABLE_NAMES
            or PLACEHOLDER_NAME_PATTERN.match(name_without_leading_underscores)
            or len(name_without_leading_underscores) < MINIMUM_DESCRIPTIVE_NAME_LENGTH
        ):
            variable_name_findings.append(StyleFindingClass(
                file_path, bound_line_number, "ERROR",
                f"name '{bound_name}' is not descriptive; say what it holds (e.g. 'hashed_user_password')",
            ))
        # Step 3: casing checks
        elif not (SNAKE_CASE_PATTERN.match(bound_name) or UPPER_SNAKE_CASE_PATTERN.match(bound_name)):
            variable_name_findings.append(StyleFindingClass(
                file_path, bound_line_number, "ERROR",
                f"name '{bound_name}' must be snake_case (or UPPER_SNAKE_CASE for constants)",
            ))

    # Step 4: hand back what was found
    return variable_name_findings


def _format_annotation_text(annotation_node: ast.expr | None) -> str:
    '''
    called by:
    * check_code_like_me.py - check_function_signature()

    output goes to:
    * check_code_like_me.py - check_function_signature()

    input contract:
    annotation_node : ast.expr | None -> containing a type annotation node, or None if absent

    operation: turn an annotation node back into source text

    flowchart:
    1. if there is no annotation, then return an empty string
    2. else unparse the node
    3. finish: return the text

    output contract:
    annotation_text : str -> containing the annotation as written (e.g. "list[str]"), or "" when absent
    '''

    # Step 1: absent annotations become an empty string so callers can test truthiness
    if annotation_node is None:
        annotation_text = ""
    # Step 2: ast.unparse reproduces the annotation exactly as a reader sees it
    else:
        annotation_text = ast.unparse(annotation_node)

    # Step 3: hand back the text
    return annotation_text


def check_function_signature(function_node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: str) -> list[StyleFindingClass]:
    '''
    called by:
    * check_code_like_me.py - check_single_python_file()

    output goes to:
    * check_code_like_me.py - check_single_python_file()

    input contract:
    function_node : ast.FunctionDef | ast.AsyncFunctionDef -> containing one function or method definition
    file_path : str -> containing the path used in findings

    operation: report naming and type-annotation problems in one function's signature

    flowchart:
    1. if the name is not snake_case, then record an ERROR
    2. if the first word is a vague verb, then record a WARNING
    3. if the return annotation is missing or non-specific, then record an ERROR
    4. for each parameter except self/cls, if it has no annotation, then record an ERROR
    5. finish: return the findings

    output contract:
    signature_findings : list[StyleFindingClass] -> containing zero or more ERROR/WARNING findings
    '''

    signature_findings: list[StyleFindingClass] = []
    function_name = function_node.name
    function_line_number = function_node.lineno

    # dunder methods (__init__, __repr__) keep Python's required names
    is_dunder_method = function_name.startswith("__") and function_name.endswith("__")

    # Step 1: function names are snake_case verb phrases
    if not is_dunder_method and not SNAKE_CASE_PATTERN.match(function_name):
        signature_findings.append(StyleFindingClass(
            file_path, function_line_number, "ERROR", f"function '{function_name}' must be snake_case, starting with a verb",
        ))

    # Step 2: vague leading verbs usually hide two responsibilities
    function_first_word = function_name.lstrip("_").split("_")[0]
    if function_first_word in VAGUE_FUNCTION_VERBS:
        signature_findings.append(StyleFindingClass(
            file_path, function_line_number, "WARNING",
            f"function '{function_name}' starts with vague verb '{function_first_word}'; name its single, specific role",
        ))

    # Step 3: the return type must exist and be specific
    return_annotation_text = _format_annotation_text(function_node.returns)
    if not return_annotation_text:
        signature_findings.append(StyleFindingClass(
            file_path, function_line_number, "ERROR", f"function '{function_name}' has no return type annotation",
        ))
    elif return_annotation_text in NON_SPECIFIC_RETURN_ANNOTATIONS:
        signature_findings.append(StyleFindingClass(
            file_path, function_line_number, "ERROR",
            f"function '{function_name}' returns '{return_annotation_text}', which is not specific (use e.g. list[str], dict[str, int])",
        ))

    # Step 4: every parameter needs a type that matches the input contract
    all_parameter_nodes = (
        function_node.args.posonlyargs + function_node.args.args + function_node.args.kwonlyargs
        + ([function_node.args.vararg] if function_node.args.vararg else [])
        + ([function_node.args.kwarg] if function_node.args.kwarg else [])
    )
    for parameter_node in all_parameter_nodes:
        if parameter_node.arg in ("self", "cls"):
            continue
        if parameter_node.annotation is None:
            signature_findings.append(StyleFindingClass(
                file_path, parameter_node.lineno, "ERROR",
                f"parameter '{parameter_node.arg}' of '{function_name}' has no type annotation",
            ))

    # Step 5: hand back what was found
    return signature_findings


def check_function_docstring(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, file_path: str, source_lines: list[str]
) -> list[StyleFindingClass]:
    '''
    called by:
    * check_code_like_me.py - check_single_python_file()

    output goes to:
    * check_code_like_me.py - check_single_python_file()

    input contract:
    function_node : ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef -> containing one definition to inspect
    file_path : str -> containing the path used in findings
    source_lines : list[str] -> containing the file's lines, used to find "# Step N" comments

    operation: report missing, empty or out-of-order contract docstring sections, and step comments that don't match the flowchart

    flowchart:
    1. if there is no docstring, then record an ERROR and finish
    2. find each required heading; if any is missing, then record an ERROR
    3. else if the headings are out of order, then record an ERROR
    4. if "called by" or "output goes to" has no content, then record an ERROR
    5. for functions with 3+ flowchart steps, if the body has no "# Step N" comments, then record a WARNING
    6. finish: return the findings

    output contract:
    docstring_findings : list[StyleFindingClass] -> containing zero or more ERROR/WARNING findings
    '''

    docstring_findings: list[StyleFindingClass] = []
    definition_name = function_node.name
    definition_line_number = function_node.lineno
    function_docstring_text = ast.get_docstring(function_node) or ""

    # Step 1: without a docstring there is nothing else to check
    if not function_docstring_text.strip():
        docstring_findings.append(StyleFindingClass(
            file_path, definition_line_number, "ERROR", f"'{definition_name}' has no contract docstring",
        ))
        return docstring_findings

    # Step 2: locate every heading (case-insensitive) and remember where it starts
    lowercase_docstring_text = function_docstring_text.lower()
    heading_positions_in_docstring: list[int] = []
    for required_heading in REQUIRED_DOCSTRING_SECTION_HEADINGS:
        heading_position = lowercase_docstring_text.find(required_heading)
        if heading_position == -1:
            docstring_findings.append(StyleFindingClass(
                file_path, definition_line_number, "ERROR",
                f"'{definition_name}' docstring is missing the '{required_heading}' section",
            ))
        heading_positions_in_docstring.append(heading_position)

    # Step 3: order only makes sense when every heading exists
    all_headings_present = all(heading_position != -1 for heading_position in heading_positions_in_docstring)
    if all_headings_present and heading_positions_in_docstring != sorted(heading_positions_in_docstring):
        docstring_findings.append(StyleFindingClass(
            file_path, definition_line_number, "ERROR",
            f"'{definition_name}' docstring sections are out of order; expected: " + ", ".join(REQUIRED_DOCSTRING_SECTION_HEADINGS),
        ))

    if all_headings_present:
        # Step 4: the call-graph sections must say something, even "none yet (entry point)"
        for section_index in (0, 1):
            section_start = heading_positions_in_docstring[section_index] + len(REQUIRED_DOCSTRING_SECTION_HEADINGS[section_index])
            section_end = heading_positions_in_docstring[section_index + 1]
            if not function_docstring_text[section_start:section_end].strip():
                docstring_findings.append(StyleFindingClass(
                    file_path, definition_line_number, "ERROR",
                    f"'{definition_name}' has an empty '{REQUIRED_DOCSTRING_SECTION_HEADINGS[section_index]}' section; list functions or write 'none yet (entry point)'",
                ))

        # Step 5: flowchart steps should be mirrored by "# Step N" comments in the body
        flowchart_section_text = function_docstring_text[heading_positions_in_docstring[4]:heading_positions_in_docstring[5]]
        flowchart_step_numbers = FLOWCHART_STEP_PATTERN.findall(flowchart_section_text)
        if not isinstance(function_node, ast.ClassDef) and len(flowchart_step_numbers) >= 3:
            function_body_source_text = "\n".join(source_lines[function_node.lineno - 1:function_node.end_lineno])
            if not STEP_COMMENT_PATTERN.search(function_body_source_text):
                docstring_findings.append(StyleFindingClass(
                    file_path, definition_line_number, "WARNING",
                    f"'{definition_name}' has a {len(flowchart_step_numbers)}-step flowchart but no '# Step N:' comments in its body",
                ))

    # Step 6: hand back what was found
    return docstring_findings


def check_progress_logging(function_node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: str) -> list[StyleFindingClass]:
    '''
    called by:
    * check_code_like_me.py - check_single_python_file()

    output goes to:
    * check_code_like_me.py - check_single_python_file()

    input contract:
    function_node : ast.FunctionDef | ast.AsyncFunctionDef -> containing one function or method definition
    file_path : str -> containing the path used in findings

    operation: warn when a function never reports progress through record_progress_step()

    flowchart:
    1. search the function body for a call to record_progress_step
    2. if none is found, then record a WARNING
    3. finish: return the findings

    output contract:
    logging_findings : list[StyleFindingClass] -> containing zero or one WARNING finding
    '''

    logging_findings: list[StyleFindingClass] = []

    # Step 1: accept both "record_progress_step(...)" and "run_logger.record_progress_step(...)"
    calls_progress_function = False
    for syntax_node in ast.walk(function_node):
        if isinstance(syntax_node, ast.Call):
            called_function_node = syntax_node.func
            called_function_name = (
                called_function_node.id if isinstance(called_function_node, ast.Name)
                else called_function_node.attr if isinstance(called_function_node, ast.Attribute)
                else ""
            )
            if called_function_name == CENTRAL_PROGRESS_FUNCTION_NAME:
                calls_progress_function = True
                break

    # Step 2: silence is a warning, not an error — tiny pure helpers may justify it
    if not calls_progress_function:
        logging_findings.append(StyleFindingClass(
            file_path, function_node.lineno, "WARNING",
            f"'{function_node.name}' never calls {CENTRAL_PROGRESS_FUNCTION_NAME}(); runtime progress will not reach the console or run_log.txt",
        ))

    # Step 3: hand back what was found
    return logging_findings


def check_single_python_file(file_path: str) -> list[StyleFindingClass]:
    '''
    called by:
    * check_code_like_me.py - run_style_check_command()

    output goes to:
    * check_code_like_me.py - run_style_check_command()

    input contract:
    file_path : str -> containing the path of an existing .py file

    operation: run every style check on one Python file and gather the findings

    flowchart:
    1. read and parse the file; if it does not parse, then record an ERROR and finish
    2. run the class-name and variable-name checks on the whole file
    3. for each function/method, run the signature, docstring and (unless disabled) logging checks
    4. for each class, run the docstring check
    5. finish: return all findings sorted by line

    output contract:
    file_findings : list[StyleFindingClass] -> containing every finding for this file, ordered by line number
    '''

    # Step 1: read the source; a syntax error makes every other check meaningless
    with open(file_path, encoding="utf-8") as python_source_file_handle:
        python_source_text = python_source_file_handle.read()
    try:
        syntax_tree = ast.parse(python_source_text, filename=file_path)
    except SyntaxError as python_syntax_error:
        return [StyleFindingClass(file_path, python_syntax_error.lineno or 1, "ERROR", f"file does not parse: {python_syntax_error.msg}")]

    source_lines = python_source_text.splitlines()
    # standalone tools and the logger itself may opt out of the logging check
    is_logging_check_disabled = (
        NO_RUN_LOG_MARKER_TEXT in python_source_text or os.path.basename(file_path) == "run_logger.py"
    )

    # Step 2: whole-file naming checks
    file_findings: list[StyleFindingClass] = []
    file_findings.extend(check_class_names(syntax_tree, file_path))
    file_findings.extend(check_variable_names(syntax_tree, file_path))

    for syntax_node in ast.walk(syntax_tree):
        # Step 3: per-function checks
        if isinstance(syntax_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            file_findings.extend(check_function_signature(syntax_node, file_path))
            file_findings.extend(check_function_docstring(syntax_node, file_path, source_lines))
            if not is_logging_check_disabled:
                file_findings.extend(check_progress_logging(syntax_node, file_path))
        # Step 4: classes carry the contract docstring too
        elif isinstance(syntax_node, ast.ClassDef):
            file_findings.extend(check_function_docstring(syntax_node, file_path, source_lines))

    # Step 5: line order makes the report easy to follow in an editor
    file_findings.sort(key=lambda style_finding: style_finding.line_number)
    return file_findings


def print_style_findings_report(all_style_findings: list[StyleFindingClass], checked_file_count: int) -> None:
    '''
    called by:
    * check_code_like_me.py - run_style_check_command()

    output goes to:
    * nothing (returns None; side effect: prints the report to the console)

    input contract:
    all_style_findings : list[StyleFindingClass] -> containing every finding across all files
    checked_file_count : int -> containing how many files were checked, >= 0

    operation: print a readable report of all findings with a summary line

    flowchart:
    1. print each finding as "path:line  SEVERITY  message"
    2. count errors and warnings
    3. print the summary line
    4. finish

    output contract:
    None -> the report has been printed
    '''

    # Step 1: one line per finding, in a format editors can jump to
    for style_finding in all_style_findings:
        print(f"{style_finding.file_path}:{style_finding.line_number}  {style_finding.severity_name:<7}  {style_finding.finding_message}")

    # Step 2: tally severities for the summary
    total_error_count = sum(1 for style_finding in all_style_findings if style_finding.severity_name == "ERROR")
    total_warning_count = sum(1 for style_finding in all_style_findings if style_finding.severity_name == "WARNING")

    # Step 3: a clear verdict at the end
    print(f"\nchecked {checked_file_count} file(s): {total_error_count} error(s), {total_warning_count} warning(s)")
    if total_error_count == 0 and total_warning_count == 0:
        print("all code_like_me checks passed")

    # Step 4: finish (nothing to return)
    return None


def run_style_check_command(command_line_arguments: list[str]) -> int:
    '''
    called by:
    * none yet (entry point: executed from the command line)

    output goes to:
    * sys.exit() in the __main__ block (becomes the process exit code)

    input contract:
    command_line_arguments : list[str] -> containing file/folder paths (sys.argv without the script name)

    operation: orchestrate the style check across all given paths and return an exit code

    flowchart:
    1. if no paths were given, then print usage and return 2
    2. collect the Python files
    3. check each file and gather the findings
    4. print the report
    5. finish: return 1 if any ERROR exists, else 0

    output contract:
    process_exit_code : int -> 0 = no errors, 1 = errors found, 2 = bad usage
    '''

    # Step 1: without paths there is nothing to do
    if not command_line_arguments:
        print("usage: python check_code_like_me.py <file.py | folder> [more paths...]")
        return 2

    # Step 2: expand folders into files
    python_file_paths = collect_python_file_paths(command_line_arguments)

    # Step 3: run every check on every file
    all_style_findings: list[StyleFindingClass] = []
    for python_file_path in python_file_paths:
        all_style_findings.extend(check_single_python_file(python_file_path))

    # Step 4: show the results
    print_style_findings_report(all_style_findings, len(python_file_paths))

    # Step 5: only errors fail the run; warnings are advice
    has_any_error = any(style_finding.severity_name == "ERROR" for style_finding in all_style_findings)
    process_exit_code = 1 if has_any_error else 0
    return process_exit_code


if __name__ == "__main__":
    # hand the command-line paths to the orchestrator and exit with its code
    sys.exit(run_style_check_command(sys.argv[1:]))
