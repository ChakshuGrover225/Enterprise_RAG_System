
def load_local_files() -> dict:
    total_file_read = 10
    skipped_file = 3
    parsed_file = 7

    return dict(
    total_file_read=total_file_read,
    skipped_file=skipped_file,
    parsed_file=parsed_file
)

def load_onedrive_files() -> dict:
    total_file_read = 100
    skipped_file = 35
    parsed_file = 65

    return dict(
    total_file_read=total_file_read,
    skipped_file=skipped_file,
    parsed_file=parsed_file
)

def load_sharepoint_files() -> dict:
    total_file_read = 245
    skipped_file = 200
    parsed_file = 45

    return dict(
    total_file_read=total_file_read,
    skipped_file=skipped_file,
    parsed_file=parsed_file
)


def load_documents():
    print(f"{'\t'*3} ----------- LOAD STARTED -----------")

    try:
        local_fileload_stats = load_local_files()
        onedrive_fileload_stats = load_onedrive_files()
        sharepoint_fileload_stats = load_sharepoint_files()


        tab = '\t'
        print(f"{tab*4} LOCAL: \tTOTAL: {local_fileload_stats['total_file_read']} \t"
              f"SKIPPED: {local_fileload_stats['skipped_file']} \t"
              f"PARSED: {local_fileload_stats['parsed_file']}")

        print(f"{tab*4} ONEDRIVE: \t TOTAL: {onedrive_fileload_stats['total_file_read']} \t"
                      f"SKIPPED: {onedrive_fileload_stats['skipped_file']} \t"
                      f"PARSED: {onedrive_fileload_stats['parsed_file']}")

        print(f"{tab*4} SHAREPOINT: \tTOTAL: {sharepoint_fileload_stats['total_file_read']} \t"
                      f"SKIPPED: {sharepoint_fileload_stats['skipped_file']} \t"
                      f"PARSED: {sharepoint_fileload_stats['parsed_file']}")

        
        print(f"{tab*3}Load document happened successfully.")

    except Exception as e:
        print(f"load_document failed {e}")

    finally:
        print(f"{tab*3}----------- LOAD ENDS -----------")