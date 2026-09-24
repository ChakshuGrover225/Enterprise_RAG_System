---
name: code-like-me
description: The user's personal coding style and engineering discipline. Use this skill EVERY time you write, generate, refactor, extend, review, or fix code for this user, in any language, even for a tiny helper or a one-off script, and even if the user does not mention "style" or "code_like_me". It defines naming (ObjectNameClass classes, verb_noun functions, very descriptive variables), single-responsibility functions with strict return types, a mandatory contract docstring (called by / output goes to / input contract / operation / flowchart / output contract), line-level comments, runtime progress prints, and logging to the central run_log.txt. If code is being produced for this user, this skill applies.
---

# code_like_me

This skill makes every piece of code look like the user wrote it themselves. The user's style has one underlying idea: **a reader should be able to understand any function, where it sits in the system, and what it promises, without reading any other file.** Every rule below serves that idea. When a situation is not covered, pick the option that best serves it.

Bundled resources:
- `assets/run_logger.py` — the central logging + progress module. Copy into the project if none exists (see "Logging and progress").
- `references/worked_example.md` — a complete multi-file example in this style. Read it the first time you use this skill in a conversation, and whenever you are unsure how a rule looks in practice.
- `scripts/check_code_like_me.py` — a checker that verifies naming, return types, and docstring sections. Run it on every Python file you write or change before handing work back.

---

## 1. Naming

Names are the first layer of documentation. A name that needs a comment to explain it is the wrong name.

| Thing | Pattern | Good | Bad |
|---|---|---|---|
| Class | PascalCase, always ending in `Class` | `UserAccountClass`, `CsvLoaderClass` | `User`, `UserAccount`, `user_class` |
| Function / method | snake_case, starts with a verb, `verb_object[_detail]` | `create_new_user`, `verify_user_password`, `load_raw_orders_csv` | `user()`, `process`, `handle_data`, `doStuff` |
| Variable | snake_case, fully descriptive of *what* it holds and *which one* | `unique_guid_for_app`, `hashed_user_password`, `total_failed_login_attempts` | `x`, `tmp`, `data`, `res`, `pwd`, `val` |
| Constant | UPPER_SNAKE_CASE, equally descriptive | `MAXIMUM_LOGIN_ATTEMPTS_BEFORE_LOCK` | `MAX`, `N` |
| Parameter | same as variable | `username_to_register` | `var1`, `u` |

Rules that people usually break, so watch for them:
- **No single-letter or abbreviated names anywhere**, including loop variables, comprehensions, lambdas, and exception objects. Write `for order_record in raw_order_records:` not `for o in orders:`; `except ValueError as invalid_value_error:` not `as e`.
- **Collections are plural and say what they contain**: `registered_user_ids`, `order_amount_by_customer_id` (dict: key → value).
- **Booleans read as a yes/no question**: `is_user_verified`, `has_expired_session`.
- **Avoid vague verbs** (`process`, `handle`, `manage`, `do`) — if the only fitting verb is vague, the function probably does two things (see §2).
- Private helpers keep the same pattern with a leading underscore: `_build_user_id_candidate`.
- Placeholders in the user's own examples (like `var1`) are illustrations only — never ship them.

In non-Python languages keep the *meaning* of the rules, adapting only what the language forces (e.g. JavaScript functions may stay snake_case since this is a personal style; Java/Go exported-name rules win where the compiler requires it — say so in a comment when you deviate).

---

## 2. One function, one role

Each function has exactly **one** job in the codebase. The test: write the `operation:` sentence for the docstring. If it naturally contains "and", "then", or "also" joining two actions, split the function.

- `create_new_user` creates a user record and returns its id. It does **not** also send a welcome email, validate password strength, and write to disk — those are `send_welcome_email`, `validate_password_strength`, `save_user_record`.
- Orchestration is a legitimate single role: a function whose one job is to call other functions in order (e.g. `run_user_signup_pipeline`) is fine, as long as it contains no business logic itself.
- Enforcing the function's own input contract (guard checks at the top) is part of the role, not a second role.
- Prefer many small, well-named functions over one clever one. Aim for functions short enough to read in one screen.

