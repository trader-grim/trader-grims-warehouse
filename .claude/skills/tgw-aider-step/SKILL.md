---
name: tgw-aider-step
description: Format a single Aider coding step for the TGW project. Use when the user says /tgw-aider-step or asks to prepare an Aider task. Writes a message-file and appends a log entry to ~/.local/share/aider-audit/usage.csv.
---

# TGW Aider Step

Format a single Aider step as a message-file and append a log entry to `~/.local/share/aider-audit/usage.csv`.

## Usage

Invoke with the step details:
> /tgw-aider-step task_id={task-id} model={model} files="{file1} {file2 ...}" step="""
> {step description}
> """

## Steps

1. Determine the output message file path:
   `~/.local/share/aider-audit/steps/{task_id}-{YYYYMMDD-HHMMSS}.md`

2. Create parent directories if they do not exist:
   `mkdir -p ~/.local/share/aider-audit/steps`

3. Write the message file containing only the step description — no extra framing or wrapper text. The file is the raw prompt aider will receive.

4. Ensure the audit CSV exists at `~/.local/share/aider-audit/usage.csv`; if not, create it with this header line:
   `timestamp,task_id,model,files,message_file`

5. Append one CSV row (wrap any field containing a comma in double-quotes):
   `{ISO datetime},{task_id},{model},"{files}",{message_file_path}`

6. Print the aider invocation command for the user to run — do not execute it:
   ```
   tgw-aider --model {model} --message-file {message_file_path} {files}
   ```

7. Never modify secrets, config files, or eBay OAuth scopes.
