# Worked example — code_like_me in a small, runnable project

A complete, checker-clean example that expands the user's own `create_new_user` sketch into a real project. Study it for: how the call graph is written in both directions, how `# Step N:` comments mirror the flowchart, how a class and its methods follow the same rules, how secrets are kept out of logs, and what `run_log.txt` looks like afterwards.

## Layout

```
project_root/
├── run_logger.py                 # copied from assets/run_logger.py
├── run_log.txt                   # created on first run, shared by everything
└── ingestion/
    ├── __init__.py
    ├── loader.py                 # create_session()            — orchestrator / entry point
    ├── user_registration.py      # create_new_user(), hash_user_password()
    ├── user_authentication.py    # verify_user()
    └── user_record_store.py      # UserRecordStoreClass
```

## Call graph (what the docstrings encode)

```
create_session()
 ├─ UserRecordStoreClass()                 -> store passed to the two calls below
 ├─ create_new_user()                      -> new_user_id_number ──┐
 │    ├─ UserRecordStoreClass.check_user_id_exists()               │
 │    ├─ hash_user_password()                                      │
 │    └─ UserRecordStoreClass.save_user_record()                   │
 └─ verify_user()  <───────────────────────────────────────────────┘
      ├─ UserRecordStoreClass.get_stored_password_hash()
      └─ hash_user_password()
```

## `ingestion/loader.py`

```python
from run_logger import record_progress_step
from ingestion.user_record_store import UserRecordStoreClass
from ingestion.user_registration import create_new_user
from ingestion.user_authentication import verify_user


def create_session(username_to_register: str, plain_text_user_password: str) -> str:
    '''
    called by:
    * none yet (entry point)

    output goes to:
    * returned to the program entry point, which shows it to the user

    input contract:
    username_to_register : str -> containing the username, 3-30 letters/digits/underscores
    plain_text_user_password : str -> containing the user's password, at least 8 characters

    operation: orchestrate signup and verification to open a session for a new user

    flowchart:
    1. create the shared user record store
    2. register the user and get their id
    3. verify the user with the same password
    4. if verification fails, then log an error and raise PermissionError
    5. else finish: return the user id as the session owner id

    output contract:
    session_owner_user_id : str -> containing the verified user's 6-character uppercase alphanumeric id
    on failure : raises ValueError (bad input, from create_new_user) or PermissionError (verification failed)
    '''

    # announce the start
    record_progress_step(progress_message=f"started for username '{username_to_register}'", source_function_name="create_session")

    # Step 1: one store shared by registration and authentication
    user_record_store = UserRecordStoreClass()

    # Step 2: registration returns the new id (see create_new_user output contract)
    new_user_id_number = create_new_user(username_to_register, plain_text_user_password, user_record_store)

    # Step 3: the new id flows straight into verification
    is_user_verified = verify_user(new_user_id_number, plain_text_user_password, user_record_store)

    # Step 4: a freshly registered user must verify; anything else is a bug worth stopping on
    if not is_user_verified:
        record_progress_step(progress_message="verification failed right after signup", source_function_name="create_session", log_level_name="ERROR")
        raise PermissionError("verification failed right after signup")

    # Step 5: report and hand back the session owner
    session_owner_user_id = new_user_id_number
    record_progress_step(progress_message=f"finished, session owner {session_owner_user_id}", source_function_name="create_session")
    return session_owner_user_id


if __name__ == "__main__":
    # run the whole flow once with sample credentials
    create_session("ada_lovelace", "analytical_engine_1843")
```

## `ingestion/user_registration.py`

