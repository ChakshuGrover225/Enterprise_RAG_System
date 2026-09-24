"""
run_logger.py — the single, central logging module for the whole system.

Every function in the project reports progress through record_progress_step(),
which prints the message to the console AND appends it to run_log.txt, so the
console and the log file always tell the same story.

Log file location:
* default: run_log.txt in the same folder as this file (place this file at the project root)
* override: set the environment variable RUN_LOG_FILE_PATH to an absolute path

Note: functions in this module do not call record_progress_step() on themselves,
because the logger logging its own logging would recurse forever.
"""

import inspect
import os
import threading
from datetime import datetime

# name of the environment variable that can override where run_log.txt lives
RUN_LOG_PATH_ENVIRONMENT_VARIABLE_NAME = "RUN_LOG_FILE_PATH"

# default file name of the central log shared by the whole system
DEFAULT_RUN_LOG_FILE_NAME = "run_log.txt"

# allowed severity levels, in increasing order of seriousness
ALLOWED_LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# one lock for the whole process so lines from different threads never interleave
RUN_LOG_WRITE_LOCK = threading.Lock()


def get_run_log_file_path() -> str:
    '''
    called by:
    * run_logger.py - append_line_to_run_log()

    output goes to:
    * run_logger.py - append_line_to_run_log()

    input contract:
    none

    operation: decide the absolute path of the central run_log.txt file

    flowchart:
    1. read the RUN_LOG_FILE_PATH environment variable
    2. if it is set and non-empty, then use it
    3. else build <folder of this file>/run_log.txt
    4. finish: return the absolute path

    output contract:
    run_log_absolute_file_path : str -> containing an absolute filesystem path ending in the log file name
    '''

    # Step 1: look for a user-provided override of the log location
    overridden_run_log_path = os.environ.get(RUN_LOG_PATH_ENVIRONMENT_VARIABLE_NAME, "").strip()

    # Step 2: an override wins when present
    if overridden_run_log_path:
        run_log_absolute_file_path = os.path.abspath(overridden_run_log_path)
    # Step 3: otherwise keep the log next to this module (the project root by convention)
    else:
        folder_containing_this_module = os.path.dirname(os.path.abspath(__file__))
        run_log_absolute_file_path = os.path.join(folder_containing_this_module, DEFAULT_RUN_LOG_FILE_NAME)

    # Step 4: hand back the resolved path
    return run_log_absolute_file_path


def format_run_log_line(progress_message: str, source_function_name: str, source_file_name: str, log_level_name: str) -> str:
    '''
    called by:
    * run_logger.py - record_progress_step()

    output goes to:
    * run_logger.py - record_progress_step() (printed to console)
    * run_logger.py - append_line_to_run_log() (written to run_log.txt)

    input contract:
    progress_message : str -> containing the human-readable description of what just happened
    source_function_name : str -> containing the name of the function reporting progress
    source_file_name : str -> containing the file (relative or base name) the reporting function lives in
    log_level_name : str -> containing one of DEBUG, INFO, WARNING, ERROR, CRITICAL

    operation: turn one progress event into a single formatted log line

    flowchart:
    1. take the current local time with millisecond precision
    2. join time, level, file, function and message with " | "
    3. finish: return the line

    output contract:
    formatted_run_log_line : str -> "YYYY-MM-DD HH:MM:SS.mmm | LEVEL    | file.py | function_name() | message", no trailing newline
    '''

    # Step 1: timestamp to the millisecond so fast sequences stay in order when read back
    current_timestamp_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    # Step 2: pad the level so columns line up when scanning the log by eye
    formatted_run_log_line = (
        f"{current_timestamp_text} | {log_level_name:<8} | {source_file_name} | "
        f"{source_function_name}() | {progress_message}"
    )

    # Step 3: hand back the single formatted line
    return formatted_run_log_line