Classes follow the same principle: one class, one responsibility; its methods each obey every function rule in this file.

---

## 3. Strict types

- **Every function declares its return type**, and it is specific: `-> str`, `-> list[str]`, `-> dict[str, int]`, `-> tuple[str, int]`, `-> UserAccountClass`, `-> None`.
- **Never** `-> Any`, bare `-> list`, bare `-> dict`, or no annotation. If the honest answer is "several shapes", the function is doing too much or needs a dataclass/`TypedDict`.
- `Optional[...]` / `X | None` is allowed only when the Output Contract says exactly when `None` is returned.
- **Every parameter is type-annotated** too, matching the Input Contract.
- The actual returned value must match the annotation and the Output Contract. If the contract says "6-digit uppercase alphanumeric string", the code must guarantee that, not merely hope for it.

---

## 4. The contract docstring (mandatory, every function and method)

Every function — public, private, method, tiny helper — gets this docstring, with these sections, in this order, with these headings. It is the function's passport: who calls it, where its output goes, what it accepts, what it does, how, and what it promises.

```python
def create_new_user(username_to_register: str, plain_text_user_password: str) -> str:
    '''
    called by:
    * ingestion/loader.py - create_session()

    output goes to:
    * ingestion/user_authentication.py - verify_user()

    input contract:
    username_to_register : str -> containing the username, 3-30 chars, letters/digits/underscore
    plain_text_user_password : str -> containing the user's password, at least 8 chars

    operation:
    create a new user record from the given credentials and return the user's id_number

    flowchart:
    1. validate that both inputs satisfy the input contract
    2. if any input is invalid, log the reason and raise ValueError
    3. else generate a candidate 6-character uppercase alphanumeric id
    4. if the id already exists, go back to step 3
    5. else store the user record under the new id
    6. finish: return the id

    output contract:
    new_user_id_number : str -> containing a 6-digit capital alphanumeric string, e.g. "A7K2Q9"
    on failure : raises ValueError when an input breaks the input contract
    '''
```

How to fill each section well:

**called by** — every function that calls this one, one bullet each, formatted `* relative/path/file.py - function_name()`. Use the path relative to the project root. For methods, write `ClassNameClass.method_name()`. If nothing calls it yet, write `* none yet (entry point)` or `* none yet (planned: <where it will be called from>)` — never leave it blank or invent callers.

**output goes to** — every function that *consumes the returned value* (not merely the caller — the place the value actually flows to next). Same bullet format. If the value is only returned to the caller, list the caller. If the function returns `None`, write `* nothing (returns None; side effect: <what it changes>)`.

**input contract** — one line per parameter: `name : type -> containing <meaning, units, allowed range/format>`. Be concrete: formats, lengths, units, whether empty is allowed.

**operation** — one sentence, the single role (see §2).

**flowchart** — numbered steps in plain language, including branches written as `if ... , then ...` / `else ...`, and a final `finish` step. It must describe the code as written. In the function body, label code blocks with `# Step N:` comments that match these numbers, so the reader can jump between docstring and code.

**output contract** — the returned value's name, type, and exact guarantees (format, length, range, ordering, uniqueness). Add an `on failure :` line for any exception the function deliberately raises.

### Keeping the call graph true
"called by" and "output goes to" are only valuable if they are accurate, so treat them as part of the code:
- When you add a call from A to B, update **B's `called by`** and, if A consumes B's output, **B's `output goes to`**.
- When you remove or rename a function, search the codebase (`grep -rn "function_name(" .`) and fix every docstring that mentions it.
- When editing an existing project, grep for real callers before writing these sections rather than guessing. If you only have part of the codebase, write what you can verify and mark the rest `* unverified: <guess>` so the user can confirm.