```python
import hashlib
import re
import secrets

from run_logger import record_progress_step
from ingestion.user_record_store import UserRecordStoreClass

# characters allowed in a user id: capital letters and digits
ALLOWED_USER_ID_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# exact length of every user id
USER_ID_LENGTH = 6

# usernames: 3-30 letters, digits or underscores
VALID_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,30}$")

# shortest password we accept
MINIMUM_PASSWORD_LENGTH = 8


def hash_user_password(plain_text_user_password: str) -> str:
    '''
    called by:
    * ingestion/user_registration.py - create_new_user()
    * ingestion/user_authentication.py - verify_user()

    output goes to:
    * ingestion/user_record_store.py - UserRecordStoreClass.save_user_record() (via create_new_user)
    * ingestion/user_authentication.py - verify_user() (compared with the stored hash)

    input contract:
    plain_text_user_password : str -> containing the raw password, non-empty

    operation: turn a plain password into its SHA-256 hex digest

    flowchart:
    1. encode the password as UTF-8 and hash it with SHA-256
    2. finish: return the hex digest

    output contract:
    hashed_user_password : str -> containing a 64-character lowercase hex string
    '''

    # announce the start without revealing the password
    record_progress_step(progress_message="hashing a password", source_function_name="hash_user_password")

    # Step 1: SHA-256 keeps the example dependency-free (use bcrypt/argon2 in production)
    hashed_user_password = hashlib.sha256(plain_text_user_password.encode("utf-8")).hexdigest()

    # Step 2: hand back the digest
    return hashed_user_password


def create_new_user(username_to_register: str, plain_text_user_password: str, user_record_store: UserRecordStoreClass) -> str:
    '''
    called by:
    * ingestion/loader.py - create_session()

    output goes to:
    * ingestion/user_authentication.py - verify_user()

    input contract:
    username_to_register : str -> containing the username, 3-30 letters/digits/underscores
    plain_text_user_password : str -> containing the user's password, at least 8 characters
    user_record_store : UserRecordStoreClass -> containing the shared store the user is saved into

    operation: create a new user record from the given credentials and return the user's id_number

    flowchart:
    1. check both inputs against the input contract
    2. if any input is invalid, then log the reason and raise ValueError
    3. else generate a candidate 6-character uppercase alphanumeric id
    4. if the id already exists, then go back to step 3
    5. else hash the password and save the record under the id
    6. finish: return the id

    output contract:
    new_user_id_number : str -> containing a 6-digit capital alphanumeric string, e.g. "A7K2Q9", unique in the store
    on failure : raises ValueError when the username or password breaks the input contract
    '''

    # announce the start with the non-secret input only
    record_progress_step(progress_message=f"started for username '{username_to_register}'", source_function_name="create_new_user")

    # Step 1: validate the username format
    is_username_valid = bool(VALID_USERNAME_PATTERN.match(username_to_register))
    # Step 1: validate the password length
    is_password_long_enough = len(plain_text_user_password) >= MINIMUM_PASSWORD_LENGTH

    # Step 2: refuse contract violations loudly and log why
    if not is_username_valid or not is_password_long_enough:
        contract_violation_reason = "invalid username" if not is_username_valid else "password shorter than 8 characters"
        record_progress_step(progress_message=f"input contract violated: {contract_violation_reason}", source_function_name="create_new_user", log_level_name="ERROR")
        raise ValueError(contract_violation_reason)

    # Step 3 / Step 4: keep generating until an unused id appears
    while True:
        # Step 3: cryptographically random id from the allowed characters
        new_user_id_number = "".join(secrets.choice(ALLOWED_USER_ID_CHARACTERS) for _character_position in range(USER_ID_LENGTH))
        record_progress_step(progress_message=f"generated candidate id {new_user_id_number}", source_function_name="create_new_user")
        # Step 4: stop once the id is free
        if not user_record_store.check_user_id_exists(new_user_id_number):
            break

    # Step 5: hash first so the plain password is never stored
    hashed_user_password = hash_user_password(plain_text_user_password)
    # Step 5: persist the record under the new id
    user_record_store.save_user_record(new_user_id_number, username_to_register, hashed_user_password)

    # Step 6: report and hand back the id
    record_progress_step(progress_message=f"finished, returning id {new_user_id_number}", source_function_name="create_new_user")
    return new_user_id_number
```

## `ingestion/user_authentication.py`

