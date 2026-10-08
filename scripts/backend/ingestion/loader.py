import time

start_time = time.time()

def _lap() -> str:
    return f"{time.time() - start_time:.2f}s"



from queue import Queue
files_ready_to_load = Queue()

from concurrent.futures import ThreadPoolExecutor
import inspect
from scripts.backend.backend_settings import settings
from rich import print
from pathlib import Path
import os
import json
import zipfile
import tiktoken


indentation = '--- --- '*2


from io import BytesIO




def count_tokens(text: str) -> int:
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


# -----------------------------------------------------------------------
# Safety checks -- #2 (unbounded/zip-bomb input) and #4 (plaintext,
# world-readable output) from the ingestion vulnerability review.
# -----------------------------------------------------------------------

class FileSafetyError(Exception):
    """Raised when a file fails a pre-parse safety check."""


MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024          # 50 MB on-disk cap, tune as needed
MAX_ZIP_UNCOMPRESSED_SIZE = 200 * 1024 * 1024   # 200 MB cap on what a zip would unpack to
MAX_ZIP_COMPRESSION_RATIO = 100                 # reject if uncompressed/compressed > 100x

# Office/OpenDocument/EPUB formats are all zip containers under the hood --
# these are the ones worth checking for zip-bomb-style ratios.
ZIP_LIKE_SUFFIXES = {
    ".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm",
    ".odt", ".ods", ".odp", ".epub",
}


def validate_file_for_parsing(
    file_path: Path,
    max_file_size: int = MAX_FILE_SIZE_BYTES,
    max_zip_uncompressed_size: int = MAX_ZIP_UNCOMPRESSED_SIZE,
    max_zip_ratio: int = MAX_ZIP_COMPRESSION_RATIO,
) -> None:
    """
    Raises FileSafetyError if file_path looks unsafe to read and parse.
    Call this BEFORE Path.read_bytes() / parse_document() -- the whole
    point is to reject the file before its bytes are loaded into memory
    or handed to anydoc/Docling.

    Two checks:
      1. On-disk size, cheap and catches the simple case (someone drops a
         20 GB file into the watched folder).
      2. For zip-based office formats, the archive's *uncompressed* size
         and compression ratio -- read from the zip's central directory
         only, nothing is extracted -- to catch zip bombs that are small
         on disk but balloon in memory/CPU once unpacked.
    """
    if not file_path.is_file():
        raise FileSafetyError(f"{file_path} is not a regular file")

    on_disk_size = file_path.stat().st_size
    if on_disk_size > max_file_size:
        raise FileSafetyError(
            f"{file_path.name} is {on_disk_size:,} bytes, "
            f"exceeds the {max_file_size:,} byte limit"
        )

    if file_path.suffix.lower() in ZIP_LIKE_SUFFIXES:
        try:
            with zipfile.ZipFile(file_path) as zf:
                infos = zf.infolist()
                total_uncompressed = sum(i.file_size for i in infos)
                total_compressed = sum(i.compress_size for i in infos) or 1
                ratio = total_uncompressed / total_compressed

                if total_uncompressed > max_zip_uncompressed_size:
                    raise FileSafetyError(
                        f"{file_path.name} would unpack to "
                        f"{total_uncompressed:,} bytes, exceeds the "
                        f"{max_zip_uncompressed_size:,} byte limit"
                    )
                if ratio > max_zip_ratio:
                    raise FileSafetyError(
                        f"{file_path.name} has a {ratio:.0f}x compression "
                        f"ratio (limit {max_zip_ratio}x) -- looks like a zip bomb"
                    )
        except zipfile.BadZipFile:
            raise FileSafetyError(
                f"{file_path.name} has a zip-based extension "
                f"but isn't a valid zip archive"
            )


