Task: reword each small daily task so it reads clearly and kindly, and write one short line of praise for finishing it.
The TASKS block is a JSON list of tasks you did not write yourself: copy their facts, never add any.

- Keep each task's "id" exactly as given. Return every id once.
- "title": under 90 characters, a plain instruction. "steps": 1 to 3 short steps. Keep any placeholder such as {names} or
  {n} exactly as written in the input; the app fills it in.
- "praise": one short, specific, warm sentence said after the task is done. No exclamation marks, no emoji, no hype.
  You may use only the placeholders {streak} (days in a row) and {week} (tasks done this week).
- Write no digits at all, no links, and no new names, numbers, dates, tools or claims.

Example:
```json
{"tasks": [{"id": "overdue-follow-up", "title": "Send a short follow-up to {names}", "steps": ["Open each application", "Write two lines asking for an update", "Set the next follow-up date"], "praise": "Those follow-ups are the ones that get replies, and you sent them."}]}
```