```python
import hmac

from run_logger import record_progress_step
from ingestion.user_record_store import UserRecordStoreClass
from ingestion.user_registration import hash_user_password


def verify_user(user_id_number: str, plain_text_user_password: str, user_record_store: UserRecordStoreClass) -> bool:
    '''
    called by:
    * ingestion/loader.py - create_session()

    output goes to:
    * ingestion/loader.py - create_session() (decides whether the session is opened)

    input contract:
    user_id_number : str -> containing a 6-character uppercase alphanumeric id from create_new_user()
    plain_text_user_password : str -> containing the password the user just typed
    user_record_store : UserRecordStoreClass -> containing the shared store to verify against

    operation: decide whether a password matches the one stored for a user id

    flowchart:
    1. fetch the stored hash for the id
    2. if no hash exists, then log a warning and return False
    3. else hash the given password
    4. compare both hashes in constant time
    5. finish: return the comparison result

    output contract:
    is_user_verified : bool -> True only when the id exists and the password matches
    '''

    # announce the start with the non-secret input only
    record_progress_step(progress_message=f"started for id {user_id_number}", source_function_name="verify_user")

    # Step 1: look up what we stored at registration
    stored_password_hash = user_record_store.get_stored_password_hash(user_id_number)

    # Step 2: unknown ids can never be verified
    if stored_password_hash is None:
        record_progress_step(progress_message=f"unknown id {user_id_number}", source_function_name="verify_user", log_level_name="WARNING")
        return False

    # Step 3: hash the typed password the same way registration did
    given_password_hash = hash_user_password(plain_text_user_password)

    # Step 4: compare_digest avoids leaking information through timing
    is_user_verified = hmac.compare_digest(stored_password_hash, given_password_hash)

    # Step 5: report and hand back the verdict
    record_progress_step(progress_message=f"finished, verified = {is_user_verified}", source_function_name="verify_user")
    return is_user_verified
```

## `ingestion/user_record_store.py`

```python
from run_logger import record_progress_step


class UserRecordStoreClass:
    '''
    called by:
    * ingestion/loader.py - create_session() (constructs one shared store)

    output goes to:
    * ingestion/user_registration.py - create_new_user()
    * ingestion/user_authentication.py - verify_user()

    input contract:
    none (starts empty)

    operation: keep user records in memory, keyed by user id

    flowchart:
    1. create an empty dictionary of user records
    2. finish

    output contract:
    UserRecordStoreClass -> an empty store ready to receive user records
    '''

    def __init__(self) -> None:
        '''
        called by:
        * ingestion/loader.py - create_session()

        output goes to:
        * nothing (returns None; side effect: initialises the empty record dictionary)

        input contract:
        none

        operation: initialise an empty user record dictionary

        flowchart:
        1. create the empty dictionary
        2. finish

        output contract:
        None -> self.user_record_by_user_id is an empty dict[str, dict[str, str]]
        '''

        # Step 1: user id -> {"username": ..., "hashed_user_password": ...}
        self.user_record_by_user_id: dict[str, dict[str, str]] = {}

        # report that the store is ready
        record_progress_step(progress_message="empty user record store created", source_function_name="UserRecordStoreClass.__init__")

    def check_user_id_exists(self, candidate_user_id: str) -> bool:
        '''
        called by:
        * ingestion/user_registration.py - create_new_user()

        output goes to:
        * ingestion/user_registration.py - create_new_user() (decides whether to regenerate the id)

        input contract:
        candidate_user_id : str -> containing a 6-character uppercase alphanumeric id

        operation: tell whether a user id is already taken

        flowchart:
        1. look the id up in the record dictionary
        2. finish: return the answer

        output contract:
        is_user_id_taken : bool -> True if a record already uses this id, else False
        '''

        # Step 1: dictionary membership is an O(1) lookup
        is_user_id_taken = candidate_user_id in self.user_record_by_user_id

        # report the lookup result
        record_progress_step(progress_message=f"id {candidate_user_id} taken: {is_user_id_taken}", source_function_name="UserRecordStoreClass.check_user_id_exists")

        # Step 2: hand back the answer
        return is_user_id_taken

    def save_user_record(self, new_user_id_number: str, username_to_register: str, hashed_user_password: str) -> None:
        '''
        called by:
        * ingestion/user_registration.py - create_new_user()

        output goes to:
        * nothing (returns None; side effect: adds one record to the store)

        input contract:
        new_user_id_number : str -> containing an unused 6-character uppercase alphanumeric id
        username_to_register : str -> containing an already-validated username
        hashed_user_password : str -> containing a 64-character hex SHA-256 digest

        operation: store one user record under its id

        flowchart:
        1. write the record into the dictionary
        2. finish

        output contract:
        None -> the record is retrievable under new_user_id_number
        '''

        # Step 1: store only the hash, never the plain password
        self.user_record_by_user_id[new_user_id_number] = {
            "username": username_to_register,
            "hashed_user_password": hashed_user_password,
        }

        # report the save without logging the hash itself
        record_progress_step(progress_message=f"saved record for id {new_user_id_number}", source_function_name="UserRecordStoreClass.save_user_record")

        # Step 2: finish (nothing to return)
        return None

    def get_stored_password_hash(self, user_id_number: str) -> str | None:
        '''
        called by:
        * ingestion/user_authentication.py - verify_user()

        output goes to:
        * ingestion/user_authentication.py - verify_user() (compared with the hash of the given password)

        input contract:
        user_id_number : str -> containing a 6-character uppercase alphanumeric id

        operation: fetch the stored password hash for one user id

        flowchart:
        1. look up the record
        2. if it exists, then take its hash
        3. else use None
        4. finish: return the hash or None

        output contract:
        stored_password_hash : str | None -> 64-character hex digest, or None when the id is unknown
        '''

        # Step 1: .get avoids a KeyError for unknown ids
        matching_user_record = self.user_record_by_user_id.get(user_id_number)

        # Step 2 / Step 3: unknown ids yield None, as the output contract states
        stored_password_hash = matching_user_record["hashed_user_password"] if matching_user_record else None

        # report whether a hash was found (never the hash itself)
        record_progress_step(progress_message=f"hash found for id {user_id_number}: {stored_password_hash is not None}", source_function_name="UserRecordStoreClass.get_stored_password_hash")

        # Step 4: hand back the hash or None
        return stored_password_hash
```