---

## 5. Comments

Every important line gets a comment saying **what is happening and why**, written for a reader who has not seen the code before. "Important" means anything that isn't trivially obvious from the name alone: computations, branches, calls to other functions, I/O, loops, guard checks, returns.

```python
# Step 3: build a candidate id from 6 random uppercase letters/digits
new_user_id_candidate = "".join(secrets.choice(ALLOWED_USER_ID_CHARACTERS) for _character_position in range(6))
```

- Put the comment on the line above the code it explains.
- Mark flowchart steps with `# Step N: ...` so they line up with the docstring.
- Don't narrate syntax (`# assign variable`); explain intent.

---

## 6. Runtime progress prints and the central run_log.txt

The user wants to *watch* the system run in the console and *reconstruct* any run afterwards from a single log file, `run_log.txt`, shared by the whole system.

**Setup (once per project):**
1. Look for an existing central logger in the project (search for `run_log.txt`, `run_logger`). If one exists, use it — never create a second log file or a second logger.
2. If none exists, copy `assets/run_logger.py` into the project (usually the project root or a `utils/`/`common/` package) and import it from there.

**In every function:**
- At the start: record that the function started (with key, non-secret inputs).
- At each important step: record progress (this is the "print statement at runtime").
- At the end: record what is being returned.
- On every failure branch: record the reason at level `ERROR` before raising.

Use `record_progress_step(...)` from `run_logger.py` — a single call that both **prints** to the console and **appends** to `run_log.txt` with timestamp, level, file, and function name, so print and log never drift apart:

```python
# report progress to the console and to the central run_log.txt
record_progress_step(
    progress_message=f"generated candidate id {new_user_id_candidate}",
    source_function_name="create_new_user",
)
```

Never log secrets (passwords, tokens, keys, full card numbers). Log that they were received, or a masked version.

---

## 7. Body layout

Inside every function, keep this order so all functions feel the same:

1. Start record (`record_progress_step(... "started" ...)`)
2. Input-contract guard checks (log `ERROR` and raise on violation)
3. The flowchart steps, each marked `# Step N:` with a progress record
4. End record (`... "finished, returning ..."`)
5. A single `return` of a clearly named result variable whose name matches the Output Contract

---

## 8. Workflow when writing code for the user

1. Plan the functions first: list each function with its one-sentence operation. Split any that contain "and".
2. Sketch the call graph (who calls whom, where outputs flow) — this is what fills `called by` / `output goes to`.
3. Check for / set up the central logger (§6).
4. Write each function: signature with full types → contract docstring → body following §7 with comments.
5. Update the docstrings of any existing functions whose callers/consumers changed.
6. Run the checker on every Python file you touched:
   `python <skill_dir>/scripts/check_code_like_me.py <file_or_folder>`
   Fix everything it reports. It checks the mechanical rules; you are still responsible for the judgement rules (one role, accurate call graph, meaningful comments).
7. When presenting the code, briefly list the functions with their operation line, so the user sees the architecture at a glance.

If the user asks for something quick ("just a snippet"), still follow the style — that is the point of this skill — but you may keep the flowchart short.

---

## 9. Final self-check

Before handing back code, confirm each of these honestly:
- [ ] Every class ends in `Class`; every function starts with a verb; no short or vague names anywhere, including loop variables and exceptions.
- [ ] Every function has one role; no operation sentence contains "and".
- [ ] Every function and parameter is type-annotated with specific types; no `Any`.
- [ ] Every function has all six docstring sections in order, and the call graph sections are accurate in both directions.
- [ ] Flowchart step numbers match the `# Step N:` comments in the body.
- [ ] Important lines are commented with intent.
- [ ] Progress is printed and logged to the single central `run_log.txt`; no secrets are logged.
- [ ] The checker script passes.