def secure_json_dump(data, output_path: str, mode: int = 0o600) -> None:
    """
    Writes `data` as JSON to output_path, restricted to owner read/write.

    Uses os.open(..., O_CREAT, mode) so the permission is set atomically
    at creation time, rather than writing the file with the default
    (often world-readable) mode first and chmod'ing it afterward -- that
    gap is a window where the content is already exposed.

    os.chmod() is called again after writing so the permission is
    enforced even if output_path already existed with looser permissions
    from before this function was in use.

    Caveat: the owner-only bits in `mode` are honored on Linux/macOS.
    Windows uses NTFS ACLs instead of POSIX permission bits, so this
    narrows exposure on POSIX systems but isn't equivalent to an ACL
    change on Windows.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(output_path, flags, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(output_path, mode)


_SENTINEL = object()

def save_to_json(no_of_retry: int = 3):
    """
    Consumer: drains files_ready_to_load as producers fill it, and writes
    each parsed item -- {"text_content": ..., "token count": ...} -- into a
    JSON array on disk. There's no unique field on these items, so the
    output is a flat list, appended to only after a write has actually
    succeeded (the append and the write happen together via `updated`, so
    a failed write never leaves `accumulated` out of sync with disk).

    On failure an item is re-queued for another attempt, tracked by object
    identity (id(item)) since there's nothing in the payload to key a
    retry off of, up to no_of_retry.

    Exits when it receives _SENTINEL -- load_document() only sends that
    after queue.join() confirms every item, including retries, has been
    accounted for, so a retried item can never get stuck behind it.
    """
    output_path = r"output/loaded_document.json"
    # relative to the process's cwd, and open() won't create "output/" on
    # its own -- make sure the directory exists before the first write,
    # otherwise every attempt fails and the item just burns through retries
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    accumulated: list = []
    retry_counts: dict = {}

    while True:
        item = files_ready_to_load.get()
        try:
            if item is _SENTINEL:
                break

            attempt_key = id(item)
            try:
                updated = accumulated + [item]
                secure_json_dump(updated, output_path)
                accumulated = updated
                retry_counts.pop(attempt_key, None)
            except Exception as e:
                retry_counts[attempt_key] = retry_counts.get(attempt_key, 0) + 1
                if retry_counts[attempt_key] <= no_of_retry:
                    files_ready_to_load.put(item)
                else:
                    print(f"[{_lap()}]{indentation} giving up on item after {no_of_retry} retries: {e}")

        finally:
            files_ready_to_load.task_done()

    print(f"[{_lap()}]{indentation} save_to_json saved {len(accumulated)} item(s) to {output_path}")
    return accumulated


def parse_with_anydoc(file_bytes: bytes, file_name: str):
    """Parse a file's bytes with anydoc and return text + metadata.
 
    Requires: pip install firecrawl-anydoc
    """
    import anydoc
 
    # Content-based detection first (what anydoc prefers); fall back to the
    # filename's extension for signature-less formats like CSV.
    fmt = anydoc.format_from_bytes(file_bytes) or anydoc.format_from_path(file_name)
 
    if fmt:
        text_content = anydoc.to_markdown_bytes(file_bytes, fmt)
    else:
        text_content = anydoc.to_markdown_bytes(file_bytes)
 
    return text_content


def parse_with_docling(file_bytes: bytes, file_name: str):
    """Parse a file's bytes with Docling and return text content.

    Requires: pip install docling

    This was referenced by secondary_parser but wasn't defined anywhere
    in the file -- without it, any file that failed primary_parser would
    hit a NameError in secondary_parser instead of actually falling back.
    """
    from docling.datamodel.base_models import DocumentStream
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    source = DocumentStream(name=file_name, stream=BytesIO(file_bytes))
    result = converter.convert(source)
    return result.document.export_to_markdown()


def primary_parser(file_bytes: bytes, file_name: str) :
    result = parse_with_anydoc(file_bytes= file_bytes, file_name= file_name)
    return result
    

def secondary_parser(file_bytes: bytes, file_name: str) :
    result = parse_with_docling(file_bytes= file_bytes, file_name= file_name)
    return result

def parse_document(file_bytes: bytes, file_name: str) -> dict :

    result = {
                "text_content": "",
                "token count" : 0
            }

    for parser_name, parser_fn in (("primary", primary_parser), ("secondary", secondary_parser)):
        try:
            result['text_content'] = parser_fn(file_bytes, file_name)
            result['token count'] = count_tokens(result['text_content'])
            return result
        except Exception as e:
            print(f"[{_lap()}]{indentation} {parser_name}_parser failed for {file_name}: {e} ")
    return result



def load_all_local_files():
    result_stats = {
        "skipped" : 0,
        "parsed" : 0,
        "found" : 0
    }
    print(f'[{_lap()}]{ indentation } {inspect.currentframe().f_code.co_name}')

    local_file_path = settings.loaderSettings.database_file_Path
    print(f"[{_lap()}]{indentation} Directory to read : {local_file_path}")


    # -------------------- Locating the files ------------------------------------
    if( (Path(local_file_path).is_dir() and os.access(local_file_path, os.R_OK)) == False):
        print(f"[{_lap()}]{indentation} file couldnt found")
        return result_stats

    print(f"[{_lap()}]{indentation * 2}File found")

    paths_of_acceptable_files = [f for f in Path(local_file_path).rglob("*") if f.suffix.lstrip(".") in settings.loaderSettings.acceptable_file_agreement]
    result_stats['found'] = len(paths_of_acceptable_files)
    print(f"[{_lap()}]{indentation}  {result_stats['found']}")

    # ------------------ Reading the files ---------------------------------------

    for file_to_read in paths_of_acceptable_files:
        try:
            validate_file_for_parsing(file_to_read)
        except FileSafetyError as e:
            print(f"[{_lap()}]{indentation} skipping {file_to_read.name}: {e}")
            result_stats['skipped'] += 1
            continue

        file_parsed_data = parse_document(file_bytes=Path(file_to_read).read_bytes(), 
                                          file_name =Path(file_to_read).name)  
        # add to queue
        files_ready_to_load.put(file_parsed_data)
        print(f"{indentation} size of Queue grew to : {files_ready_to_load.qsize()}")

        if(file_parsed_data is None):
            result_stats['skipped'] += 1
        else:
            result_stats['parsed']  += 1


    

        



    # ------------------ printing results ----------------------------------------
    print(f'[{_lap()}] { indentation } found:{result_stats['found']} parsed:{result_stats['parsed']} skipped:{result_stats['skipped']} ')
    

def load_all_sharepoint_files():
    time.sleep(4)
    print(f' { indentation } {inspect.currentframe().f_code.co_name}')
    print(f' { indentation } found:{0} parsed:{0} skipped:{0} ')
    pass

def load_all_google_drive_files():
    
    print(f' { indentation } {inspect.currentframe().f_code.co_name}')
    time.sleep(5)
    print(f' { indentation } found:{0} parsed:{0} skipped:{0} ')

def load_all_one_notes_files():
    print(f' { indentation } {inspect.currentframe().f_code.co_name}')
    time.sleep(6)
    print(f' { indentation } found:{0} parsed:{0} skipped:{0} ')


def load_document():
    funcs = [
        load_all_sharepoint_files,
        load_all_local_files,
        load_all_one_notes_files,
        load_all_google_drive_files
    ]



    with ThreadPoolExecutor(max_workers=5) as executor:
        # Start the consumer first so it's already draining the queue the
        # moment producers start putting items on it.
        consumer_future = executor.submit(save_to_json)

        futures = [executor.submit(f) for f in funcs]
        results = [f.result() for f in futures]

        # No producer will add anything else past this point. join() blocks
        # until every item -- including any re-queued for retry inside
        # save_to_json -- has had task_done() called on it, so it's now
        # safe to tell the consumer to stop.
        files_ready_to_load.join()
        files_ready_to_load.put(_SENTINEL)

        saved_items = consumer_future.result()

    print(f"[{_lap()}]------------saved {len(saved_items)} item(s) to disk")
    print(f"[{_lap()}]------------ENDS HERE")