## Resulting `run_log.txt` (and identical console output)

```
2026-09-21 22:48:10.839 | INFO     | ingestion/loader.py | create_session() | started for username 'ada_lovelace'
2026-09-21 22:48:10.840 | INFO     | ingestion/user_record_store.py | UserRecordStoreClass.__init__() | empty user record store created
2026-09-21 22:48:10.840 | INFO     | ingestion/user_registration.py | create_new_user() | started for username 'ada_lovelace'
2026-09-21 22:48:10.840 | INFO     | ingestion/user_registration.py | create_new_user() | generated candidate id GO0ZSD
2026-09-21 22:48:10.841 | INFO     | ingestion/user_record_store.py | UserRecordStoreClass.check_user_id_exists() | id GO0ZSD taken: False
2026-09-21 22:48:10.841 | INFO     | ingestion/user_registration.py | hash_user_password() | hashing a password
2026-09-21 22:48:10.842 | INFO     | ingestion/user_record_store.py | UserRecordStoreClass.save_user_record() | saved record for id GO0ZSD
2026-09-21 22:48:10.842 | INFO     | ingestion/user_registration.py | create_new_user() | finished, returning id GO0ZSD
2026-09-21 22:48:10.843 | INFO     | ingestion/user_authentication.py | verify_user() | started for id GO0ZSD
2026-09-21 22:48:10.843 | INFO     | ingestion/user_record_store.py | UserRecordStoreClass.get_stored_password_hash() | hash found for id GO0ZSD: True
2026-09-21 22:48:10.844 | INFO     | ingestion/user_registration.py | hash_user_password() | hashing a password
2026-09-21 22:48:10.844 | INFO     | ingestion/user_authentication.py | verify_user() | finished, verified = True
2026-09-21 22:48:10.845 | INFO     | ingestion/loader.py | create_session() | finished, session owner GO0ZSD
```
