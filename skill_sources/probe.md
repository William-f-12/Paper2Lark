Run the Paper2Lark compatibility diagnostic when the user asks to test the plugin installation. This skill only verifies the local packaged runtime. M4 provides separate add, library and read/publish skills; probe does not exercise them. Use the doctor skill for configuration or Lark diagnosis.

Locate this skill's installed `SKILL.md` file. Resolve `../../scripts/paper2lark.py` relative to that file's containing directory. Run it with Python 3.11 or newer and the single argument `probe`, using a quoted absolute path. Never resolve the launcher against the user's working directory.

Report the returned JSON's runtime version, SHA-256 and success or error. Do not claim Lark authentication or paper workflows were checked. This command does not create files, connect to Lark, or request authorization.
