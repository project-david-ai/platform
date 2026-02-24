code_interpreter = {
    "type": "function",
    "function": {
        "name": "code_interpreter",
        "description": (
            "Executes Python code in a secure sandbox. Returns stdout/stderr and uploads generated files.\n\n"
            "## MANDATORY SANDBOX RULES — NON-NEGOTIABLE:\n\n"
            "### 1. TEMPFILE COMPLIANCE (REQUIRED FOR ALL FILE-GENERATING SCRIPTS)\n"
            "   The FIRST two lines of ANY script that generates files MUST be:\n"
            "   ```\n"
            "   import tempfile\n"
            '   tempfile.tempdir = "/app/generated_files"\n'
            "   ```\n"
            "   This applies to: python-docx, openpyxl, matplotlib, pandas Excel output, and any\n"
            "   library that internally uses Python's `tempfile` module. Omitting this causes silent failures.\n\n"
            "### 2. FILE I/O CONFINEMENT\n"
            "   All file reads and writes MUST use `/app/generated_files/` as the base path.\n"
            "   NEVER write to `/tmp`, relative paths, or any other directory.\n"
            "   Example: `doc.save('/app/generated_files/report.docx')`\n\n"
            "### 3. MANDATORY VERIFICATION\n"
            "   After saving any file, you MUST verify it exists:\n"
            "   `import os; print(os.path.exists('/app/generated_files/my_file.docx'))`\n"
            "   If verification prints `False`, the file was not saved — correct and retry.\n\n"
            "### 4. IMPORTS\n"
            "   - Word docs : `from docx import Document`  (package: python-docx)\n"
            "   - Excel     : `import openpyxl` or `import pandas as pd`\n"
            "   - Plotting  : `import matplotlib.pyplot as plt`\n\n"
            "### 5. NO INTERACTIVE INPUT\n"
            "   `input()` is disabled. All values must be hardcoded or derived from context.\n\n"
            "### 6. ALWAYS PRINT FEEDBACK\n"
            "   Always `print()` a success message or result so execution output is visible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Valid Python code to execute in the sandbox. "
                        'MUST begin with `import tempfile; tempfile.tempdir = "/app/generated_files"` '
                        "if the script generates any files. "
                        "All file saves MUST target `/app/generated_files/`. "
                        "Do NOT wrap code in markdown backticks."
                    ),
                }
            },
            "required": ["code"],
        },
    },
}