def append_line_to_run_log(formatted_run_log_line: str) -> None:
    '''
    called by:
    * run_logger.py - record_progress_step()

    output goes to:
    * nothing (returns None; side effect: appends one line to run_log.txt)

    input contract:
    formatted_run_log_line : str -> containing one already-formatted log line without a trailing newline

    operation: append one line to the central run_log.txt file

    flowchart:
    1. resolve the log file path
    2. make sure the folder for the log file exists
    3. under the process-wide lock, append the line plus a newline
    4. finish

    output contract:
    None -> the line is durably appended to run_log.txt
    on failure : raises OSError if the file cannot be written
    '''

    # Step 1: find out where the central log lives
    run_log_absolute_file_path = get_run_log_file_path()

    # Step 2: create the parent folder if it does not exist yet
    os.makedirs(os.path.dirname(run_log_absolute_file_path), exist_ok=True)

    # Step 3: lock so concurrent threads never mix half-lines, then append in UTF-8
    with RUN_LOG_WRITE_LOCK:
        with open(run_log_absolute_file_path, "a", encoding="utf-8") as run_log_file_handle:
            run_log_file_handle.write(formatted_run_log_line + "\n")

    # Step 4: finish (nothing to return)
    return None


def _find_reporting_file_name() -> str:
    '''
    called by:
    * run_logger.py - record_progress_step()

    output goes to:
    * run_logger.py - format_run_log_line()

    input contract:
    none

    operation: find the file name of the code that called record_progress_step()

    flowchart:
    1. look two frames up the call stack (past this helper and record_progress_step)
    2. if that frame exists, then take its file path relative to the current working directory
    3. else use "unknown_file"
    4. finish: return the name

    output contract:
    reporting_file_name : str -> containing a relative file path such as "ingestion/loader.py", or "unknown_file"
    '''

    # Step 1: frame 0 = this helper, frame 1 = record_progress_step, frame 2 = the real caller
    current_call_stack_frames = inspect.stack()

    # Step 2: use the real caller's file when the stack is deep enough
    if len(current_call_stack_frames) > 2:
        reporting_file_absolute_path = current_call_stack_frames[2].filename
        reporting_file_name = os.path.relpath(reporting_file_absolute_path, os.getcwd())
    # Step 3: fall back safely rather than crash the program because of logging
    else:
        reporting_file_name = "unknown_file"

    # Step 4: hand back the file name
    return reporting_file_name


def record_progress_step(progress_message: str, source_function_name: str, log_level_name: str = "INFO") -> None:
    '''
    called by:
    * every function in the project that reports runtime progress

    output goes to:
    * nothing (returns None; side effects: prints one line to the console and appends it to run_log.txt)

    input contract:
    progress_message : str -> containing what just happened; must not contain secrets
    source_function_name : str -> containing the name of the calling function, without "()"
    log_level_name : str -> containing one of DEBUG, INFO, WARNING, ERROR, CRITICAL (default INFO)

    operation: report one progress event to both the console and the central run log

    flowchart:
    1. if the level is not an allowed level, then treat it as WARNING and note the bad level in the message
    2. find which file is reporting
    3. build the formatted line
    4. print the line to the console
    5. append the same line to run_log.txt
    6. finish

    output contract:
    None -> the identical line has been printed and appended to run_log.txt
    on failure : raises OSError only if run_log.txt cannot be written
    '''

    # Step 1: normalise the level; an unknown level must never silently disappear
    normalised_log_level_name = log_level_name.upper()
    if normalised_log_level_name not in ALLOWED_LOG_LEVEL_NAMES:
        progress_message = f"[unknown log level '{log_level_name}'] {progress_message}"
        normalised_log_level_name = "WARNING"

    # Step 2: identify the reporting file automatically so callers don't have to pass it
    reporting_file_name = _find_reporting_file_name()

    # Step 3: build the one line that both the console and the file will receive
    formatted_run_log_line = format_run_log_line(
        progress_message=progress_message,
        source_function_name=source_function_name,
        source_file_name=reporting_file_name,
        log_level_name=normalised_log_level_name,
    )

    # Step 4: show live progress on the console (flush so it appears immediately)
    print(formatted_run_log_line, flush=True)

    # Step 5: persist the same line in the central run_log.txt
    append_line_to_run_log(formatted_run_log_line)

    # Step 6: finish (nothing to return)
    return None
